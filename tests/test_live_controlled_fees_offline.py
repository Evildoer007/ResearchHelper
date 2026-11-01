"""Checker-only fixtures and single-use guard. Not approved backend evidence."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests import accept_live_controlled_fee_comparison as probe


class ControlledFeeCheckerTests(unittest.TestCase):
    def setUp(self):
        evidence = []
        refs = {}
        for field, token in probe.RATES.items():
            side = '甲' if field.endswith('_a') else '乙'
            refs[field] = 'ev_' + field
            evidence.append({'id': refs[field], 'task_id': 'checker_only', 'research_version': 1,
                'object': '样例ETF' + side, 'raw_value': float(token), 'value': float(token),
                'raw_unit': '%/年', 'unit': '%/年', 'conversion_factor': 1,
                'source_check': '通过', 'applicability_check': '通过',
                'calculation_context': {'fund_id': '样例ETF' + side, 'share_class': '样例ETF' + side + 'A份额',
                    'fee_basis': 'net_assets', 'rate_period': 'annual', 'period': '2026年度规则',
                    'metric': 'management_fee' if field.startswith('management') else 'custody_fee'}})
        self.task = {'id': 'checker_only', 'version': 1, 'question': probe.QUESTION,
            'entry': 'ETF', 'intent': '比较对象', 'status': '已完成', 'source_records': [],
            'evidence': evidence, 'calculations': [{'id': 'calc1', 'method': probe.METHOD,
                'task_id': 'checker_only', 'research_version': 1,
                'parameters': {'unit': '%/年', 'period': '2026年度规则'},
                'input_refs': list(refs.values()), 'input_bindings': refs,
                'inputs': {key: float(value) for key, value in probe.RATES.items()},
                'value': -.21, 'unit': '百分点/年'}],
            'questions': [{'id': 'q1', 'question': probe.QUESTION, 'required': True,
                'status': '已回答', 'calculation_refs': ['calc1'], 'refs': list(refs.values())}]}
        self.report = '合成材料：样例ETF甲与样例ETF乙，非实际投资产品。'

    def test_checker_is_explicitly_not_investment_quality(self):
        value = probe.verify_comparison(self.task, self.report)
        self.assertEqual(value['expected_difference'], '-0.21')
        self.assertTrue(value['synthetic_only'])
        self.assertFalse(value['independent_investment_quality'])

    def test_wrong_quantity_units_or_owner_cannot_pass(self):
        for key, value in (('raw_value', .9), ('unit', '%'), ('source_check', '待核验'),
                           ('task_id', 'another'), ('research_version', 2)):
            task = copy.deepcopy(self.task)
            task['evidence'][0][key] = value
            with self.subTest(key=key), self.assertRaises(AssertionError):
                probe.verify_comparison(task, self.report)

    def test_swapped_fund_metric_or_scope_is_rejected(self):
        for key, value in (('fund_id', '样例ETF乙'), ('metric', 'custody_fee'),
                           ('share_class', '另一份额'), ('period', '2025年度规则'), ('fee_basis', 'subscription_amount')):
            task = copy.deepcopy(self.task)
            task['evidence'][0]['calculation_context'][key] = value
            with self.subTest(key=key), self.assertRaises(AssertionError):
                probe.verify_comparison(task, self.report)

    def test_wrong_difference_or_unexecuted_comparison_is_rejected(self):
        for key, value in (('value', .21), ('method', 'invented'), ('unit', '%/年')):
            task = copy.deepcopy(self.task)
            task['calculations'][0][key] = value
            with self.subTest(key=key), self.assertRaises(AssertionError):
                probe.verify_comparison(task, self.report)

    def test_missing_required_answer_reference_or_synthetic_label_is_rejected(self):
        task = copy.deepcopy(self.task)
        task['questions'][0]['calculation_refs'] = []
        with self.assertRaises(AssertionError):
            probe.verify_comparison(task, self.report)
        with self.assertRaises(AssertionError):
            probe.verify_comparison(self.task, self.report.replace('合成', '真实'))

    def test_missing_scope_never_reads_private_configuration(self):
        with patch.dict('os.environ', {}, clear=True), self.assertRaises(RuntimeError):
            probe.selected_candidate()

    def test_closed_case_cannot_be_reopened_under_new_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            closed = root / 'result/acceptance/live-controlled-fees-closed'
            closed.mkdir(parents=True)
            (closed / 'receipt.json').write_text(json.dumps({'case': probe.CASE, 'status': 'failed'}))
            with patch.object(probe, 'ROOT', root), patch.dict('os.environ', {
                'RESEARCHHELPER_AUTHORIZED_LIVE_CASE': probe.AUTHORIZATION,
                'RESEARCHHELPER_CURRENT_RECEIPT': str(root / 'new-candidate.json')}, clear=True), self.assertRaises(RuntimeError):
                probe.selected_candidate()


if __name__ == '__main__':
    unittest.main()
