"""Contract bridge: a synthetic close and retained exchange NAV, not a trade."""
import copy
import unittest

try:
    from . import test_sse_etf_pcf_source as fixtures
except ImportError:
    import test_sse_etf_pcf_source as fixtures
from researchhelper.contracts import (ContractError, METHOD_REGISTRY, calculation_context_contract,
    validate_calculation_context, charge, now)
from researchhelper.evidence import (api_calculation_context_contract, snapshot_record,
    validate_calculation_context_basis, _record_current_acquisition, candidate_evidence)
from researchhelper.planning import input_fingerprint
from researchhelper.etf_sources import share_context_choices
from researchhelper.output_schema import original_span_output_schema, decode_original_spans
from researchhelper.reasoning import extractable_source_views


def literal(schema):
    if 'enum' in schema: return schema['enum'][0]
    if 'anyOf' in schema: return literal(schema['anyOf'][0])
    if schema['type'] == 'object': return {key:literal(value) for key,value in schema['properties'].items()}
    if schema['type'] == 'array': return []
    if schema['type'] == 'string': return 'control'
    if schema['type'] == 'null': return None
    if schema['type'] == 'boolean': return True
    raise AssertionError('Unsupported control type')


class SSEETFPcfPriceContextTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.SSEETFPcfSourceTests()
        fixture.setUpClass(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.task = fixture.task
        self.task['questions'][0]['method_needs'] = ['etf_daily_nav_premium_v1']
        self.task['input_hash'] = input_fingerprint(self.task)
        nav = fixture.collect()
        self.task['source_records'].append(snapshot_record(self.task, 'ifind', 'cmd_history_quotation',
            dict(codes='510300.SH', indicators='close', startdate='2026-10-21', enddate='2026-10-21',
                functionpara=dict(Currency='RMB', CPS='1', Fill='Omit')),
            dict(errorcode=0, tables=[dict(thscode='510300.SH', time=['2026-10-21'], table=dict(close=[4.18]))])))
        price = self.task['source_records'][-1]
        charge(self.task['ledger'], 'data_requests')
        event = dict(kind='data_requests', source='ifind', operation='cmd_history_quotation',
            fingerprint=price['request_hash'], batch=self.task['batch'], status='成功', time=now(),
            transport_isolated=True, network_request_started=True, network_worker_reaped=True,
            synthetic_test_transport=True)
        self.task['ledger']['dispatches'].append(event)
        _record_current_acquisition(self.task, price, event)
        self.price, self.nav = price, nav
        nav_contract = api_calculation_context_contract(nav)
        price_contract = api_calculation_context_contract(price)
        nav_context = dict(nav_contract['fixed_fields'], fund_id='510300.SH')
        price_context = dict(fund_id='510300.SH', currency='CNY', price_kind='close',
            observation_date='2026-10-21', share_class='listed_etf_unit:510300.SH')
        def item(record, value, values, bases):
            return dict(id=record['id'], task_id=self.task['id'], research_version=self.task['version'], unit='元',
                object='510300.SH', period='2026-10-21', value=value, artifact=record,
                snapshot_hash=record['hash'],
                identity=dict(provider_code='510300.SH', object='510300.SH'),
                record_locator=(dict(row=0, field='unit_nav') if record is nav
                    else dict(table=0, row=0, field='close')),
                calculation_context=values, calculation_context_basis=bases)
        price_bases = {key: copy.deepcopy((nav_contract if key=='share_class' else price_contract)
            ['basis_templates'][key]) for key in price_context}
        self.bindings = dict(price=item(price, 4.18, price_context, price_bases),
            nav=item(nav, 4.1856, nav_context, {key:copy.deepcopy(nav_contract['basis_templates'][key])
                for key in nav_context}))
        self.method = 'etf_daily_nav_premium_v1'
        self.parameters = dict(METHOD_REGISTRY[self.method]['parameters'], valuation_date='2026-10-21')

    def test_own_quote_roles_and_explicit_exchange_share_role_form_same_day_inputs(self):
        for item in self.bindings.values():
            validate_calculation_context_basis(item, self.task['source_records'], task=self.task)
        validate_calculation_context(self.method, self.bindings, self.parameters,
            context_contract=calculation_context_contract(self.task, self.method))
        self.assertEqual(self.task['evidence'], [])
        self.assertEqual(self.task['calculations'], [])
        self.assertEqual(self.task['ledger']['used']['model_calls'], 0)

    def test_wrong_day_or_borrowed_field_remains_invalid(self):
        for role, field, value in (('price', 'observation_date', '2026-10-22'),
                ('nav', 'valuation_date', '2026-10-22'), ('price', 'share_class', 'A')):
            bindings = copy.deepcopy(self.bindings)
            bindings[role]['calculation_context'][field] = value
            with self.subTest(role=role, field=field), self.assertRaises(ContractError):
                validate_calculation_context_basis(bindings[role], self.task['source_records'], task=self.task)

    def wire(self):
        views = extractable_source_views(self.task)
        schema = original_span_output_schema(self.task, views)
        variant = next(option for option in schema['properties']['selections']['items']['anyOf']
            if option['properties'].get('additional_contexts', {}).get('items', {}).get('anyOf'))
        selected = literal(variant)
        option = variant['properties']['selection']['properties']['evidence_specs']['items']['anyOf'][0]
        proposal = literal(option)
        proposal['api_cell_selections'] = [option['properties']['api_cell_selections']['items']['enum'][0]]
        proposal['question_ids'] = ['q1']
        proposal['review'].update(question_ids=['q1'], allowed_natures=['已实现事实'],
            conditions=['仅用于本次同日数据事后对比，不证明历史可得。'], reason='离线显式选择控制。')
        selected['selection']['evidence_specs'] = [proposal]
        selected['additional_contexts'] = [literal(variant['properties']['additional_contexts']['items']['anyOf'][0])]
        wire = literal(schema); wire['selections'] = [selected]
        return views, schema, wire

    def test_explicit_model_selector_decodes_the_shared_role_but_does_not_approve(self):
        views, schema, wire = self.wire()
        before = copy.deepcopy((self.task, wire))
        result = decode_original_spans(wire, schema, (self.task, views))
        self.assertEqual(len(result['evidence_specs']), 1)
        spec = result['evidence_specs'][0]
        self.assertEqual(spec['calculation_context']['fund_id'], '510300.SH')
        self.assertEqual(spec['calculation_context']['share_class'], 'listed_etf_unit:510300.SH')
        self.assertEqual(spec['calculation_context_basis']['share_class']['artifact_id'], self.nav['id'])
        self.assertNotIn('source_check', spec)
        item = candidate_evidence(self.task, self.price, spec)
        self.assertEqual(item['source_check'], '通过')
        self.assertEqual(item['applicability_check'], '待核验')
        self.assertEqual((self.task, wire), before)

    def test_legacy_source_policy_does_not_gain_api_only_or_shared_roles(self):
        self.task['official_etf_pcf_version'] = 1
        self.assertEqual(share_context_choices(self.task, self.price, dict(table=0, row=0, field='close'), '510300.SH'), [])
        self.assertIsNone(original_span_output_schema(self.task, extractable_source_views(self.task)))
        with self.assertRaises(ContractError):
            validate_calculation_context_basis(self.bindings['price'], self.task['source_records'], task=self.task)

    def test_missing_actual_receipt_or_foreign_task_has_no_shared_choices(self):
        for changed in ('acquisition', 'task_id'):
            task = copy.deepcopy(self.task)
            record = next(item for item in task['source_records'] if item['id'] == self.nav['id'])
            if changed == 'acquisition': record.pop(changed)
            else: record[changed] = 'foreign'
            self.assertEqual(share_context_choices(task, self.price, dict(table=0, row=0, field='close'), '510300.SH'), [])

    def test_shared_role_is_not_a_general_cross_api_currency_or_date_permission(self):
        for field, value in (('currency', 'CNY'), ('observation_date', '2026-10-21')):
            item = copy.deepcopy(self.bindings['price'])
            item['calculation_context'] = {field:value}
            item['calculation_context_basis'] = {field:dict(kind='api', artifact_id=self.nav['id'],
                snapshot_hash=self.nav['hash'], field=field)}
            with self.subTest(field=field), self.assertRaises(ContractError):
                validate_calculation_context_basis(item, self.task['source_records'], task=self.task)

    def test_unread_exchange_source_never_enters_model_choices(self):
        views = [view for view in extractable_source_views(self.task) if view['id'] == self.price['id']]
        self.assertIsNone(original_span_output_schema(self.task, views))
        self.assertEqual(share_context_choices(self.task, self.price, dict(table=0, row=0, field='close'),
            '510300.SH', allowed_ids={self.price['id']}), [])

    def test_changed_or_omitted_reference_is_not_autocompleted(self):
        views, schema, wire = self.wire()
        wire['selections'][0]['additional_contexts'][0]['context_ref'] = 'foreign'
        with self.assertRaises(ContractError): decode_original_spans(wire, schema, (self.task, views))
        views, schema, wire = self.wire()
        wire['selections'][0]['additional_contexts'] = []
        spec = decode_original_spans(wire, schema, (self.task, views))['evidence_specs'][0]
        self.assertNotIn('share_class', spec['calculation_context'])

    def test_multiday_prices_split_the_eligible_day_from_other_cells(self):
        record = snapshot_record(self.task, 'ifind', 'cmd_history_quotation',
            dict(codes='510300.SH', indicators='close', startdate='2026-10-20', enddate='2026-10-21',
                functionpara=dict(Currency='RMB', CPS='1', Fill='Omit')),
            dict(errorcode=0, tables=[dict(thscode='510300.SH', time=['2026-10-20','2026-10-21'],
                table=dict(close=[4.15,4.18]))]))
        self.task['source_records'].append(record)
        charge(self.task['ledger'], 'data_requests')
        event = dict(kind='data_requests', source='ifind', operation='cmd_history_quotation',
            fingerprint=record['request_hash'], batch=self.task['batch'], status='成功', time=now(),
            transport_isolated=True, network_request_started=True, network_worker_reaped=True,
            synthetic_test_transport=True)
        self.task['ledger']['dispatches'].append(event)
        _record_current_acquisition(self.task, record, event)
        self.assertEqual(share_context_choices(self.task, record, dict(table=0,row=0,field='close'), '510300.SH'), [])
        self.assertEqual(len(share_context_choices(self.task, record, dict(table=0,row=1,field='close'), '510300.SH')), 1)
        views = [view for view in extractable_source_views(self.task) if view['id'] in (self.nav['id'],record['id'])]
        schema = original_span_output_schema(self.task, views)
        variants = [item for item in schema['properties']['selections']['items']['anyOf']
            if item['properties']['source_ref']['enum'] == ['source_2']]
        self.assertEqual(len(variants), 2)
        self.assertEqual(sum(bool(item['properties']['additional_contexts']['items'].get('anyOf')) for item in variants), 1)
