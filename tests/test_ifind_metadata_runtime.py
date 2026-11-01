"""Real retained metadata through ordinary adapters; synthetic transport only."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.evidence import EvidenceCollector, current_query_reference, data_request_contract, validate_source_request
from researchhelper.financial_sources import (IFIND_METADATA_POLICY, IFIND_METADATA_REGISTRY,
    basic_financial_definitions, metadata_request_shapes, metadata_wait_seconds)
from researchhelper.output_schema import _matches, _planned_request_schemas
from researchhelper.planning import input_fingerprint

ROOT = Path(__file__).resolve().parents[1]


def prepare_fixture():
    metadata = ROOT/'result/acceptance/ifind-financial-metadata-20261023'
    basic = ROOT/'result/acceptance/ifind-financial-basic-20261023'
    proof = json.loads((metadata/'receipt.json').read_text())
    return dict(policy=json.loads((metadata/'2-get_limit_policy.json').read_text()),
        indicators=json.loads((metadata/'3-get_indicators.json').read_text()),
        parameters={selection['indicator']: json.loads((metadata/event['file'].split('/')[-1]).read_text())
            for selection, event in zip(proof['selections'], proof['requests'][3:])},
        request=json.loads((basic/'request.json').read_text()),
        basic=json.loads((basic/'raw-response.json').read_text()))


class IFindMetadataRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.environ.get('RESEARCHHELPER_IFIND_METADATA_FIXTURE')
        cls.fixture = json.loads(Path(path).read_text()) if path else prepare_fixture()

    def setUp(self):
        guard = patch('socket.socket.connect', side_effect=AssertionError('Offline test only'))
        guard.start(); self.addCleanup(guard.stop)
        folder = tempfile.TemporaryDirectory(prefix='rh-ifind-metadata-'); self.addCleanup(folder.cleanup)
        resources = Path(os.environ.get('RESEARCHHELPER_IFIND_METADATA_RESOURCES', str(ROOT)))
        self.agent = ResearchAgent(ResearchArchive(folder.name), resources, offline=True, require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task = self.agent.create('核对这些候选公司的2026年半年报营业收入与归母净利润，保留原值及未核实口径。',
            entry='行业', objects=self.fixture['request']['codes'].split(','), research_mode='current')
        self.collector = EvidenceCollector(offline=False)
        self.calls = []
        hook = patch.object(self.collector, '_http_json', side_effect=self.wire)
        hook.start(); self.addCleanup(hook.stop)

    def wire(self, task, request, timeout, event):
        operation = urlsplit(request.full_url).path.rsplit('/', 1)[-1]
        query = parse_qs(urlsplit(request.full_url).query)
        self.calls.append(dict(operation=operation, method=request.get_method(), query=query, body=request.data))
        event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True,
            synthetic_test_transport=True)
        if operation == 'get_limit_policy':
            return copy.deepcopy(self.fixture['policy'])
        if operation == 'get_indicators':
            return copy.deepcopy(self.fixture['indicators'])
        if operation == 'get_indicator_params':
            return copy.deepcopy(self.fixture['parameters'][query['indicator'][0]])
        if operation == 'basic_data_service':
            self.assertEqual(json.loads(request.data), self.fixture['request'])
            return copy.deepcopy(self.fixture['basic'])
        raise AssertionError('Unregistered test operation')

    def collect(self, operation, params):
        record = self.collector.external(self.task, 'ifind', operation, params,
            dict(access_token='synthetic_metadata_control_token'))
        self.task['source_records'].append(record)
        return record

    def catalogue(self):
        self.collect('get_limit_policy', {})
        return self.collect('get_indicators', dict(indicators=['营业收入', '归属于母公司所有者的净利润'], market='A股'))

    def definitions(self):
        self.catalogue()
        return [self.collect('get_indicator_params', dict(indicator=field['indicator'], func_name='基础函数'))
            for field in self.fixture['request']['indipara']]

    def test_new_policy_is_bound_to_the_task_and_old_defaults_do_not_change(self):
        self.assertEqual(self.task[IFIND_METADATA_POLICY], 1)
        old = copy.deepcopy(self.task); old.pop(IFIND_METADATA_POLICY)
        self.assertNotEqual(input_fingerprint(old), input_fingerprint(self.task))
        self.assertEqual(metadata_request_shapes(old), [])

    def test_before_actual_policy_only_policy_lookup_is_offered(self):
        self.assertEqual([row['operation'] for row in metadata_request_shapes(self.task)], ['get_limit_policy'])
        with self.assertRaises(ContractError):
            self.collect('get_indicators', dict(indicators=['营业收入'], market='A股'))
        self.assertEqual(self.calls, [])

    def test_policy_keeps_original_shape_without_inventing_an_error_code(self):
        record = self.collect('get_limit_policy', {})
        self.assertEqual(record['snapshot'], self.fixture['policy'])
        self.assertNotIn('errorcode', record['snapshot'])
        self.assertEqual(record['status'], '成功待核验')
        self.assertIsNotNone(current_query_reference(self.task, record))

    def test_search_uses_the_official_get_query_and_header_not_a_token_parameter(self):
        self.catalogue()
        self.assertEqual(self.calls[-1]['method'], 'GET')
        self.assertIsNone(self.calls[-1]['body'])
        self.assertEqual(json.loads(self.calls[-1]['query']['indicators'][0]), ['营业收入', '归属于母公司所有者的净利润'])
        self.assertNotIn('access_token', self.calls[-1]['query'])

    def test_parameters_keep_actual_order_and_do_not_choose_latest_defaults(self):
        records = self.definitions()
        rows = records[0]['metadata_contract']['parsed']
        self.assertEqual([row['name'] for row in rows], ['D', 'C'])
        self.assertEqual(rows[0]['defaultValue'], '8')
        tools = metadata_request_shapes(self.task)
        basic = next(row for row in tools if row['operation'] == 'basic_data_service')
        self.assertTrue(all('defaultValue' not in row for rows in basic['field_definitions'].values() for row in rows))
        self.assertEqual(self.task['ledger']['used']['model_calls'], 0)

    def test_full_actual_48_company_batch_keeps96_original_values_without_approval(self):
        self.definitions()
        record = self.collect('basic_data_service', self.fixture['request'])
        self.assertEqual(record['snapshot'], self.fixture['basic'])
        self.assertEqual(record['metadata_contract']['parsed']['row_count'], 48)
        self.assertEqual(sum(len(cells) for row in record['snapshot']['tables'] for cells in row['table'].values()), 96)
        self.assertFalse(record['metadata_contract']['numeric_evidence_approved'])
        self.assertEqual(self.task['ledger']['used']['data_requests'], 5)
        self.assertEqual(self.task['evidence'], [])
        self.assertEqual(self.task['calculations'], [])
        self.assertIsNotNone(current_query_reference(self.task, record))

    def test_planning_schema_exposes_the_same_registered_requests(self):
        self.definitions()
        contract = data_request_contract(self.task)
        schema = dict(anyOf=_planned_request_schemas(self.task, contract))
        request = dict(id='company_half_year', source='ifind', operation='basic_data_service',
            parameters=self.fixture['request'], question_ids=[self.task['questions'][0]['id']],
            selection_reason='使用本任务实际字典明确选择报告期及合并口径，仍保留单位缺口')
        self.assertTrue(_matches(request, schema))

    def test_unsupported_or_foreign_indicator_is_rejected_before_spending(self):
        self.catalogue(); before = len(self.calls)
        with self.assertRaises(ContractError):
            self.collect('get_indicator_params', dict(indicator='ths_guessed_stock', func_name='基础函数'))
        self.assertEqual(len(self.calls), before)

    def test_changed_metadata_cannot_authorize_a_later_financial_request(self):
        records = self.definitions()
        records[0]['snapshot']['data'] = '[]'
        self.assertEqual(len(basic_financial_definitions(self.task)), 1)
        with self.assertRaises(ContractError):
            validate_source_request(self.task, 'ifind', 'basic_data_service', self.fixture['request'])

    def test_imports_do_not_acquire_current_parameter_authority(self):
        self.definitions()
        for record in self.task['source_records']:
            record.pop('acquisition', None)
        self.assertEqual(basic_financial_definitions(self.task), {})
        self.assertEqual([row['operation'] for row in metadata_request_shapes(self.task)], ['get_limit_policy'])

    def test_foreign_task_or_version_cannot_borrow_the_dictionary(self):
        self.definitions()
        for change in (dict(id='foreign'), dict(version=self.task['version']+1)):
            task = dict(self.task, **change)
            self.assertEqual(basic_financial_definitions(task), {})

    def test_historical_document_and_invalid_policy_do_not_gain_tools(self):
        for change in (dict(research_mode='historical'), dict(intent='原件内容核对')):
            self.assertEqual(metadata_request_shapes(dict(self.task, **change)), [])
        for version in (True, '1', 2):
            with self.assertRaises(ContractError):
                metadata_request_shapes(dict(self.task, **{IFIND_METADATA_POLICY: version}))

    def test_latest_default_future_period_and_reversed_parameters_are_rejected(self):
        self.definitions()
        for values in (['8', '1'], ['20261231', '1'], ['1', '20260630'], ['20260630', 'unknown']):
            request = copy.deepcopy(self.fixture['request'])
            request['indipara'][0]['indiparams'] = values
            with self.subTest(values=values), self.assertRaises(ContractError):
                validate_source_request(self.task, 'ifind', 'basic_data_service', request)

    def test_unrelated_securities_and_duplicates_do_not_expand_the_scope(self):
        self.definitions()
        for codes in ('AAPL.US', '600000.SH', self.fixture['request']['codes']+',600030.SH'):
            request = dict(self.fixture['request'], codes=codes)
            with self.subTest(codes=codes), self.assertRaises(ContractError):
                validate_source_request(self.task, 'ifind', 'basic_data_service', request)

    def test_mismatched_original_is_retained_but_cannot_mint_a_current_reference(self):
        self.definitions()
        self.fixture = copy.deepcopy(self.fixture)
        self.fixture['basic']['inputParams']['params'][0]['params'][2]['value'] = '2'
        record = self.collect('basic_data_service', self.fixture['request'])
        self.assertEqual(record['snapshot'], self.fixture['basic'])
        self.assertEqual(record['status'], '返回结构异常')
        self.assertIsNone(current_query_reference(self.task, record))
        self.assertNotIn('acquisition', record)

    def test_missing_financial_cell_is_not_filled_or_a_whole_batch_failure(self):
        self.definitions(); self.fixture = copy.deepcopy(self.fixture)
        self.fixture['basic']['tables'][0]['table']['ths_revenue_stock'][0] = None
        record = self.collect('basic_data_service', self.fixture['request'])
        self.assertEqual(record['status'], '成功待核验')
        self.assertEqual(len(record['metadata_contract']['parsed']['missing_values']), 1)
        self.assertIsNone(record['snapshot']['tables'][0]['table']['ths_revenue_stock'][0])

    def test_metadata_timeout_keeps_one_charge_without_a_retry(self):
        before = self.task['ledger']['used']['data_requests']
        with patch.object(self.collector, '_http_json', side_effect=TimeoutError()):
            with self.assertRaises(ContractError): self.collect('get_limit_policy', {})
        self.assertEqual(self.task['ledger']['used']['data_requests'], before+1)
        self.assertEqual(len(self.task['ledger']['dispatches']), 1)

    def test_basic_timeout_keeps_one_charge_without_a_retry(self):
        self.definitions(); before = self.task['ledger']['used']['data_requests']
        with patch.object(self.collector, '_http_json', side_effect=TimeoutError()) as wire:
            with self.assertRaises(ContractError): self.collect('basic_data_service', self.fixture['request'])
        self.assertEqual(wire.call_count, 1)
        self.assertEqual(self.task['ledger']['used']['data_requests'], before+1)

    def test_registry_remains_raw_only_without_financial_units_or_fields(self):
        self.assertTrue(all(row['raw_only'] and row['fields'] == {} for row in IFIND_METADATA_REGISTRY.values()))

    def test_prior_policy_cannot_be_added_after_the_metadata_dispatch(self):
        self.catalogue()
        policy, search = self.task['source_records']
        policy['acquisition']['dispatch_index'] = search['acquisition']['dispatch_index']+1
        self.assertIsNone(current_query_reference(self.task, search))

    def test_typed_metadata_reference_rejects_boolean_versions(self):
        record = self.collect('get_limit_policy', {})
        record['research_version'] = True
        self.assertIsNone(current_query_reference(self.task, record))

    def test_single_request_cell_limit_is_enforced_before_dispatch(self):
        self.fixture = copy.deepcopy(self.fixture)
        self.fixture['policy']['request_data_limit']['rules'][0]['max_per_request'] = 1
        self.definitions(); before = len(self.calls)
        with self.assertRaises(ContractError):
            self.collect('basic_data_service', self.fixture['request'])
        self.assertEqual(len(self.calls), before)

    def test_malformed_parameter_result_is_retained_without_inventing_empty_definitions(self):
        self.catalogue(); self.fixture = copy.deepcopy(self.fixture)
        code = self.fixture['request']['indipara'][0]['indicator']
        self.fixture['parameters'][code]['data'] = 'invalid-json'
        record = self.collect('get_indicator_params', dict(indicator=code, func_name='基础函数'))
        self.assertEqual(record['snapshot']['data'], 'invalid-json')
        self.assertEqual(record['status'], '返回结构异常')
        self.assertEqual(basic_financial_definitions(self.task), {})

    def test_budget_exhaustion_does_not_dispatch_or_reset_ledger(self):
        self.task['ledger']['limits']['data_requests'] = 0
        with self.assertRaises(ContractError): self.collect('get_limit_policy', {})
        self.assertEqual(self.calls, [])
        self.assertEqual(self.task['ledger']['used']['data_requests'], 0)

    def test_raw_credential_echo_is_removed_from_both_snapshot_and_metadata_contract(self):
        self.fixture = copy.deepcopy(self.fixture)
        self.fixture['policy']['debug'] = 'synthetic_metadata_control_token'
        record = self.collect('get_limit_policy', {})
        self.assertNotIn('synthetic_metadata_control_token', json.dumps(record))

    def test_rate_wait_uses_the_actual_policy_without_a_new_query(self):
        self.collect('get_limit_policy', {})
        timestamp = self.task['ledger']['dispatches'][-1]['time']
        before = len(self.calls)
        with patch('researchhelper.financial_sources.now', return_value=timestamp):
            self.assertGreaterEqual(metadata_wait_seconds(self.task, 'get_limit_policy'), .1)
        self.assertEqual(len(self.calls), before)

    def test_restart_restores_originals_dictionary_bindings_and_the_same_ledger(self):
        self.definitions(); record = self.collect('basic_data_service', self.fixture['request'])
        self.agent.archive.save(self.task)
        restored = self.agent.archive.get(self.task['id'])
        self.assertEqual(restored['ledger'], self.task['ledger'])
        self.assertEqual(restored['source_records'][-1]['snapshot'], record['snapshot'])
        self.assertEqual(set(basic_financial_definitions(restored)), set(basic_financial_definitions(self.task)))
        self.assertIsNotNone(current_query_reference(restored, restored['source_records'][-1]))

    def _engine_policy_request(self):
        self.agent.collector = self.collector
        self.agent.source_credentials = {'ifind': {'access_token': 'synthetic_metadata_control_token'}}
        request = dict(id='reuse_policy', source='ifind', operation='get_limit_policy', parameters={},
            question_ids=[self.task['questions'][0]['id']], selection_reason='仅复用真实同任务策略')
        with patch.object(self.agent, '_save_model_checkpoint'), patch.object(self.agent, '_journal'):
            self.agent._acquire_sources(self.task, [request], metadata_dependency=True)

    def test_agent_reuses_genuine_metadata_without_another_request(self):
        record = self.collect('get_limit_policy', {})
        self._engine_policy_request()
        self.assertEqual(len(self.calls), 1)
        self.assertIn('reuse_policy', record['data_need_ids'])

    def test_agent_does_not_alias_imported_metadata_as_an_actual_acquisition(self):
        record = self.collect('get_limit_policy', {}); record.pop('acquisition')
        self._engine_policy_request()
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn('reuse_policy', record.get('data_need_ids', []))
        self.assertEqual([row['operation'] for row in metadata_request_shapes(self.task)], ['get_limit_policy'])

    def test_analysis_inventory_lists_raw_financial_fields_without_values_or_approval(self):
        from researchhelper.source_inventory import inventory
        self.definitions(); record = self.collect('basic_data_service', self.fixture['request'])
        record['question_ids'] = [self.task['questions'][0]['id']]
        result = inventory(self.task)
        self.assertEqual(len(result['records']), 1)
        source = result['records'][0]
        self.assertEqual(source['reference']['kind'], 'query_request')
        self.assertEqual(len(source['groups']), 48)
        self.assertTrue(all(field['status'] == 'present_unverified'
            for group in source['groups'] for field in group['fields']))
        self.assertFalse(result['financial_approval'] or result['absence_proved'])
        self.assertNotIn('13836472563', json.dumps(result))
        record.pop('acquisition')
        self.assertEqual(inventory(self.task)['records'], [])
