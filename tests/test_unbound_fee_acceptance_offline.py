"""Independent checker tests, not backend evidence or a live-case replay."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests import accept_live_unbound_fee_inputs as probe
from tests.test_live_controlled_fees_offline import ControlledFeeCheckerTests


class UnboundFeeCheckerTests(unittest.TestCase):
    def setUp(self):
        ControlledFeeCheckerTests.setUp(self)
        self.task['question'] = probe.QUESTION
        self.task['questions'][0]['question'] = probe.QUESTION
        calculation = self.task['calculations'][0]
        calculation['value'] = -.13
        for item in self.task['evidence']:
            field = next(field for field, ref in calculation['input_bindings'].items() if ref == item['id'])
            item.update(raw_value=float(probe.RATES[field]), value=float(probe.RATES[field]),
                        raw_unit='%', unit='%')
            item['calculation_context_basis'] = {name: {} for name in item['calculation_context']}
            calculation['inputs'][field] = float(probe.RATES[field])

    def test_valid_percent_with_independent_annual_frequency(self):
        result = probe.verify_comparison(self.task, self.report)
        self.assertEqual(result['expected_difference'], '-0.13')
        self.assertTrue(result['raw_unit_preserved'])
        self.assertTrue(result['annual_frequency_independently_anchored'])
        self.assertFalse(result['independent_investment_quality'])

    def test_quantity_unit_owner_and_approval_changes_fail(self):
        for key, value in [('value', .9), ('raw_value', .9), ('unit', '%/年'),
                           ('task_id', 'another'), ('research_version', 2), ('source_check', '待核验')]:
            task = deepcopy(self.task)
            task['evidence'][0][key] = value
            with self.subTest(key=key), self.assertRaises(AssertionError):
                probe.verify_comparison(task, self.report)

    def test_missing_or_different_annual_context_fails(self):
        for key, value in [('rate_period', 'monthly'), ('fee_basis', 'assets'),
                           ('fund_id', '样例ETF乙'), ('metric', 'custody_fee'),
                           ('share_class', '其他份额'), ('period', '2025年度规则')]:
            task = deepcopy(self.task)
            task['evidence'][0]['calculation_context'][key] = value
            with self.subTest(key=key), self.assertRaises(AssertionError):
                probe.verify_comparison(task, self.report)
        task = deepcopy(self.task)
        task['evidence'][0]['calculation_context_basis'].pop('rate_period')
        with self.assertRaises(AssertionError):
            probe.verify_comparison(task, self.report)

    def test_unexecuted_wrong_or_duplicate_input_calculation_fails(self):
        for key, value in [('value', .13), ('method', 'fee_difference_v1'), ('unit', '%/年')]:
            task = deepcopy(self.task)
            task['calculations'][0][key] = value
            with self.subTest(key=key), self.assertRaises(AssertionError):
                probe.verify_comparison(task, self.report)
        task = deepcopy(self.task)
        task['calculations'][0]['input_bindings']['custody_a'] = 'ev_management_a'
        with self.assertRaises(AssertionError):
            probe.verify_comparison(task, self.report)

    def test_partial_or_uncited_answer_never_counts_as_passed(self):
        for key, value in [('status', '未回答'), ('refs', []), ('calculation_refs', [])]:
            task = deepcopy(self.task)
            task['questions'][0][key] = value
            with self.subTest(key=key), self.assertRaises(AssertionError):
                probe.verify_comparison(task, self.report)
        with self.assertRaises(AssertionError):
            probe.verify_comparison(self.task, self.report.replace('合成', '真实'))

    def test_no_scope_does_not_access_credentials(self):
        with patch.dict('os.environ', {}, clear=True), self.assertRaises(RuntimeError):
            probe.selected_candidate()

    def test_closed_case_cannot_be_reopened_or_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / 'result/acceptance/live-controlled-fees-closed'
            folder.mkdir(parents=True)
            (folder / 'receipt.json').write_text(json.dumps({'case': probe.CASE, 'status': 'failed'}))
            with patch.object(probe, 'ROOT', root), patch.dict('os.environ', {
                    'RESEARCHHELPER_AUTHORIZED_LIVE_CASE': probe.AUTHORIZATION,
                    'RESEARCHHELPER_CURRENT_RECEIPT': str(root / 'new-candidate.json')}, clear=True), self.assertRaises(RuntimeError):
                probe.selected_candidate()


if __name__ == '__main__':
    unittest.main()
