"""Real response shapes, scoped fixture receipts and no financial promotion."""
import copy
import json
import tempfile
import unittest

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import (available_security_codes, data_request_contract, snapshot_record,
    validate_source_request, _record_current_acquisition, current_query_reference, current_snapshot_reference)
from researchhelper.index_sources import (OUTPUT, PROFILES, index_response, index_request_shapes,
    validate_index_request, callable_member_codes)
from researchhelper.object_discovery import research_sample_codes
from researchhelper.output_schema import planning_output_schema, _matches
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import evidence_contract, source_views, extractable_source_views

RETAINED = ROOT/'result/acceptance/ifind-index-pool-rd68mv8i/receipt.json'


class IndexConstituentSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = RETAINED.read_bytes()
        receipt = json.loads(cls.raw)
        row = next(row for row in receipt['results'] if row['parameters']['reportname'] == 'p03291')
        cls.params, cls.payload = row['parameters'], row['business_snapshot']

    @classmethod
    def tearDownClass(cls):
        if RETAINED.read_bytes() != cls.raw:
            raise AssertionError('Retained diagnostic changed')

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name),ROOT,offline=True,require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task = self.agent.create('核对上证50指数000016.SH在2026年9月30日的成分证券名单，不要求权重。',
            entry='指数',objects=['000016.SH'],research_mode='current')

    def source(self, *, receipt=False):
        record = snapshot_record(self.task,'ifind','data_pool',self.params,self.payload)
        if receipt:
            # Explicit in-memory transport fixture, never an online acceptance.
            event = dict(kind='data_requests',status='成功',batch=self.task['batch'],
                fingerprint=record['request_hash'],transport_isolated=True,network_request_started=True,network_worker_reaped=True)
            self.task['ledger']['dispatches'].append(event)
            _record_current_acquisition(self.task,record,event)
        self.task['source_records'].append(record)
        return record

    def test_real_fifty_rows_are_positions_and_names_not_weights_or_approval(self):
        result = index_response(self.task,self.params,self.payload)
        self.assertEqual((result['index_code'],result['observed_rows']),('000016.SH',50))
        self.assertTrue(result['row_count_matches_hint'])
        self.assertFalse(result['complete_set_verified'])
        self.assertFalse(result['weights_available'])
        self.assertFalse(result['numeric_evidence_registered'])
        self.assertEqual(result['members'][0],dict(row=0,code='600028.SH',name='中国石化',name_in_time='XD中国石',date='2026-09-30'))

    def test_request_schema_and_dispatch_share_one_exact_shape(self):
        self.assertEqual(validate_source_request(self.task,'ifind','data_pool',self.params),self.params)
        tool = next(tool for tool in data_request_contract(self.task)['tools'] if tool['operation']=='data_pool')
        self.assertEqual(tool['fixed_parameters'],dict(reportname='p03291',outputpara=OUTPUT))
        schema = planning_output_schema(self.task)['properties']['data_requests']['items']
        wire = dict(id='dr_members',source='ifind',operation='data_pool',parameters=self.params,
                    question_ids=['q1'],selection_reason='从明确登记的同日名单取数。')
        self.assertTrue(_matches(wire,schema))

    def test_registered_name_does_not_require_user_to_type_provider_block_id(self):
        task = copy.deepcopy(self.task)
        task['question'] = '核对上证50的成分证券'
        task['objects'] = []
        self.assertEqual(index_request_shapes(task)[0]['index_profiles'][0]['blockname'],'001005260')
        self.assertEqual(validate_index_request(task,self.params)[0],'000016.SH')

    def test_unknown_index_never_guesses_or_scans_a_block(self):
        task = copy.deepcopy(self.task)
        task.update(question='核对沪深300指数000300.SH成分',objects=['000300.SH'])
        self.assertEqual(index_request_shapes(task),[])
        with self.assertRaises(ContractError):
            validate_index_request(task,self.params)

    def test_old_and_boolean_policy_do_not_upgrade(self):
        for value in (None, True, 0, 2):
            task = copy.deepcopy(self.task)
            task['index_constituent_source_version'] = value
            self.assertEqual(index_request_shapes(task),[])
            self.assertEqual(callable_member_codes(task),[])
            with self.assertRaises(ContractError):
                validate_index_request(task,self.params)

    def test_wrong_report_extra_keys_and_undocumented_columns_rejected(self):
        for changes in (dict(reportname='p03425'),dict(outputpara=OUTPUT+',weight'),dict(extra='x')):
            params = dict(copy.deepcopy(self.params),**changes)
            with self.subTest(changes=changes),self.assertRaises(ContractError):
                validate_index_request(self.task,params)

    def test_impossible_and_future_dates_rejected_without_rewriting(self):
        for date in ('20260230','20990101','2026-09-30',True):
            params=copy.deepcopy(self.params)
            params['functionpara']['date']=date
            with self.subTest(date=date),self.assertRaises(ContractError):
                validate_index_request(self.task,params)

    def test_response_dates_and_types_must_match_actual_fields(self):
        for change in ('date','label','dtype','length','code','duplicate'):
            payload = copy.deepcopy(self.payload)
            columns = payload['tables'][0]['table']
            if change=='date':columns['p03291_f001'][0]='2026/10/01'
            elif change=='label':payload['outParams']['p03291_f002']='权重'
            elif change=='dtype':payload['descrs']['p03291_f002']['type']='DT_DOUBLE'
            elif change=='length':columns['p03291_f002'].pop()
            elif change=='code':columns['p03291_f002'][0]='600028'
            else:columns['p03291_f002'][1]=columns['p03291_f002'][0]
            with self.subTest(change=change),self.assertRaises(ContractError):
                index_response(self.task,self.params,payload)

    def test_unexpected_short_set_retained_but_not_called_complete(self):
        payload = copy.deepcopy(self.payload)
        for field in payload['tables'][0]['table']:
            payload['tables'][0]['table'][field]=payload['tables'][0]['table'][field][:3]
        result=index_response(self.task,self.params,payload)
        self.assertEqual(result['observed_rows'],3)
        self.assertFalse(result['row_count_matches_hint'])
        self.assertFalse(result['complete_set_verified'])

    def test_no_transport_receipt_means_no_member_lookup_authority(self):
        self.source()
        self.assertEqual(callable_member_codes(self.task),[])
        self.assertEqual(available_security_codes(self.task),['000016.SH'])

    def test_actual_shape_fixture_allows_lookup_but_not_default_research_sample(self):
        source=self.source(receipt=True)
        self.assertIsNotNone(current_query_reference(self.task,source))
        self.assertIsNone(current_snapshot_reference(self.task,source))
        self.assertEqual(len(callable_member_codes(self.task)),50)
        self.assertIn('600028.SH',available_security_codes(self.task))
        self.assertEqual(research_sample_codes(self.task),['000016.SH'])

    def test_cross_task_version_tampering_and_raw_changes_do_not_expand_authority(self):
        source=self.source(receipt=True)
        before=copy.deepcopy(source)
        for changes in (dict(task_id='foreign'),dict(research_version=2),dict(research_version=True),dict(hash='foreign')):
            source.clear();source.update(copy.deepcopy(before));source.update(changes)
            self.assertEqual(callable_member_codes(self.task),[])
        source.clear();source.update(copy.deepcopy(before))
        source['snapshot']['tables'][0]['table']['p03291_f001'][0]='2026/10/01'
        self.assertEqual(callable_member_codes(self.task),[])

    def test_raw_response_is_visible_but_not_an_extractable_financial_source(self):
        self.source(receipt=True)
        views=source_views(self.task)
        self.assertEqual(views[0]['raw_response'],self.payload)
        self.assertEqual(extractable_source_views(self.task),[])
        contract=evidence_contract(self.task,views)['sources'][0]
        self.assertTrue(contract['index_raw_only'])
        self.assertFalse(contract['numeric_evidence_registered'])

    def test_policy_is_bound_and_module_is_releasable_not_an_external_import(self):
        task=copy.deepcopy(self.task)
        task.pop('index_constituent_source_version')
        self.assertNotEqual(input_fingerprint(task),input_fingerprint(self.task))
        self.assertNotIn('index_constituent_source_version',self.agent.create_case('mechanism'))
        manifest=json.loads((ROOT/'packaging/package-source-map.json').read_text())
        self.assertIn('modules/evidencecollector/src/index_sources.py',manifest['files'])
