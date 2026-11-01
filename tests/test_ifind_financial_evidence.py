"""Synthetic definition fixtures validate mechanics, not real supplier units."""

import copy
import unittest
from unittest.mock import patch

from researchhelper.contracts import fingerprint
from researchhelper.ifind_financial_evidence import basic_financial_contract
from researchhelper import evidence
from researchhelper.contracts import calculate, validate_calculation_context, validate_evidence
from researchhelper.analyzer import ResearchAnalyzer


def definition(indicator='fixture_profit', metric='net_profit'):
    claims = dict(indicator=indicator, unit='元', currency='CNY', accounting_standard='CAS',
                  metric=metric, statement_scope='consolidated', report_type='1',
                  parameter_order=['D', 'C'], period_kind='cumulative',
                  applicable_codes=['000001.SZ'])
    payload = dict(claims=copy.deepcopy(claims), note='SYNTHETIC TEST ONLY; no supplier facts proved')
    return dict(claims=claims, basis=dict(kind='vendor_metadata', source='synthetic-fixture',
                locator='fixture/claims', obtained_at='2026-10-20T00:00:00+08:00',
                payload=payload, hash=fingerprint(payload)))


def record(value=12):
    params = dict(codes='000001.SZ', indipara=[dict(indicator='fixture_profit', indiparams=['20260630', '1'])])
    snapshot = dict(errorcode=0, inputParams=dict(params=[dict(function='fixture_profit', params=[
        dict(name='FDREPORT', value='20260630'), dict(name='FL', value='1')])]),
        tables=[dict(thscode='000001.SZ', table=dict(fixture_profit=[value]))])
    return rehash(dict(id='fixture-record', kind='api', source='ifind', operation='basic_data_service',
        status='成功待核验', parameters=params, snapshot=snapshot, obtained_at='2026-10-23T12:00:00+08:00'))


def rehash(row):
    row['hash'] = fingerprint(row['snapshot'])
    row['request_hash'] = fingerprint({key: row[key] for key in ('source', 'operation', 'parameters')})
    return row


def refresh_definition(row):
    row['basis']['payload']['claims'] = copy.deepcopy(row['claims'])
    row['basis']['hash'] = fingerprint(row['basis']['payload'])
    return row


class BasicFinancialContractTests(unittest.TestCase):
    def setUp(self):
        self.record = record()
        self.definitions = {'fixture_profit': definition()}

    def contract(self):
        return basic_financial_contract(self.record, self.definitions)

    def assert_raw(self):
        result = self.contract()
        self.assertEqual(result['status'], 'raw_only')
        self.assertEqual(result['cells'], [])
        self.assertTrue(result['reason'])

    def test_registered_exact_cell_and_cumulative_context(self):
        before = copy.deepcopy((self.record, self.definitions))
        result = self.contract()
        self.assertEqual(result['status'], 'registered_constraints')
        cell = result['cells'][0]
        self.assertEqual(cell['record_locator'], dict(table=0, row=0, field='fixture_profit'))
        self.assertEqual(cell['period'], '2026年1-6月')
        self.assertEqual(cell['report_end'], '2026-06-30')
        self.assertEqual(cell['calculation_context']['accounting_scope'], 'consolidated')
        self.assertFalse(result['historical_availability_verified'])
        self.assertFalse(result['context_is_approval'])
        self.assertEqual(before, (self.record, self.definitions))
        result['fields']['fixture_profit']['definition_basis']['source'] = 'changed'
        self.assertEqual(self.definitions['fixture_profit']['basis']['source'], 'synthetic-fixture')

    def test_zero_is_preserved_and_null_is_not_filled(self):
        self.record = record(0)
        self.assertEqual(self.contract()['cells'][0]['raw_value'], 0)
        self.record = record(None)
        self.assert_raw()

    def test_nonfinite_boolean_string_are_rejected(self):
        for value in (float('inf'), float('-inf'), float('nan'), True, '12'):
            with self.subTest(value=value):
                self.record = record()
                self.record['snapshot']['tables'][0]['table']['fixture_profit'] = [value]
                if value in (True, '12'):
                    rehash(self.record)
                self.assert_raw()

    def test_wrong_response_code_and_definition_code(self):
        self.record['snapshot']['tables'][0]['thscode'] = '000002.SZ'
        rehash(self.record)
        self.assert_raw()
        self.record = record()
        self.definitions['fixture_profit']['claims']['applicable_codes'] = ['000002.SZ']
        refresh_definition(self.definitions['fixture_profit'])
        self.assert_raw()

    def test_wrong_period_and_scope_echo(self):
        for index, value in ((0, '20260331'), (1, '2')):
            with self.subTest(index=index):
                self.record = record()
                self.record['snapshot']['inputParams']['params'][0]['params'][index]['value'] = value
                rehash(self.record)
                self.assert_raw()

    def test_scope_must_match_definition(self):
        self.definitions['fixture_profit']['claims']['report_type'] = '2'
        refresh_definition(self.definitions['fixture_profit'])
        self.assert_raw()

    def test_explicit_definition_period_scope_cannot_be_reused(self):
        claims = self.definitions['fixture_profit']['claims']
        for periods in ('20260630', [], ['20260930'], [None], ['20260630', '20260630']):
            with self.subTest(periods=periods):
                claims['applicable_periods'] = periods
                refresh_definition(self.definitions['fixture_profit'])
                self.assert_raw()
        claims['applicable_periods'] = ['20260630']
        refresh_definition(self.definitions['fixture_profit'])
        self.assertEqual(self.contract()['status'], 'registered_constraints')

    def test_missing_each_semantic_definition_is_raw(self):
        for key in ('unit', 'currency', 'accounting_standard', 'metric', 'statement_scope', 'period_kind'):
            with self.subTest(key=key):
                self.definitions = {'fixture_profit': definition()}
                del self.definitions['fixture_profit']['claims'][key]
                refresh_definition(self.definitions['fixture_profit'])
                self.assert_raw()

    def test_parent_profit_not_promoted_to_statement_profit(self):
        self.definitions = {'fixture_profit': definition(metric='parent_net_profit')}
        self.assertEqual(self.contract()['cells'][0]['calculation_context']['metric'], 'parent_net_profit')
        self.definitions['fixture_profit']['claims']['metric'] = 'net_profit'
        self.assert_raw()  # Retained definition still says parent_net_profit.

    def test_future_report_and_future_definition_are_rejected(self):
        self.record['parameters']['indipara'][0]['indiparams'][0] = '20261231'
        self.record['snapshot']['inputParams']['params'][0]['params'][0]['value'] = '20261231'
        rehash(self.record)
        self.assert_raw()
        self.record = record()
        self.definitions['fixture_profit']['basis']['obtained_at'] = '2026-10-24T00:00:00+08:00'
        self.assert_raw()

    def test_historical_mode_and_cutoff_do_not_gain_current_snapshot(self):
        self.record['research_mode'] = 'historical'
        self.assert_raw()
        self.record = record()
        self.record['cutoff'] = '2026-03-31'
        self.assert_raw()

    def test_missing_basis_or_tampered_snapshot_cannot_register(self):
        self.definitions['fixture_profit'].pop('basis')
        self.assert_raw()
        self.definitions = {'fixture_profit': definition()}
        self.record['snapshot']['tables'][0]['table']['fixture_profit'] = [999]
        self.assert_raw()

    def test_partial_columns_remain_absent(self):
        self.record['snapshot']['tables'][0]['table'] = {}
        rehash(self.record)
        self.assert_raw()
        self.record['snapshot']['tables'][0].pop('table')
        rehash(self.record)
        self.assert_raw()

    def test_same_report_type_cannot_mix_parent_and_consolidated(self):
        self.record['parameters']['indipara'].append(dict(indicator='fixture_revenue', indiparams=['20260630', '1']))
        self.record['snapshot']['inputParams']['params'].append(dict(function='fixture_revenue',
            params=[dict(name='FDREPORT', value='20260630'), dict(name='FL', value='1')]))
        self.record['snapshot']['tables'][0]['table']['fixture_revenue'] = [20]
        self.definitions['fixture_revenue'] = definition('fixture_revenue', 'revenue')
        self.definitions['fixture_revenue']['claims']['statement_scope'] = 'parent'
        refresh_definition(self.definitions['fixture_revenue'])
        rehash(self.record)
        self.assert_raw()

    def test_unknown_definition_and_metric_are_raw(self):
        self.definitions = {}
        self.assert_raw()
        self.definitions = {'fixture_profit': definition(metric='adjusted_net_profit')}
        self.assert_raw()


class BasicFinancialEntryTests(unittest.TestCase):
    """Use genuine source/identity/ledger validators with synthetic retained data."""

    def setUp(self):
        self.definitions = {'fixture_profit': definition(),
                            'fixture_revenue': definition('fixture_revenue', 'revenue')}
        self.task = dict(id='fixture_task', version=1, batch=1, entry='公司', intent='财务分析',
            question='核对000001.SZ的累计收入和净利润', objects=['000001.SZ'],
            created_at='2026-10-23T00:00:00+08:00', cutoff='2026-10-23T23:59:59+08:00',
            research_mode='current', ifind_metadata_sources_version=1,
            fixed_financial_catalog_version=1, partial_financial_response_version=1,
            ledger=dict(dispatches=[]), source_records=[], questions=[dict(id='q1')])
        self.task['original_input'] = {key: copy.deepcopy(self.task[key]) for key in ('question', 'objects')}
        self.task['input_hash'] = fingerprint(self.task['original_input'])
        self.addCleanup(patch.stopall)
        patch('researchhelper.evidence._controlled_financial_definitions', return_value=self.definitions).start()
        patch('researchhelper.evidence.now', return_value='2026-10-23T23:59:59+08:00').start()
        patch('socket.socket.connect', side_effect=AssertionError('offline test only')).start()
        self.add_source('get_limit_policy', {}, dict(qps_limit=dict(unit='requests_per_second',
            default_per_function=1, account_total=1), request_data_limit=dict(rules=[
                dict(data_types=['basicData'], max_per_request=100)])))
        self.add_source('get_indicators', dict(indicators=['净利润', '营业收入'], market='A股'),
            dict(indicators=list(self.definitions), datas=[dict(indicator=key, name=key, path=[])
                                                        for key in self.definitions]))
        for key in self.definitions:
            self.add_source('get_indicator_params', dict(indicator=key, func_name='基础函数'),
                dict(code=1, data=[dict(name='D', describe='报告期', defaultValue='', choice={}, type='enum_or_input'),
                    dict(name='C', describe='报表类型', defaultValue='', choice={'合并': '1'}, type='enum')]))
        raw = record()
        raw['parameters']['indipara'].append(dict(indicator='fixture_revenue', indiparams=['20260630', '1']))
        raw['snapshot']['inputParams']['params'].append(dict(function='fixture_revenue', params=[
            dict(name='FDREPORT', value='20260630'), dict(name='FL', value='1')]))
        raw['snapshot']['tables'][0]['table']['fixture_revenue'] = [100]
        self.source = self.add_source('basic_data_service', raw['parameters'], raw['snapshot'])
        self.source.update(fixed_financial_catalog_version=1,
                           fixed_financial_catalog_digest=fingerprint(self.definitions))

    def add_source(self, operation, parameters, snapshot):
        index = len(self.task['source_records'])
        source = rehash(dict(id='fixture_source_' + str(index), kind='api', source='ifind',
            operation=operation, parameters=parameters, snapshot=snapshot, status='成功待核验',
            obtained_at='2026-10-23T12:00:00+08:00', task_id=self.task['id'], research_version=1))
        event = dict(kind='data_requests', fingerprint=source['request_hash'], status='成功', batch=1,
            transport_isolated=True, network_request_started=True, network_worker_reaped=True,
            response_snapshot_hash=source['hash'], response_obtained_at=source['obtained_at'])
        self.task['ledger']['dispatches'].append(event)
        source['acquisition'] = dict(version=1, task_id=self.task['id'], research_version=1, batch=1,
            dispatch_index=index, request_hash=source['request_hash'], snapshot_hash=source['hash'],
            obtained_at=source['obtained_at'])
        self.task['source_records'].append(source)
        return source

    def test_selection_source_verification_and_registered_calculation(self):
        before = copy.deepcopy(self.source['snapshot'])
        choices = evidence.api_cell_choices(self.task, self.source)
        self.assertEqual(len(choices), 2)
        items = {}
        for choice in choices:
            spec = evidence.expand_api_cell_selection(self.task, dict(api_cell_selection=choice['candidate_id'],
                question_ids=['q1'], review={}))
            item = evidence.candidate_evidence(self.task, self.source, spec)
            self.assertEqual(item['source_check'], '通过')
            self.assertEqual(item['applicability_check'], '待核验')
            review = dict(reason='合成原值仅用于本次累计同口径计算', question_ids=['q1'],
                          allowed_natures=['已实现事实'], conditions=['累计合并报表，保持原值及单位'])
            ResearchAnalyzer().assess(self.task, item, review, self.task['source_records'])
            validate_evidence(item, self.task)
            items[item['calculation_context']['metric']] = item
        parameters = dict(amount_unit='元', currency='CNY', period='2026年1-6月',
                          accounting_scope='consolidated', accounting_standard='CAS')
        validate_calculation_context('net_profit_margin_v1', items, parameters)
        result = calculate('net_profit_margin_v1', {key: item['value'] for key, item in items.items()}, parameters)
        self.assertEqual(result['value'], 12)
        self.assertEqual(before, self.source['snapshot'])
        self.assertNotIn('time', self.source['snapshot']['tables'][0])

    def test_missing_catalogue_marker_and_model_definitions_do_not_authorize(self):
        self.source.pop('fixed_financial_catalog_digest')
        self.source['definitions'] = self.definitions
        self.task['definitions'] = self.definitions
        self.assertEqual(evidence.api_cell_choices(self.task, self.source), [])

    def test_cross_task_historical_and_missing_wire_receipt_are_rejected(self):
        for mutation in ('task', 'historical', 'receipt', 'version'):
            task = copy.deepcopy(self.task)
            if mutation == 'task':
                task['id'] = 'another_task'
            elif mutation == 'historical':
                task['research_mode'] = 'historical'
            elif mutation == 'version':
                task['fixed_financial_catalog_version'] = True
            else:
                task['ledger']['dispatches'][-1]['network_worker_reaped'] = False
            self.assertEqual(evidence.api_cell_choices(task, task['source_records'][-1]), [])

    def test_unknown_real_supplier_attributes_stay_raw(self):
        with patch('researchhelper.evidence._controlled_financial_definitions', return_value={}):
            self.source['fixed_financial_catalog_digest'] = fingerprint({})
            self.assertEqual(evidence.api_cell_choices(self.task, self.source), [])
