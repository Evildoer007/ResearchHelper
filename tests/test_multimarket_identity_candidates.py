"""Current identity candidate protocol, transport fixtures only, no network.

Observed vendor shape: one thscode cell can contain several comma-separated
market codes and the candidate response omits the isexact echo. Those rows
remain candidates; an explicit exact-code lookup is still necessary.
"""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError,fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import (IDENTITY_EXACT_PROFILE,IDENTITY_CANDIDATE_PROFILE,
    identity_query_parameters,identity_candidates,identity_resolutions,available_security_codes,
    snapshot_record,_record_current_acquisition,validate_source_request,data_request_contract)
from researchhelper.output_schema import planning_output_schema,decode_planning_output
from researchhelper.planning import input_fingerprint,question_specs


class MultimarketIdentityCandidateTests(unittest.TestCase):
    """Version1 keeps its old exact-lookup-only authority after version2 ships."""
    def setUp(self):
        private=tempfile.TemporaryDirectory();self.addCleanup(private.cleanup)
        self.agent=ResearchAgent(ResearchArchive(Path(private.name)),ROOT,offline=True,require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task=self.agent.create('核对宁德时代和腾讯控股的上市市场及行情',research_mode='current')
        self.task['identity_candidate_lookup_version']=1
        self.task['input_hash']=input_fingerprint(self.task)
        guard=patch('socket.socket.connect',side_effect=AssertionError('Offline identity fixture'))
        guard.start();self.addCleanup(guard.stop)

    def retain_candidates(self,query='腾讯控股',codes='0700.HK,TCTZF.PQ',**change):
        params=identity_query_parameters('secname',query,IDENTITY_CANDIDATE_PROFILE)
        payload=dict(errorcode=0,inputParams=dict(mode='secname'),
            tables=[dict(secname=query,table=dict(thscode=[codes]))])
        payload.update(change)
        source=snapshot_record(self.task,'ifind','get_thscode',params,payload)
        event=dict(kind='data_requests',status='成功',batch=self.task['batch'],fingerprint=source['request_hash'],
            transport_isolated=True,network_request_started=True,network_worker_reaped=True)
        self.task['ledger']['dispatches'].append(event)
        _record_current_acquisition(self.task,source,event)
        self.task['source_records'].append(source)
        return source

    def exact(self,query,code):
        params=identity_query_parameters('seccode',query)
        source=snapshot_record(self.task,'ifind','get_thscode',params,dict(errorcode=0,
            inputParams=dict(mode='seccode',isexact=1),tables=[dict(seccode=query,table=dict(thscode=[code]))]))
        self.task['source_records'].append(source)
        return source

    def test_foreign_listing_not_filtered_into_a_unique_identity(self):
        self.retain_candidates()
        candidate=identity_candidates(self.task)[0]
        self.assertEqual(candidate['codes'],['0700.HK','TCTZF.PQ'])
        self.assertEqual(candidate['unsupported_codes'],['TCTZF.PQ'])
        self.assertFalse(candidate['identity_approved']);self.assertFalse(candidate['market_selected'])
        self.assertEqual(identity_resolutions(self.task)[0]['status'],'候选待确定')
        self.assertEqual(available_security_codes(self.task),[])

    def test_candidates_authorize_only_exact_original_numbers_not_quotes(self):
        self.retain_candidates()
        self.assertEqual(validate_source_request(self.task,'ifind','get_thscode',identity_query_parameters('seccode','0700')),
            identity_query_parameters('seccode','0700'))
        for guessed in ('00700','700','TCTZF'):
            with self.assertRaises(ContractError):
                validate_source_request(self.task,'ifind','get_thscode',identity_query_parameters('seccode',guessed))
        with self.assertRaises(ContractError):
            validate_source_request(self.task,'ifind','cmd_history_quotation',dict(codes='0700.HK',indicators='close',
                startdate='2026-10-12',enddate='2026-10-16',functionpara=dict(Currency='RMB',CPS='1',Fill='Omit')))

    def test_explicit_exact_response_enables_only_selected_market(self):
        self.retain_candidates('宁德时代','300750.SZ,3750.HK,CYATY.PQ')
        self.assertEqual(available_security_codes(self.task),[])
        self.exact('300750','300750.SZ')
        self.assertEqual(available_security_codes(self.task),['300750.SZ'])
        self.assertEqual(self.task['objects'],[])

    def test_two_supported_markets_remain_two_without_default_selection(self):
        self.retain_candidates('宁德时代','300750.SZ,3750.HK,CYATY.PQ')
        self.assertEqual(identity_candidates(self.task)[0]['exact_lookup_codes'],['300750.SZ','3750.HK'])
        self.assertEqual(available_security_codes(self.task),[])

    def test_unrelated_names_and_fuzzy_code_queries_are_rejected(self):
        with self.assertRaises(ContractError):
            validate_source_request(self.task,'ifind','get_thscode',identity_query_parameters('secname','其他公司',IDENTITY_CANDIDATE_PROFILE))
        with self.assertRaises(ContractError):identity_query_parameters('seccode','0700',IDENTITY_CANDIDATE_PROFILE)

    def test_old_tasks_and_boolean_policy_cannot_use_candidates(self):
        for value in (None,True,3):
            old=copy.deepcopy(self.task)
            if value is None:old.pop('identity_candidate_lookup_version')
            else:old['identity_candidate_lookup_version']=value
            old['input_hash']=input_fingerprint(old)
            with self.assertRaises(ContractError):
                validate_source_request(old,'ifind','get_thscode',identity_query_parameters('secname','腾讯控股',IDENTITY_CANDIDATE_PROFILE))
            self.assertEqual(identity_candidates(old),[])

    def test_tampered_cross_task_and_unmetered_sources_never_open_scope(self):
        for mutation in ('snapshot','id','origin','task','event'):
            self.task['source_records']=[];self.task['ledger']['dispatches']=[]
            source=self.retain_candidates()
            if mutation=='snapshot':source['snapshot']['tables'][0]['table']['thscode']=['600000.SH']
            if mutation=='id':source['id']='FORGED_ID'
            if mutation=='origin':source['identity_origin']['input_hash']='0'*64
            if mutation=='task':source['task_id']='OTHER_TASK'
            if mutation=='event':self.task['ledger']['dispatches'][0]['network_worker_reaped']=False
            self.assertEqual(identity_candidates(self.task),[],mutation)

    def test_malformed_or_wrong_echo_responses_do_not_produce_candidates(self):
        for value,change in (('0700.HK,',{}),(' 0700.HK',{}),('0700.HK',dict(inputParams=dict(mode='seccode'))),
                ('0700.HK',dict(inputParams=dict(mode='secname',isexact=1)))):
            self.task['source_records']=[];self.task['ledger']['dispatches']=[]
            self.retain_candidates(codes=value,**change)
            self.assertEqual(identity_candidates(self.task),[])

    def test_candidate_policy_is_input_bound(self):
        self.retain_candidates()
        old_hash=self.task['input_hash'];self.task['identity_candidate_lookup_version']=2
        self.assertNotEqual(input_fingerprint(self.task),old_hash)
        self.assertEqual(identity_candidates(self.task),[])

    def test_current_short_plan_can_select_candidate_profile_and_legacy_cannot(self):
        schema=planning_output_schema(self.task)
        request=dict(id='name_candidates',source='ifind',operation='get_thscode',
            parameters=dict(query_mode='secname',query_value='腾讯控股',profile_id=IDENTITY_CANDIDATE_PROFILE),
            question_ids=['q1'],selection_reason='只取用户原名称的全部市场候选。')
        questions=question_specs(self.task)
        wire=dict(version=3,main_question=questions[0],additional_questions=[],data_requests=[request],calculations=[])
        decoded=decode_planning_output(wire,schema)
        self.assertEqual(decoded['data_requests'][0]['parameters'],identity_query_parameters('secname','腾讯控股',IDENTITY_CANDIDATE_PROFILE))
        old=copy.deepcopy(self.task);old.pop('identity_candidate_lookup_version');old['input_hash']=input_fingerprint(old)
        with self.assertRaises(ContractError):decode_planning_output(wire,planning_output_schema(old))

    def test_exact_profile_does_not_implicitly_select_a_name_or_market(self):
        tool=next(row for row in data_request_contract(self.task)['tools'] if row['operation']=='get_thscode')
        self.assertEqual(tool['query_modes'],['seccode'])
        self.assertEqual(tool['candidate_profile']['query_modes'],['secname'])
        self.assertEqual(tool['candidate_profile']['retained_candidates'],[])

    def test_short_exact_selection_cannot_add_or_drop_zeroes(self):
        self.retain_candidates()
        schema=planning_output_schema(self.task)
        questions=question_specs(self.task)
        for value in ('0700','00700','700'):
            request=dict(id='exact_selected_code',source='ifind',operation='get_thscode',
                parameters=dict(query_mode='seccode',query_value=value,profile_id=IDENTITY_EXACT_PROFILE),
                question_ids=['q1'],selection_reason='精确核对实际港股候选号码。')
            wire=dict(version=3,main_question=questions[0],additional_questions=[],data_requests=[request],calculations=[])
            if value=='0700':
                self.assertEqual(decode_planning_output(wire,schema)['data_requests'][0]['parameters'],identity_query_parameters('seccode','0700'))
            else:
                with self.assertRaises(ContractError):decode_planning_output(wire,schema)
