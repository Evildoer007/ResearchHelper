"""Validate separate price and NAV dates on an adopted offline evidence pair."""
import copy
import tempfile
import unittest

from tests import ROOT
from tests.test_sse_nav_delivery_chain import SSENavDeliveryChainTests
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import (ContractError, METHOD_REGISTRY, fingerprint,
    calculation_dependency_hash, calculation_context_contract, validate_calculation_context)
from researchhelper.engine import ResearchAgent
from researchhelper.knowledge import ResearchKnowledge
from researchhelper.output_schema import deferred_calculation_output_schema
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import calculation_input_requirements
from researchhelper.reports import compose
from researchhelper.source_reader import validate_read_specs
from researchhelper.visual import research_figures


METHOD = 'etf_daily_nav_premium_v1'
POLICY = 'daily_nav_date_roles_version'
OBSERVATION_DATE = '2026-10-21'


class DailyNavDateRoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = SSENavDeliveryChainTests()
        try:
            fixture.setUp()
            proposal = fixture.proposal()
            validate_read_specs(fixture.task, proposal['evidence_specs'], fixture.reading_bundle())
            cls.initial = fixture.agent.start(fixture.task['id'], proposal, defer_delivery=True)
            if not cls.initial.get('host_checkpoint') or len(cls.initial['evidence']) != 2:
                raise AssertionError(cls.initial['gaps'])
            cls.spec = proposal['calculations'][0]
        finally:
            fixture.doCleanups()

    def setUp(self):
        self.task = copy.deepcopy(self.initial)
        self.task['calculations'] = []
        self.contract = calculation_context_contract(self.task, METHOD)
        evidence = {item['id']: item for item in self.task['evidence']}
        self.bindings = {role: evidence[reference] for role, reference in self.spec['inputs'].items()}
        self.parameters = copy.deepcopy(self.spec['parameters'])

    def validate(self, bindings=None, contract=None):
        validate_calculation_context(METHOD, bindings or self.bindings, self.parameters,
                                    context_contract=self.contract if contract is None else contract)

    def test_same_day_role_contract_needs_no_fabricated_price_valuation_date(self):
        before = copy.deepcopy(self.bindings)
        self.validate()
        self.assertEqual(self.bindings, before)
        self.assertNotIn('valuation_date', self.bindings['price']['calculation_context'])
        self.assertEqual(self.contract['version'], 2)

    def test_legacy_method_requires_the_same_valuation_field_for_both_inputs(self):
        with self.assertRaisesRegex(ContractError, 'valuation_date'):
            validate_calculation_context(METHOD, self.bindings, self.parameters)
        legacy = copy.deepcopy(self.task); legacy.pop(POLICY)
        self.assertIsNone(calculation_context_contract(legacy, METHOD))

    def test_each_role_requires_its_own_date_and_matching_period(self):
        for role, field in (('price', 'observation_date'), ('nav', 'valuation_date')):
            for value in ('2026-10-20', '', None, OBSERVATION_DATE + 'T15:00:00+08:00'):
                records = copy.deepcopy(self.bindings); records[role]['calculation_context'][field] = value
                with self.subTest(role=role, value=value), self.assertRaises(ContractError): self.validate(records)
            records = copy.deepcopy(self.bindings); records[role]['period'] = '2026-10-20'
            with self.assertRaises(ContractError): self.validate(records)

    def test_wrong_price_observation_is_not_hidden_by_an_extra_valuation_date(self):
        records = copy.deepcopy(self.bindings)
        records['price']['calculation_context'].update(observation_date='2026-10-20', valuation_date=OBSERVATION_DATE)
        with self.assertRaises(ContractError): self.validate(records)

    def test_share_currency_and_close_nav_definition_gates_are_unchanged(self):
        for role, field, value in (('nav', 'share_class', 'unknown'), ('nav', 'fund_id', 'other'),
                ('nav', 'currency', 'USD'), ('nav', 'nav_kind', 'IOPV'), ('price', 'price_kind', 'intraday')):
            records = copy.deepcopy(self.bindings); records[role]['calculation_context'][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError): self.validate(records)

    def test_unregistered_or_wrong_method_contract_is_rejected(self):
        changed = copy.deepcopy(self.contract); changed['version'] = 99
        with self.assertRaises(ContractError): self.validate(contract=changed)
        with self.assertRaises(ContractError):
            validate_calculation_context('etf_nav_premium_v1', self.bindings,
                dict(METHOD_REGISTRY['etf_nav_premium_v1']['parameters'], valuation_time=OBSERVATION_DATE + 'T15:00:00+08:00'),
                context_contract=self.contract)

    def test_input_descriptors_are_per_role_not_one_shared_date_field(self):
        self.task['calculation_specs'] = []
        descriptor = calculation_input_requirements(self.task)['unbound_requirements'][0]
        self.assertEqual(descriptor['required_context_by_input'], self.contract['input_fields'])
        self.assertIn('observation_date', descriptor['required_context_by_input']['price'])
        self.assertNotIn('valuation_date', descriptor['required_context_by_input']['price'])
        self.assertIn('valuation_date', descriptor['required_context_by_input']['nav'])

    def test_bound_proposal_descriptors_keep_the_actual_input_role(self):
        self.task['calculation_specs'] = [dict(id='unit_projection_only', method=METHOD,
            inputs={key:item['id'] for key,item in self.bindings.items()}, parameters=self.parameters, question_ids=['q1'])]
        descriptor = calculation_input_requirements(self.task)['proposal_requirements']
        self.assertNotIn('valuation_date', descriptor[self.bindings['price']['id']]['required_context_fields'])
        self.assertIn('observation_date', descriptor[self.bindings['price']['id']]['required_context_fields'])

    def test_initial_binding_schema_does_not_suppress_date_role_inputs(self):
        self.task['calculation_specs'] = []
        schema = deferred_calculation_output_schema(self.task)
        self.assertIsNotNone(schema)
        choices = schema['properties']['calculations']['items']['anyOf']
        method = next(item for item in choices if item['properties']['method']['enum'] == [METHOD])
        inputs = method['properties']['inputs']['properties']
        self.assertEqual(inputs['price']['enum'], [self.bindings['price']['id']])
        self.assertEqual(inputs['nav']['enum'], [self.bindings['nav']['id']])

    def test_missing_nav_role_never_generates_an_empty_selection_enum(self):
        self.task['calculation_specs'] = []
        self.task['evidence'] = [copy.deepcopy(self.bindings['price']) for _ in range(2)]
        self.task['evidence'][1]['id'] = 'unit_duplicate_price_only'
        self.assertIsNone(deferred_calculation_output_schema(self.task))

    def test_optional_contract_changes_dependency_hash_without_changing_legacy_hash(self):
        item = dict(method=METHOD, inputs=dict(price=1, nav=1))
        fields = ('method', 'implementation_version', 'method_binding', 'input_versions', 'input_bindings',
                  'inputs', 'parameters', 'assumptions', 'assumption_sources', 'scenario_overrides')
        old = calculation_dependency_hash(item)
        self.assertEqual(old, fingerprint({key:item.get(key) for key in fields}))
        item['context_contract'] = self.contract
        self.assertNotEqual(calculation_dependency_hash(item), old)

    def calculated_projection(self):
        agent = ResearchAgent.__new__(ResearchAgent)
        agent.knowledge = ResearchKnowledge()
        agent._calculate_spec(self.task, copy.deepcopy(self.spec))
        self.assertEqual(len(self.task['calculations']), 1)
        return self.task['calculations'][0]

    def test_calculation_freeze_and_visual_keep_registered_role_contract(self):
        item = self.calculated_projection()
        self.assertEqual(item['context_contract'], self.contract)
        self.assertEqual(item['dependency_hash'], calculation_dependency_hash(item))
        content = compose(self.task)
        self.assertEqual(content[POLICY], 1)
        figure = research_figures(content)[item['id']]
        self.assertIn(OBSERVATION_DATE, figure['basis'])
        self.assertNotIn('valuation_date', self.bindings['price']['calculation_context'])
        self.assertEqual(self.task['status'], self.initial['status'])

    def test_report_refuses_removed_or_changed_context_contract(self):
        item = self.calculated_projection()
        item.pop('context_contract')
        item['dependency_hash'] = calculation_dependency_hash(item)
        with self.assertRaisesRegex(ContractError, '口径契约'):
            compose(self.task)

    def test_new_tasks_freeze_policy_without_rewriting_old_inputs_or_methods(self):
        legacy = copy.deepcopy(self.task); legacy.pop(POLICY)
        self.assertNotEqual(input_fingerprint(self.task), input_fingerprint(legacy))
        with tempfile.TemporaryDirectory(prefix='date-role-policy-') as directory:
            agent = ResearchAgent(ResearchArchive(directory), ROOT, offline=True, require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            task = agent.create('解释ETF净值', questions=[dict(id='q1', kind='fact', question='解释ETF净值',
                answer_scope='general_definition', knowledge_needs=['rh_k_etf_premium_measures'])])
            self.assertEqual(task[POLICY], 1)
            self.assertEqual(compose(agent.start(task['id']))[POLICY], 1)
            self.assertNotIn(POLICY, agent.create_case('mechanism'))


if __name__ == '__main__':
    unittest.main()
