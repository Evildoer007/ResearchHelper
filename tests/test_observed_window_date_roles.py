"""Query coverage and actual endpoint dates are distinct, versioned roles.

The synthetic response has a month start with no observation. The role
tests can run after historical delivery files are removed.
"""
import copy
from datetime import date, timedelta
import json
import tempfile
import unittest
from unittest.mock import patch
from tests import ROOT
from tests.test_long_series_reasoning_payload import synthetic_price_task
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import (ContractError, METHOD_REGISTRY, calculation_context_contract,
    calculation_dependency_hash, methods_for_task, validate_calculation_context)
from researchhelper.engine import ResearchAgent
from researchhelper.knowledge import ResearchKnowledge
from researchhelper.planning import input_fingerprint
from researchhelper.output_schema import deferred_calculation_output_schema
from researchhelper.reasoning import calculation_input_requirements
from researchhelper.reports import compose

POLICY = 'observed_window_date_roles_version'
METHOD = 'observed_price_return_v1'


class ObservedWindowDateRoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        month_start = date(2026, 8, 1)
        month_days = [month_start + timedelta(days=i) for i in range(31)]
        days = [day.isoformat() for day in month_days if day.weekday() < 5]
        window = month_start.isoformat(), month_days[-1].isoformat()
        with tempfile.TemporaryDirectory() as folder, patch(
                'socket.socket.connect', side_effect=AssertionError('Offline fixture')):
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True)
            try:
                task = synthetic_price_task(agent, days=days, query_window=window)
            finally:
                agent.close_runtime()
        task.pop(POLICY, None)
        task['status'] = '部分完成'
        ResearchAgent._gap(task, '合成样例仅验证区间计算，未生成研究回答。', category='question_coverage')
        task['calculation_specs'] = [dict(
            id='window_return', method=METHOD, question_ids=['q2'],
            inputs=dict(start_price=task['evidence'][0]['id'], end_price=task['evidence'][-1]['id']),
            parameters=dict(METHOD_REGISTRY[METHOD]['parameters'], start_date=days[0], end_date=days[-1]))]
        cls.original = json.dumps(task, ensure_ascii=False)

    def setUp(self):
        self.task = json.loads(self.original)
        self.task[POLICY] = 1
        self.spec = copy.deepcopy(self.task['calculation_specs'][0])
        by_id = {item['id']: item for item in self.task['evidence']}
        self.bindings = {name: by_id[ident] for name, ident in self.spec['inputs'].items()}
        self.parameters = self.spec['parameters']
        self.contract = calculation_context_contract(self.task, METHOD)

    def validate(self, bindings=None, parameters=None):
        validate_calculation_context(METHOD, bindings or self.bindings, parameters or self.parameters,
            context_contract=self.contract)

    def test_month_query_accepts_its_observed_endpoints_without_rewriting_evidence(self):
        before = copy.deepcopy(self.bindings)
        self.assertEqual(self.parameters['start_date'], '2026-08-03')
        self.assertEqual(self.bindings['start_price']['calculation_context']['window_start'], '2026-08-01')
        self.validate()
        self.assertEqual(self.bindings, before)

    def test_legacy_contract_keeps_original_rejection(self):
        with self.assertRaisesRegex(ContractError, 'window_start'):
            validate_calculation_context(METHOD, self.bindings, self.parameters)
        for version in (None, False, True, 0, 2):
            task = dict(self.task, **{POLICY: version})
            self.assertIsNone(calculation_context_contract(task, METHOD))

    def test_different_query_windows_or_outside_endpoints_reject(self):
        for field, value, both in (('window_start', '2026-08-02', False),
                ('window_end', '2026-09-01', False), ('window_start', '2026-08-04', True),
                ('window_end', '2026-08-30', True), ('window_start', '2026/08/01', True),
                ('window_start', True, True), ('window_end', None, True)):
            records = copy.deepcopy(self.bindings)
            for role in records if both else ('start_price',):
                records[role]['calculation_context'][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ContractError):
                self.validate(records)

    def test_observation_date_and_period_are_not_filled_from_window(self):
        for role, field in (('start_price', 'observation_date'), ('end_price', 'observation_date')):
            records = copy.deepcopy(self.bindings)
            records[role]['calculation_context'][field] = records[role]['calculation_context']['window_start']
            if records[role]['calculation_context'][field] == records[role]['period']:
                records[role]['calculation_context'][field] = '2026-08-02'
            with self.assertRaises(ContractError): self.validate(records)
        records = copy.deepcopy(self.bindings); records['start_price']['period'] = '2026-08-01'
        with self.assertRaises(ContractError): self.validate(records)

    def test_object_currency_basis_kind_and_company_action_checks_remain(self):
        for field, value in (('security_id', '600519.SH'), ('currency', 'USD'),
                ('price_basis', 'adjusted'), ('price_kind', 'intraday'), ('corporate_action_status', 'none')):
            records = copy.deepcopy(self.bindings)
            records['end_price']['calculation_context'][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError): self.validate(records)

    def test_foreign_contract_cannot_authorize_another_method(self):
        changed = copy.deepcopy(self.contract); changed['method'] = 'etf_daily_nav_premium_v1'
        with self.assertRaises(ContractError):
            validate_calculation_context(METHOD, self.bindings, self.parameters, context_contract=changed)

    def test_index_uses_same_window_roles_but_keeps_index_identity(self):
        method = 'observed_index_change_v1'
        contract = calculation_context_contract(self.task, method)
        parameters = dict(METHOD_REGISTRY[method]['parameters'], start_date='2026-08-03',
            end_date='2026-08-31', index_variant='price', index_currency='CNY')
        records = {role: dict(object='000300.SH', unit='点', period=day,
            calculation_context=dict(index_id='000300.SH', index_base='same_verified_base',
                index_variant='price', index_currency='CNY', level_kind='close',
                observation_date=day, window_start='2026-08-01', window_end='2026-08-31'))
            for role, day in (('start_level', '2026-08-03'), ('end_level', '2026-08-31'))}
        validate_calculation_context(method, records, parameters, context_contract=contract)
        with self.assertRaises(ContractError): validate_calculation_context(method, records, parameters)
        records['end_level']['calculation_context']['index_variant'] = 'total_return'
        with self.assertRaises(ContractError):
            validate_calculation_context(method, records, parameters, context_contract=contract)

    def test_catalogue_and_binding_descriptors_keep_price_fields_not_fund_fields(self):
        definition = methods_for_task(self.task)[METHOD]
        self.assertEqual(definition['binding_context'], METHOD_REGISTRY[METHOD]['binding_context'])
        self.assertEqual(definition['input_context_contract'], self.contract)
        self.assertNotIn('fund_id', definition['binding_context'])
        descriptor = calculation_input_requirements(self.task)['proposal_requirements']
        fields = descriptor[self.spec['inputs']['start_price']]['required_context_fields']
        self.assertIn('window_start', fields); self.assertIn('observation_date', fields)

    def test_unbound_price_roles_offer_all_returned_close_candidates_before_analysis(self):
        self.task['calculation_specs'] = []
        schema = deferred_calculation_output_schema(self.task)
        self.assertIsNotNone(schema)
        choices = schema['properties']['calculations']['items']['anyOf']
        variant = next(item for item in choices if item['properties']['method']['enum'] == [METHOD])
        inputs = variant['properties']['inputs']['properties']
        for role in ('start_price', 'end_price'):
            self.assertIn(self.spec['inputs'][role], inputs[role]['enum'])
            self.assertEqual(len(inputs[role]['enum']), 21)

    def test_wrong_price_kind_does_not_create_an_empty_role_enum(self):
        self.task['calculation_specs'] = []
        for item in self.task['evidence']:
            item['calculation_context']['price_kind'] = 'intraday'
        self.assertIsNone(deferred_calculation_output_schema(self.task))

    def calculated_projection(self):
        agent = ResearchAgent.__new__(ResearchAgent); agent.knowledge = ResearchKnowledge()
        agent._calculate_spec(self.task, self.spec)
        self.assertEqual(len(self.task['calculations']), 1)
        return self.task['calculations'][0]

    def test_actual_calculation_and_freeze_preserve_dates_and_context_version(self):
        calculation = self.calculated_projection()
        self.assertEqual(calculation['context_contract'], self.contract)
        self.assertEqual(calculation['dependency_hash'], calculation_dependency_hash(calculation))
        self.assertEqual(calculation['parameters']['start_date'], '2026-08-03')
        frozen = compose(self.task)
        self.assertEqual(frozen[POLICY], 1)
        self.assertEqual(self.task['status'], '部分完成')
        self.assertIn('自然月收益', self.contract['rule'])

    def test_report_rejects_removed_contract_even_if_dependency_hash_recomputed(self):
        calculation = self.calculated_projection()
        calculation.pop('context_contract')
        calculation['dependency_hash'] = calculation_dependency_hash(calculation)
        with self.assertRaisesRegex(ContractError, '口径契约'): compose(self.task)

    def test_new_task_policy_is_hashed_frozen_and_not_adopted_by_old_or_fixture_tasks(self):
        legacy = copy.deepcopy(self.task); legacy.pop(POLICY)
        self.assertNotEqual(input_fingerprint(self.task), input_fingerprint(legacy))
        with tempfile.TemporaryDirectory(dir=ROOT / 'result/acceptance') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            task = agent.create('解释价格变化', questions=[dict(id='q1', kind='fact', question='解释价格变化',
                answer_scope='general_definition', knowledge_needs=['rh_k_price_return_measures'])])
            self.assertEqual(task[POLICY], 1)
            self.assertEqual(compose(agent.start(task['id']))[POLICY], 1)
            self.assertNotIn(POLICY, agent.create_case('mechanism'))
        self.assertNotIn(POLICY, json.loads(self.original))


if __name__ == '__main__':
    unittest.main()
