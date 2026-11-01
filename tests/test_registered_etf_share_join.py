"""Registered, source-backed joins do not approve evidence or guess fields."""
import copy
import unittest
from unittest.mock import patch

try:
    from . import test_sse_etf_price_context as fixtures
except ImportError:
    import test_sse_etf_price_context as fixtures
from researchhelper.contracts import ContractError, fingerprint, validate_calculation_context, calculation_context_contract
from researchhelper.evidence import candidate_evidence
from researchhelper.etf_sources import join_price_share_context
from researchhelper.output_schema import decode_original_spans
from researchhelper.planning import input_fingerprint

POLICY = 'registered_etf_share_join_version'


class RegisteredETFShareJoinTests(unittest.TestCase):
    def setUp(self):
        self.control = fixtures.SSEETFPcfPriceContextTests()
        self.control.setUp()
        self.addCleanup(self.control.doCleanups)
        self.task = self.control.task
        self.task.update(run_mode='model', **{POLICY: 1})
        self.task['input_hash'] = input_fingerprint(self.task)
        views, schema, wire = self.control.wire()
        wire['selections'][0]['additional_contexts'] = []
        self.spec = decode_original_spans(wire, schema, (self.task, views))['evidence_specs'][0]

    def candidate(self, task=None, spec=None):
        return candidate_evidence(task or self.task, self.control.price, spec or self.spec)

    def test_unique_same_day_source_join_passes_source_not_financial_approval(self):
        item = self.candidate()
        self.assertEqual(item['calculation_context']['share_class'], 'listed_etf_unit:510300.SH')
        self.assertEqual(item['calculation_context_basis']['share_class']['artifact_id'], self.control.nav['id'])
        self.assertEqual(item['source_check'], '通过')
        self.assertEqual(item['applicability_check'], '待核验')
        self.assertFalse(item['calculation_context_joins'][0]['financial_approval'])
        validate_calculation_context(self.control.method, dict(price=item, nav=self.control.bindings['nav']),
            self.control.parameters, context_contract=calculation_context_contract(self.task, self.control.method))

    def test_original_spec_snapshot_task_and_ledger_are_not_rewritten(self):
        before = fingerprint((self.task, self.spec, self.control.price))
        self.candidate()
        self.assertEqual(fingerprint((self.task, self.spec, self.control.price)), before)
        self.assertNotIn('share_class', self.spec['calculation_context'])
        self.assertEqual(self.task['ledger']['used']['model_calls'], 0)

    def test_no_nav_or_unread_nav_retains_missing_share_class(self):
        task = copy.deepcopy(self.task)
        task['source_records'] = [r for r in task['source_records'] if r['id'] != self.control.nav['id']]
        self.assertNotIn('share_class', self.candidate(task)['calculation_context'])
        with patch('researchhelper.reasoning.extractable_source_views', return_value=[dict(id=self.control.price['id'])]):
            self.assertNotIn('share_class', self.candidate()['calculation_context'])

    def test_duplicate_possible_anchors_are_not_arbitrarily_selected(self):
        task = copy.deepcopy(self.task)
        task['source_records'].append(copy.deepcopy(self.control.nav))
        self.assertNotIn('share_class', self.candidate(task)['calculation_context'])

    def test_existing_explicit_value_or_basis_is_never_overwritten(self):
        from researchhelper.evidence import expand_api_value_reference, expand_api_context_reference
        spec = expand_api_context_reference(self.task, self.control.price,
            expand_api_value_reference(self.task, self.control.price, self.spec))
        for field in ('calculation_context', 'calculation_context_basis'):
            changed = copy.deepcopy(spec)
            changed[field]['share_class'] = 'unverified-explicit-value'
            self.assertEqual(join_price_share_context(self.task, self.control.price, changed), changed)

    def test_currency_or_fund_context_cannot_be_borrowed_from_nav(self):
        for field in ('currency', 'fund_id', 'observation_date', 'price_kind'):
            changed = copy.deepcopy(self.spec)
            changed['calculation_context'].pop(field)
            with self.subTest(field=field):
                item = self.candidate(spec=changed)
                self.assertNotIn('share_class', item['calculation_context'])

    def test_wrong_spec_date_is_rejected_not_fixed_to_nav_date(self):
        from researchhelper.evidence import expand_api_value_reference, expand_api_context_reference
        expanded = expand_api_context_reference(self.task, self.control.price,
            expand_api_value_reference(self.task, self.control.price, self.spec))
        expanded['period'] = '2026-10-20'
        with self.assertRaises(ContractError):
            join_price_share_context(self.task, self.control.price, expanded)

    def test_legacy_historical_document_and_inactive_tasks_keep_missing_field(self):
        legacy = copy.deepcopy(self.task)
        legacy.pop(POLICY)
        self.assertNotIn('share_class', self.candidate(legacy)['calculation_context'])
        for changes in [dict(research_mode='historical'), dict(intent='原件内容核对'), dict(run_mode='offline')]:
            task = dict(self.task, **changes)
            self.assertEqual(join_price_share_context(task, self.control.price, self.spec), self.spec)

    def test_unsupported_policy_is_not_a_silent_opt_in(self):
        for version in (True, 2):
            with self.subTest(version=version), self.assertRaises(ContractError):
                join_price_share_context(dict(self.task, **{POLICY: version}), self.control.price, self.spec)

    def test_model_and_host_share_identical_join_rules(self):
        app = self.candidate()
        skill = self.candidate(dict(self.task, run_mode='host'))
        self.assertEqual(app, skill)

    def test_model_cannot_forge_internal_join_audit_record(self):
        from researchhelper.reasoning import validate_model_specs
        spec = copy.deepcopy(self.spec)
        spec['calculation_context_joins'] = [dict(financial_approval=True)]
        with self.assertRaises(ContractError):
            validate_model_specs(self.task, [spec])

    def test_policy_is_part_of_input_fingerprint(self):
        task = copy.deepcopy(self.task)
        task.pop(POLICY)
        self.assertNotEqual(input_fingerprint(task), input_fingerprint(self.task))


if __name__ == '__main__':
    unittest.main()
