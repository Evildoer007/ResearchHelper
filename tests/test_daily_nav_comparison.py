"""Daily descriptive ratios retain identity, source and historical boundaries."""
import copy
from decimal import Decimal
import unittest

from tests import test_original_span_selection as fixtures
from researchhelper.contracts import (ContractError, METHOD_REGISTRY, calculate,
    validate_calculation_context, validate_calculation_parameters)
from researchhelper.knowledge import ResearchKnowledge
from researchhelper.evidence import validate_calculation_context_basis, material_calculation_context_contract
from researchhelper.reports import METHOD_NOTATION
from researchhelper.visual import research_figures


METHOD = 'etf_daily_nav_premium_v1'


class DailyNavComparisonTests(unittest.TestCase):
    def setUp(self):
        self.parameters = dict(METHOD_REGISTRY[METHOD]['parameters'], valuation_date='2026-06-30')
        common = dict(fund_id='synthetic_fund', share_class='synthetic_listed_class',
                      currency='CNY', valuation_date='2026-06-30')
        self.records = {key: dict(id=key, object='SYNTHETIC_ETF', source='合成验收', unit='元', period='2026-06-30',
            calculation_context=dict(common, **extra)) for key, extra in
            (('price', {'price_kind': 'close'}), ('nav', {'nav_kind': 'official'}))}

    def test_signed_values_match_independent_decimal_arithmetic(self):
        validate_calculation_context(METHOD, self.records, self.parameters)
        for price, nav in ((1.02, 1), (0.98, 1), (1, 1), (7.42, 9.1808)):
            result = calculate(METHOD, dict(price=price, nav=nav), self.parameters)
            expected = (Decimal(str(price)) / Decimal(str(nav)) - 1) * 100
            self.assertLessEqual(abs(Decimal(str(result['value'])) - expected), Decimal(str(result['tolerance'])))
            self.assertEqual(result['unit'], '%')

    def test_date_only_is_explicit_and_does_not_supply_a_clock(self):
        validate_calculation_parameters(METHOD, self.parameters)
        self.assertNotIn('valuation_time', METHOD_REGISTRY[METHOD]['binding_context'])
        for date in ('2026-06', '2026-02-30', '2026-06-30T15:00:00+08:00', ''):
            with self.subTest(date=date), self.assertRaises(ContractError):
                validate_calculation_parameters(METHOD, dict(self.parameters, valuation_date=date))
        with self.assertRaises(ContractError):
            validate_calculation_parameters(METHOD, dict(self.parameters, valuation_time='2026-06-30T15:00:00+08:00'))

    def test_old_same_instant_method_keeps_its_original_full_clock_gate(self):
        parameters = dict(METHOD_REGISTRY['etf_nav_premium_v1']['parameters'], valuation_time='2026-06-30')
        with self.assertRaises(ContractError): validate_calculation_parameters('etf_nav_premium_v1', parameters)
        parameters['valuation_time'] = '2026-06-30T15:00:00+08:00'
        validate_calculation_parameters('etf_nav_premium_v1', parameters)
        self.assertEqual(METHOD_REGISTRY['etf_nav_premium_v1']['context_parameters'], ['valuation_time'])

    def test_fund_class_currency_and_object_cannot_be_mixed(self):
        for field, value in (('fund_id', 'other'), ('share_class', 'other_class'), ('currency', 'USD')):
            records = copy.deepcopy(self.records); records['nav']['calculation_context'][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                validate_calculation_context(METHOD, records, self.parameters)
        for field, value in (('object', 'OTHER_ETF'), ('unit', '美元'), ('usage_scope', 'document_content')):
            records = copy.deepcopy(self.records); records['nav'][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                validate_calculation_context(METHOD, records, self.parameters)

    def test_previous_day_recent_nav_and_intraday_reference_are_rejected(self):
        for field, value in (('valuation_date', '2026-06-29'), ('nav_kind', 'IOPV')):
            records = copy.deepcopy(self.records); records['nav']['calculation_context'][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                validate_calculation_context(METHOD, records, self.parameters)
        records = copy.deepcopy(self.records); records['nav']['period'] = '2026-06-29'
        with self.assertRaises(ContractError): validate_calculation_context(METHOD, records, self.parameters)
        records = copy.deepcopy(self.records); records['price']['calculation_context']['price_kind'] = 'intraday'
        with self.assertRaises(ContractError): validate_calculation_context(METHOD, records, self.parameters)

    def test_missing_class_or_unknown_numeric_input_does_not_gain_approval(self):
        records = copy.deepcopy(self.records); records['nav']['calculation_context'].pop('share_class')
        with self.assertRaises(ContractError): validate_calculation_context(METHOD, records, self.parameters)
        for price, nav in ((0, 1), (-1, 1), (1, 0), (True, 1), ('1.02', 1), (float('nan'), 1)):
            with self.subTest(price=price, nav=nav), self.assertRaises(ContractError):
                calculate(METHOD, dict(price=price, nav=nav), self.parameters)

    def test_an_api_close_cannot_be_laundered_into_official_nav(self):
        records = copy.deepcopy(self.records)
        records['nav'].update(artifact={'kind':'api'}, record_locator={'field':'close'})
        with self.assertRaisesRegex(ContractError, '行情价格字段不能改标'):
            validate_calculation_context(METHOD, records, self.parameters)

    def test_two_unknown_share_classes_do_not_become_identical_known_shares(self):
        records = copy.deepcopy(self.records)
        for record in records.values(): record['calculation_context']['share_class'] = '未知'
        with self.assertRaises(ContractError): validate_calculation_context(METHOD, records, self.parameters)

    def test_registered_knowledge_is_a_distinct_method_not_fact_or_definition(self):
        knowledge = ResearchKnowledge()
        entry = next(item for item in knowledge.catalog() if item['id'] == METHOD_REGISTRY[METHOD]['knowledge_id'])
        self.assertEqual(entry['implementation'], METHOD)
        self.assertEqual(entry['version'], 1)
        self.assertEqual(entry['type'], '方法')
        self.assertNotEqual(entry.get('knowledge_role'), 'general_definition')
        old = next(item for item in knowledge.catalog() if item['id'] == 'rh_k_nav_premium')
        self.assertEqual(old['version'], 1)

    def test_rendering_keeps_two_original_values_as_a_table_not_a_fake_path(self):
        records = copy.deepcopy(self.records)
        records['price']['value'], records['nav']['value'] = 1.02, 1
        calculation = dict(id='synthetic_daily', method=METHOD, inputs={'price':1.02,'nav':1},
            input_bindings={'price':'price','nav':'nav'}, input_refs=['price','nav'], parameters=self.parameters,
            output=calculate(METHOD, dict(price=1.02, nav=1), self.parameters))
        figure = research_figures(dict(evidence=list(records.values()), calculations=[calculation]))['synthetic_daily']
        self.assertEqual(figure['presentation'], 'table')
        self.assertEqual([row['value'] for row in figure['rows']], [1.02, 1])
        self.assertIn('2026-06-30', figure['basis'])
        self.assertIn('<mfrac>', METHOD_NOTATION[METHOD][1])


class ValuationDateAnchorTests(unittest.TestCase):
    def setUp(self):
        fixtures.OriginalSpanSelectionTests.setUpClass()
        fixture = fixtures.OriginalSpanSelectionTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.addCleanup(fixtures.OriginalSpanSelectionTests.tearDownClass)
        self.task = fixture.task
        self.item = fixture.decode()['evidence_specs'][0]
        self.item.update(task_id=self.task['id'], research_version=self.task['version'])
        date = fixture.prompt['untrusted_sources'][0]['observation_anchors'][fixture.date_index]
        self.anchor = dict(kind='material', artifact_id=fixture.views[0]['id'],
            snapshot_hash=fixture.views[0]['snapshot_hash'], part=date['part'], quote=date['quote'])
        self.item.update(calculation_context={'valuation_date':'2026-06-30'},
                         calculation_context_basis={'valuation_date':self.anchor})

    def test_actual_date_anchor_can_be_normalized_without_changing_the_quote(self):
        before = copy.deepcopy(self.item)
        validate_calculation_context_basis(self.item, self.task['source_records'])
        self.assertEqual(self.item, before)
        contract = material_calculation_context_contract(['valuation_date'])
        self.assertIn('valuation_date', contract['normalized_date_fields'])
        self.assertNotIn('valuation_date', contract['literal_fields'])

    def test_another_date_or_period_cannot_borrow_this_date_anchor(self):
        for change in ('value', 'period', 'hash'):
            item = copy.deepcopy(self.item)
            if change == 'value': item['calculation_context']['valuation_date'] = '2026-06-29'
            elif change == 'period': item['period'] = '2026-03-31'
            else: item['calculation_context_basis']['valuation_date']['snapshot_hash'] = 'changed'
            with self.subTest(change=change), self.assertRaises(ContractError):
                validate_calculation_context_basis(item, self.task['source_records'])

    def test_a_date_does_not_prove_an_invented_fifteen_oclock_timestamp(self):
        item = copy.deepcopy(self.item)
        item['calculation_context'] = {'valuation_time':'2026-06-30T15:00:00+08:00'}
        item['calculation_context_basis'] = {'valuation_time':self.anchor}
        with self.assertRaises(ContractError): validate_calculation_context_basis(item, self.task['source_records'])


if __name__ == '__main__':
    unittest.main()
