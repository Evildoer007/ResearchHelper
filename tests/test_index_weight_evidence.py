"""Index weights: explicit whole-source selection, scalar gates, no live calls.

Uses immutable downloaded XLS bytes and synthetic transport receipts in a new
private task. These tests do not claim a new provider or real model acceptance.
"""
import base64
import copy
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import xlrd
from tests import ROOT
from tests.test_csi_official_source import CATALOGUE,FILE
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.contracts import ContractError,validate_evidence
from researchhelper.data_requirements import pending
from researchhelper.evidence import (EvidenceCollector,candidate_evidence,expand_api_cell_selections,
    current_snapshot_reference,FIELD_REGISTRY)
from researchhelper.index_sources import (parse_csi_catalogue,parse_csi_workbook,verified_weight_source,
    weight_table_choices)
from researchhelper.output_schema import api_extraction_output_schema,decode_api_extraction_output
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import extractable_source_views,validate_model_specs


class IndexWeightEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalogue=CATALOGUE.read_bytes();cls.binary=FILE.read_bytes()
        cls.protected={CATALOGUE:hashlib.sha256(cls.catalogue).hexdigest(),FILE:hashlib.sha256(cls.binary).hexdigest()}
        book=xlrd.open_workbook(file_contents=cls.binary);sheet=book.sheet_by_index(0)
        cls.rows=[sheet.row_values(i) for i in range(sheet.nrows)]
        cls.types=[list(sheet.row_types(i)) for i in range(sheet.nrows)];cls.sheet_name=sheet.name

    def setUp(self):
        guard=patch('socket.socket.connect',side_effect=AssertionError('Offline fixture only'))
        guard.start();self.addCleanup(guard.stop)
        private=tempfile.TemporaryDirectory(dir=ROOT/'result/acceptance');self.addCleanup(private.cleanup)
        self.agent=ResearchAgent(ResearchArchive(private.name),ROOT,offline=True,require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        needs=['各成分百分比权重','对应成分代码与名称','权重观测日期与单位']
        constraints={text:dict(value_kind='number' if i==0 else 'text',unit_dimension='percentage' if i==0 else 'not_applicable',
            minimum_count=300 if i<2 else 1,period_kind='point') for i,text in enumerate(needs)}
        self.task=self.agent.create('核对沪深300在2026年10月19日的成分及权重',entry='指数',objects=['000300.SH'],
            research_mode='current',questions=[dict(id='q1',question='核对成分权重',kind='fact',required=True,
                evidence_needs=needs,evidence_need_constraints=constraints,method_needs=[])])
        self.task.update(index_weight_evidence_version=1,typed_evidence_needs_version=1,api_need_compatibility_version=2)
        self.task['input_hash']=input_fingerprint(self.task)
        self.collector=EvidenceCollector(offline=False)

    def tearDown(self):
        self.assertTrue(all(hashlib.sha256(path.read_bytes()).hexdigest()==value for path,value in self.protected.items()))

    def wire(self,task,selected,started,timeout,event,**kwargs):
        raw=self.catalogue if selected['role']=='catalog' else self.binary
        event.update(transport_isolated=True,network_request_started=True,network_worker_reaped=True)
        result=dict(original_base64=base64.b64encode(raw).decode(),original_sha256=hashlib.sha256(raw).hexdigest(),
                    url=selected['url'],response_headers={'Content-Type':'application/vnd.ms-excel'})
        if selected['role']=='catalog':result['entries']=parse_csi_catalogue(raw,selected['index_code'])
        else:result['parsed']=parse_csi_workbook(raw,selected['index_code'],selected['role'],version=2)
        return result

    def acquire(self):
        with patch('researchhelper.source_transport.official_index',side_effect=self.wire):
            catalogue=self.collector.external(self.task,'official','index_catalog',dict(index_code='000300.SH'),{})
            self.task['source_records'].append(catalogue)
            selected=next(e for e in catalogue['snapshot']['entries'] if e['role']=='weights')
            record=self.collector.external(self.task,'official','index_file',dict(catalog_id=catalogue['id'],
                entry_id=selected['id'],as_of='2026-10-19'),{})
            self.task['source_records'].append(record)
        return record

    def selection(self):
        views=extractable_source_views(self.task);self.assertEqual(len(views),1)
        schema=api_extraction_output_schema(self.task,views)
        choice=weight_table_choices(self.task,views)[0]
        wire=dict(version=4,evidence_specs=[dict(index_weight_table_selection=choice['reference'],review=dict(
            need_links=[dict(question_id='q1',need_ids=['n1','n2','n3'])],allowed_natures=['已实现事实'],
            conditions=['仅该原件当前版本与实际日期，不证明历史当时可得、基金持仓或收益贡献。'],
            reason='逐行核对同一官方权重原件，不归一化、不补值。'))])
        return decode_api_extraction_output(wire,schema,context=(self.task,views))['evidence_specs']

    def test_real_original_preserves_all_values_leading_zero_and_unmodified_sum(self):
        record=self.acquire();parsed=verified_weight_source(self.task,record)
        self.assertEqual(parsed['row_count'],300);self.assertEqual(parsed['available_weight_count'],300)
        self.assertEqual(parsed['weight_sum'],'100.001')
        self.assertEqual(parsed['members'][0]['code'],'000001.SZ')
        self.assertEqual(current_snapshot_reference(self.task,record)['kind'],'current_snapshot')
        self.assertEqual(self.task['ledger']['used']['data_requests'],2)
        self.assertEqual(self.task['ledger']['used']['model_calls'],0)

    def test_whole_table_selection_passes_the_same_scalar_gates_for_300_members(self):
        record=self.acquire();selected=self.selection();self.assertEqual(len(selected),1)
        specs=expand_api_cell_selections(self.task,selected);self.assertEqual(len(specs),300)
        validate_model_specs(self.task,specs)
        for spec,row in zip(specs,self.rows[1:]):
            item=candidate_evidence(self.task,record,spec)
            self.assertEqual(item['value'],row[9]);self.assertEqual(item['unit'],'%')
            ResearchAnalyzer().assess(self.task,item,spec['review'],self.task['source_records'])
            validate_evidence(item,self.task);self.task['evidence'].append(item)
        self.assertEqual(len(self.task['evidence']),300)
        self.assertEqual(pending(self.task),[])
        self.assertEqual(len({e['identity']['member_code'] for e in self.task['evidence']}),300)

    def test_wrong_value_bool_unit_identity_date_and_duplicate_table_are_rejected(self):
        record=self.acquire();selected=self.selection();spec=expand_api_cell_selections(self.task,selected)[0]
        for changes in (dict(value=True),dict(unit='点'),dict(value=99),dict(period='2026-10-20'),dict(object='000016.SH'),
                        dict(identity=dict(spec['identity'],member_code='600000.SH'))):
            with self.subTest(changes=changes),self.assertRaises(ContractError):
                candidate_evidence(self.task,record,dict(spec,**changes))
        with self.assertRaises(ContractError):expand_api_cell_selections(self.task,selected*2)
        for change in (dict(id='foreign'),dict(version=self.task['version']+1),dict(research_mode='historical')):
            changed=copy.deepcopy(self.task);changed.update(change)
            with self.assertRaises(ContractError):expand_api_cell_selections(changed,selected)

    def test_old_raw_record_cannot_gain_numeric_qualification(self):
        record=self.acquire();old=copy.deepcopy(self.task);old.pop('index_weight_evidence_version')
        self.assertIsNone(current_snapshot_reference(old,record))
        self.assertEqual(extractable_source_views(old),[])
        self.assertIn('weight',FIELD_REGISTRY['official.index_file']['fields'])

    def test_partial_rows_and_missing_weights_are_retained_not_filled_or_normalized(self):
        for kind in ('missing_member','missing_weight'):
            rows=copy.deepcopy(self.rows);types=copy.deepcopy(self.types)
            if kind=='missing_member':rows.pop();types.pop()
            else:rows[2][9]='';types[2][9]=xlrd.XL_CELL_EMPTY
            sheet=SimpleNamespace(name=self.sheet_name,nrows=len(rows),ncols=len(rows[0]),
                row_values=lambda i:rows[i],cell_type=lambda r,c:types[r][c])
            book=SimpleNamespace(nsheets=1,sheet_by_index=lambda _:sheet,release_resources=lambda:None)
            with patch('xlrd.open_workbook',return_value=book):
                result=parse_csi_workbook(self.binary,'000300.SH','weights',version=2)
            self.assertEqual(result['available_weight_count'],299)
            self.assertFalse(result['independent_membership_verified'])
            if kind=='missing_member':self.assertFalse(result['row_count_matches_hint'])
            else:
                self.assertIsNone(result['members'][1]['weight'])
                self.assertEqual(result['raw_rows'][1][9],'')
                self.assertFalse(result['weight_sum_is_complete'])


if __name__=='__main__':unittest.main()
