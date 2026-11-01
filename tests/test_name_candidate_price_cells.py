"""Synthetic transport fixtures for candidate-linked current price selection.

No provider, model, issuer identity, historical availability or investment
verdict is approved by these tests. Ordinary source/applicability gates run.
"""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from tests import test_multimarket_identity_candidates as fixtures
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.contracts import ContractError,validate_evidence
from researchhelper.evidence import (snapshot_record,_record_current_acquisition,api_cell_choices,
    candidate_code_references,expand_api_cell_selections,candidate_evidence,CANDIDATE_CODE_LIMITATION,validate_provenance)
from researchhelper.output_schema import api_extraction_output_schema,decode_api_extraction_output
from researchhelper.reasoning import extractable_source_views
from researchhelper.planning import input_fingerprint


class NameCandidatePriceCellTests(unittest.TestCase):
    retain_candidates=fixtures.MultimarketIdentityCandidateTests.retain_candidates

    def setUp(self):
        private=tempfile.TemporaryDirectory();self.addCleanup(private.cleanup)
        self.agent=ResearchAgent(ResearchArchive(Path(private.name)/'store'),ROOT,offline=True,require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task=self.agent.create('核对港股腾讯控股的收盘价，仅当前取得版本。',research_mode='current',
            questions=[dict(id='q1',question='当前取得的代码行情是什么？',kind='fact',
                evidence_needs=['2026-10-12收盘价','2026-10-13收盘价'])])
        self.parent=self.retain_candidates()
        params=dict(codes='0700.HK',indicators='close',startdate='2026-10-12',enddate='2026-10-13',
            functionpara=dict(Currency='RMB',CPS='1',Fill='Omit'))
        self.quote=snapshot_record(self.task,'ifind','cmd_history_quotation',params,dict(errorcode=0,
            tables=[dict(thscode='0700.HK',time=['2026-10-12','2026-10-13'],table=dict(close=[100.0,101.0]))]))
        event=dict(kind='data_requests',status='成功',batch=self.task['batch'],fingerprint=self.quote['request_hash'],
            transport_isolated=True,network_request_started=True,network_worker_reaped=True)
        self.task['ledger']['dispatches'].append(event);_record_current_acquisition(self.task,self.quote,event)
        self.task['source_records'].append(self.quote)
        guard=patch('socket.socket.connect',side_effect=AssertionError('Offline candidate price fixture'))
        guard.start();self.addCleanup(guard.stop)

    def specifications(self):
        result=[]
        for cell,need in zip(api_cell_choices(self.task,self.quote),self.task['questions'][0]['evidence_needs']):
            result.append(dict(api_cell_selections=[cell['candidate_id']],question_ids=['q1'],
                review=dict(question_ids=['q1'],allowed_natures=['已实现事实'],conditions=['仅当前取得代码行情'],
                    reason='核对本行收盘价，不确认公司身份或历史可得性。',
                    evidence_need_texts=[dict(question_id='q1',texts=[need])])))
        return result

    def test_strict_protocol_lists_both_actual_cells_without_numeric_generation(self):
        schema=api_extraction_output_schema(self.task,extractable_source_views(self.task))
        self.assertIsNotNone(schema);self.assertEqual(schema['properties']['version']['enum'],[2])
        cells=api_cell_choices(self.task,self.quote)
        self.assertEqual([cell['value'] for cell in cells],[100.0,101.0])
        self.assertTrue(all(not cell['source_approved'] for cell in cells))
        self.assertTrue(all(cell['candidate_code_reference']['discovery']['operation']=='get_thscode' for cell in cells))
        decoded=decode_api_extraction_output(dict(version=2,evidence_specs=self.specifications()),schema)
        self.assertNotIn('value',decoded['evidence_specs'][0])

    def test_selected_rows_still_pass_source_and_financial_gates_separately(self):
        schema=api_extraction_output_schema(self.task,extractable_source_views(self.task))
        decoded=decode_api_extraction_output(dict(version=2,evidence_specs=self.specifications()),schema)
        specs=expand_api_cell_selections(self.task,decoded['evidence_specs'])
        self.assertEqual(len(specs),2)
        for spec in specs:
            self.assertNotIn('direct_code_reference',spec)
            self.assertIn('candidate_code_reference',spec)
            item=candidate_evidence(self.task,self.quote,spec)
            self.assertEqual(item['identity']['mode'],'candidate_provider_observation')
            self.assertIsNone(item['published_at']);self.assertNotIn('valid_from',item['identity'])
            self.assertNotEqual(item['applicability_check'],'通过')
            ResearchAnalyzer().assess(self.task,item,spec['review'],self.task['source_records'])
            validate_evidence(item,self.task)
            self.assertIn(CANDIDATE_CODE_LIMITATION,item['applicability_review']['conditions'])
            self.task['evidence'].append(item)
        self.assertEqual([item['value'] for item in self.task['evidence']],[100.0,101.0])
        self.agent.archive.save(self.task)
        restored=self.agent.archive.get(self.task['id'])
        validate_provenance(restored['evidence'][0],restored)

    def test_old_or_invalid_policy_cannot_upgrade_closed_data(self):
        for value in (None,True,0,2):
            task=copy.deepcopy(self.task)
            if value is None:task.pop('name_candidate_observation_version')
            else:task['name_candidate_observation_version']=value
            task['input_hash']=input_fingerprint(task)
            self.assertEqual(api_cell_choices(task,task['source_records'][-1]),[])
            self.assertEqual(candidate_code_references(task,task['source_records'][-1]),[])

    def test_mixed_or_rewritten_identity_is_rejected(self):
        spec=expand_api_cell_selections(self.task,self.specifications())[0]
        for extra in (dict(direct_code_reference={}),dict(identity={}),dict(object='腾讯控股')):
            with self.assertRaises(ContractError):candidate_evidence(self.task,self.quote,dict(spec,**extra))

    def test_changed_parent_quote_or_order_loses_selection(self):
        for part in ('parent','quote','order'):
            task=copy.deepcopy(self.task)
            if part=='parent':task['source_records'][0]['snapshot']['tables'][0]['table']['thscode']=['00700.HK']
            if part=='quote':task['source_records'][1]['snapshot']['tables'][0]['table']['close'][0]=999
            if part=='order':task['source_records'][0]['acquisition']['dispatch_index']=1
            self.assertEqual(api_cell_choices(task,task['source_records'][-1]),[],part)

    def test_numeric_or_approval_injection_is_not_a_short_selection(self):
        schema=api_extraction_output_schema(self.task,extractable_source_views(self.task))
        for key,value in (('value',999),('identity',{}),('source_check','通过')):
            specs=self.specifications();specs[0][key]=value
            with self.assertRaises(ContractError):decode_api_extraction_output(dict(version=2,evidence_specs=specs),schema)
