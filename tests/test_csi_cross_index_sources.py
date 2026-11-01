"""Retained real 500/1000 files through ordinary adapters, no live/model calls.

Transport and applicability decisions are authored controls. They prove data
contracts, not autonomous research or a historically available index universe.
"""
import base64
import copy
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.contracts import ContractError,charge,now,validate_evidence
from researchhelper.planning import input_fingerprint
from researchhelper.evidence import (EvidenceCollector,snapshot_record,_record_current_acquisition,
    candidate_code_references,direct_code_reference,validate_source_request,candidate_evidence,expand_api_cell_selections)
from researchhelper.index_sources import (PROFILE_POLICY,CSI_PROFILES,official_index_profiles,
    parse_csi_catalogue,parse_csi_workbook,callable_member_codes,verified_weight_source,weight_table_choices)
from researchhelper.output_schema import api_extraction_output_schema,decode_api_extraction_output
from researchhelper.reasoning import extractable_source_views,validate_model_specs
from researchhelper.data_requirements import pending
from researchhelper.analyzer import ResearchAnalyzer

ROOT=Path(__file__).resolve().parents[1]
CODES=('000905','000852')


def prepare_fixture():
    folder=ROOT/'data/diagnostics/csi-cross-index-20261023'
    return {code:{role:base64.b64encode((folder/code/name).read_bytes()).decode()
        for role,name in (('catalog','catalog.json'),('weights','weights.xls'),('members','members.xls'))}
        for code in CODES}


class CSICrossIndexSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path=os.environ.get('RESEARCHHELPER_CROSS_CSI_FIXTURE')
        fixture=json.loads(Path(path).read_text()) if path else prepare_fixture()
        cls.raw={code:{role:base64.b64decode(value,validate=True) for role,value in rows.items()}
            for code,rows in fixture.items()}

    def setUp(self):
        guard=patch('socket.socket.connect',side_effect=AssertionError('Offline controls only'))
        guard.start();self.addCleanup(guard.stop)
        folder=tempfile.TemporaryDirectory(prefix='rh-cross-index-');self.addCleanup(folder.cleanup)
        resources=Path(os.environ.get('RESEARCHHELPER_CROSS_CSI_RESOURCES',str(ROOT)))
        self.agent=ResearchAgent(ResearchArchive(folder.name),resources,offline=True,require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.collector=EvidenceCollector(offline=False)
        self.reset('000905')

    def reset(self,code):
        self.code=code;self.index=code+'.SH'
        count=CSI_PROFILES[self.index]['expected_rows']
        needs=['各成分百分比权重','对应成分代码与名称','权重观测日期与单位']
        constraints={text:dict(value_kind='number' if i==0 else 'text',
            unit_dimension='percentage' if i==0 else 'not_applicable',minimum_count=count if i<2 else 1,
            period_kind='point') for i,text in enumerate(needs)}
        self.task=self.agent.create('核对'+CSI_PROFILES[self.index]['name']+'当前官方成分权重，不作收益推断。',
            entry='指数',objects=[self.index],research_mode='current',questions=[dict(id='q1',
                question='核对当前官方成分权重',kind='fact',required=True,evidence_needs=needs,
                evidence_need_constraints=constraints,method_needs=[])])
        # Explicit authored questions keep legacy defaults. Opt this fixture
        # into the same typed weight contract as an ordinary new Chat task.
        self.task.update(index_weight_evidence_version=1,typed_evidence_needs_version=1,
            api_need_compatibility_version=2)
        self.task['input_hash']=input_fingerprint(self.task)

    def wire(self,task,selected,started,timeout,event,**kwargs):
        raw=self.raw[self.code][selected['role']]
        event.update(transport_isolated=True,network_request_started=True,network_worker_reaped=True,
            synthetic_test_transport=True)
        result=dict(original_base64=base64.b64encode(raw).decode(),original_sha256=hashlib.sha256(raw).hexdigest(),
            url=selected['url'],response_headers={})
        if selected['role']=='catalog':result['entries']=parse_csi_catalogue(raw,self.index)
        else:result['parsed']=parse_csi_workbook(raw,self.index,selected['role'],
            version=2 if selected['role']=='weights' else 1)
        return result

    def acquire(self,role='weights',as_of=None):
        with patch('researchhelper.source_transport.official_index',side_effect=self.wire):
            catalog=self.collector.external(self.task,'official','index_catalog',dict(index_code=self.index),{})
            self.task['source_records'].append(catalog)
            entry=next(x for x in catalog['snapshot']['entries'] if x['role']==role)
            source=self.collector.external(self.task,'official','index_file',
                dict(catalog_id=catalog['id'],entry_id=entry['id'],as_of=as_of),{})
            self.task['source_records'].append(source)
        return source

    def quote(self,code):
        params=dict(codes=code,indicators='close',startdate='2026-10-21',enddate='2026-10-21',
            functionpara=dict(Currency='RMB',CPS='1',Fill='Omit'))
        source=snapshot_record(self.task,'ifind','cmd_history_quotation',params,
            dict(errorcode=0,tables=[dict(thscode=code,time=['2026-10-21'],table=dict(close=[10.0]))]))
        charge(self.task['ledger'],'data_requests')
        event=dict(kind='data_requests',source='ifind',operation='cmd_history_quotation',status='成功',
            batch=self.task['batch'],fingerprint=source['request_hash'],time=now(),transport_isolated=True,
            network_request_started=True,network_worker_reaped=True,synthetic_test_transport=True)
        self.task['ledger']['dispatches'].append(event);_record_current_acquisition(self.task,source,event)
        self.task['source_records'].append(source)
        return source

    def test_codes_and_names_offer_only_declared_index_profiles(self):
        for code in CODES:
            self.reset(code)
            self.assertEqual(set(official_index_profiles(self.task)),{self.index})
            task=copy.deepcopy(self.task);task['objects']=[]
            self.assertEqual(set(official_index_profiles(task)),{self.index})
        self.assertEqual(self.task[PROFILE_POLICY],2)

    def test_legacy_tasks_keep_the_original_profile_set(self):
        self.task.pop(PROFILE_POLICY)
        self.assertEqual(official_index_profiles(self.task),{})
        self.task.update(question='核对沪深300',objects=['000300.SH'])
        self.assertEqual(set(official_index_profiles(self.task)),{'000300.SH'})

    def test_unknown_versions_are_not_implicit_opt_in(self):
        for value in (True,1,3,'2'):
            self.task[PROFILE_POLICY]=value
            with self.assertRaises(ContractError):official_index_profiles(self.task)

    def test_profile_output_does_not_mutate_the_registry(self):
        value=official_index_profiles(self.task)
        value[self.index]['aliases'].append('unrelated')
        self.assertNotIn('unrelated',CSI_PROFILES[self.index]['aliases'])

    def test_real_weight_files_preserve_dates_values_and_non_normalized_sum(self):
        for code,total,count in (('000905','100.000',500),('000852','100.012',1000)):
            self.reset(code);source=self.acquire();parsed=verified_weight_source(self.task,source)
            self.assertEqual((parsed['row_count'],parsed['available_weight_count']),(count,count))
            self.assertEqual(parsed['weight_sum'],total)
            self.assertEqual(parsed['observation_date'],'2026-10-19')
            self.assertEqual(self.task['ledger']['used']['data_requests'],2)
            self.assertFalse(parsed['financial_approval'] or parsed['historical_availability_verified'])

    def test_member_file_date_is_not_relabelled_to_weight_date(self):
        for code,count in (('000905',500),('000852',1000)):
            self.reset(code);source=self.acquire('members')
            self.assertEqual(source['snapshot']['parsed']['observation_date'],'2026-10-22')
            self.assertEqual(len(callable_member_codes(self.task)),count)

    def test_wrong_asof_keeps_the_original_and_no_retry(self):
        source=self.acquire(as_of='2026-10-22')
        self.assertNotEqual(source['status'],'成功待核验')
        self.assertEqual(source['snapshot']['parsed']['observation_date'],'2026-10-19')
        self.assertEqual(callable_member_codes(self.task),[])

    def test_foreign_index_binary_is_rejected_not_recoded(self):
        with self.assertRaises(ContractError):
            parse_csi_workbook(self.raw['000852']['weights'],'000905.SH','weights',version=2)

    def test_selected_member_can_request_ifind_quote_with_exact_parent_reference(self):
        source=self.acquire();member=verified_weight_source(self.task,source)['members'][0]
        quote=self.quote(member['code'])
        self.assertEqual(validate_source_request(self.task,'ifind','cmd_history_quotation',quote['parameters']),quote['parameters'])
        refs=candidate_code_references(self.task,quote)
        self.assertEqual(len(refs),1)
        self.assertEqual(refs[0]['discovery']['operation'],'index_file')
        self.assertEqual(refs[0]['discovery']['row_locator'],dict(excel_row=member['excel_row']))
        self.assertIsNone(direct_code_reference(self.task,quote))

    def test_later_discovery_does_not_retroactively_authorize_old_quote(self):
        member=parse_csi_workbook(self.raw[self.code]['weights'],self.index,'weights',version=2)['members'][0]
        quote=self.quote(member['code']);self.acquire()
        self.assertEqual(candidate_code_references(self.task,quote),[])

    def test_changed_original_or_foreign_task_cannot_authorize_member_lookups(self):
        source=self.acquire();self.assertEqual(len(callable_member_codes(self.task)),500)
        source['snapshot']['parsed']['members'][0]['code']='600519.SH'
        self.assertEqual(callable_member_codes(self.task),[])
        self.task['id']='foreign'
        self.assertEqual(callable_member_codes(self.task),[])

    def test_old_and_historical_tasks_do_not_inherit_new_official_lookup_scope(self):
        self.acquire();self.task.pop(PROFILE_POLICY)
        self.assertEqual(callable_member_codes(self.task),[])
        self.task[PROFILE_POLICY]=2;self.task['research_mode']='historical'
        self.assertEqual(callable_member_codes(self.task),[])

    def test_unrelated_security_is_not_permitted_by_an_index_download(self):
        self.acquire()
        with self.assertRaises(ContractError):
            validate_source_request(self.task,'ifind','cmd_history_quotation',dict(codes='AAPL.US',indicators='close',
                startdate='2026-10-21',enddate='2026-10-21',functionpara=dict(Currency='RMB',CPS='1',Fill='Omit')))

    def test_policy_is_in_the_input_fingerprint(self):
        before=input_fingerprint(self.task);self.task.pop(PROFILE_POLICY)
        self.assertNotEqual(input_fingerprint(self.task),before)

    def test_whole_table_selection_adopts_every_original_weight_without_filling(self):
        for code,count in (('000905',500),('000852',1000)):
            self.reset(code);source=self.acquire();views=extractable_source_views(self.task)
            choice=weight_table_choices(self.task,views)[0]
            wire=dict(version=4,evidence_specs=[dict(index_weight_table_selection=choice['reference'],review=dict(
                need_links=[dict(question_id='q1',need_ids=['n1','n2','n3'])],allowed_natures=['已实现事实'],
                conditions=['仅当前取得原件及其实际日期，不证明历史当时可得、基金持仓或收益贡献。'],
                reason='离线人为适用性控制，逐行保留原值，不代表模型验收。'))])
            schema=api_extraction_output_schema(self.task,views)
            selected=decode_api_extraction_output(wire,schema,context=(self.task,views))['evidence_specs']
            self.assertEqual(len(selected),1)
            specs=expand_api_cell_selections(self.task,selected)
            validate_model_specs(self.task,specs)
            self.assertEqual(len(specs),count)
            adopted=[]
            for spec in specs:
                item=candidate_evidence(self.task,source,spec)
                ResearchAnalyzer().assess(self.task,item,spec['review'],self.task['source_records'])
                validate_evidence(item,self.task);adopted.append(item);self.task['evidence'].append(item)
            self.assertEqual(sum(Decimal(str(e['value'])) for e in adopted),Decimal(choice['weight_sum']))
            self.assertTrue(all(e['period']=='2026-10-19' and e['unit']=='%' for e in adopted))
            self.assertEqual(pending(self.task),[])
            self.assertEqual(self.task['ledger']['used']['model_calls'],0)
