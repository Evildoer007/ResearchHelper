"""Independent checker and no-retry guards for two-object actual-App acceptance."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.accept_current_live_market_comparison import (
    AUTHORIZATION, CASE, CODES, ROOT, run, selected_candidate, verified_comparison,
)
from tests.test_current_live_market_acceptance_offline import numeric_fixture


def comparison_fixture():
    first = numeric_fixture()
    second = copy.deepcopy(first)
    second['source_records'][0]['id'] = 'second_api'
    second['source_records'][0]['snapshot']['tables'][0]['thscode'] = CODES[1]
    for index, item in enumerate(second['evidence']):
        item['id'] = 'second_' + str(index); item['artifact_id'] = 'second_api'
    calculation = second['calculations'][0]
    calculation['id'] = 'second_change'
    calculation['input_bindings'] = {'start_price': 'second_0', 'end_price': 'second_1'}
    return {key: first[key] + second[key] for key in ('source_records', 'evidence', 'calculations')}


class ComparisonAcceptance(unittest.TestCase):
    def test_two_original_windows_are_required(self):
        result = verified_comparison(comparison_fixture())
        self.assertEqual(set(result['objects']), set(CODES))
        self.assertAlmostEqual(result['difference_percentage_points'], 0)

    def test_one_security_is_not_a_comparison(self):
        with self.assertRaises(AssertionError): verified_comparison(numeric_fixture())

    def test_cross_security_endpoints_are_rejected(self):
        fixture = comparison_fixture()
        fixture['calculations'][0]['input_bindings']['end_price'] = 'second_1'
        with self.assertRaises(AssertionError): verified_comparison(fixture)

    def test_mutated_values_dates_units_and_scope_are_rejected(self):
        for mutate in (
            lambda task: task['calculations'][1]['output'].update(value=99),
            lambda task: task['evidence'][2].update(value=99),
            lambda task: task['evidence'][2].update(unit='港元'),
            lambda task: task['evidence'][2].update(nature='预测'),
            lambda task: task['evidence'][2].update(usage_scope='document_content'),
            lambda task: task['evidence'][2].update(applicability_check='待核验'),
            lambda task: task['source_records'][1]['snapshot']['tables'][0]['time'].__setitem__(0, '2026-09-22'),
        ):
            with self.subTest(mutate=mutate):
                fixture = comparison_fixture(); mutate(fixture)
                with self.assertRaises(AssertionError): verified_comparison(fixture)

    def test_duplicate_code_cannot_replace_second_security(self):
        fixture = comparison_fixture()
        fixture['source_records'][1]['snapshot']['tables'][0]['thscode'] = CODES[0]
        with self.assertRaises(AssertionError): verified_comparison(fixture)

    def test_missing_authorization_or_secret_never_opens_runtime(self):
        with patch.dict(os.environ, {}, clear=True), patch('tests.accept_current_live_market_comparison.AuthorizedRuntime') as runtime:
            with self.assertRaises(RuntimeError): run('FAKE_NEVER_SENT')
            runtime.assert_not_called()
        with patch('tests.accept_current_live_market_comparison.selected_candidate', return_value=Path('/fake')), patch('tests.accept_current_live_market_comparison.AuthorizedRuntime') as runtime:
            with self.assertRaises(ValueError): run('')
            runtime.assert_not_called()

    def test_closed_case_cannot_be_dispatched_on_another_candidate(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'result/acceptance') as directory:
            root = Path(directory); receipt = root / 'result/acceptance/live-market-comparison-existing/receipt.json'
            receipt.parent.mkdir(parents=True); receipt.write_text(json.dumps({'case': CASE, 'status': 'failed'}))
            with patch('tests.accept_current_live_market_comparison.ROOT', root), patch.dict(os.environ, {
                'RESEARCHHELPER_AUTHORIZED_LIVE_CASE': AUTHORIZATION,
                'RESEARCHHELPER_CURRENT_RECEIPT': '/new-candidate/candidate.json'}, clear=True):
                with self.assertRaises(RuntimeError): selected_candidate()


if __name__ == '__main__':
    unittest.main()
