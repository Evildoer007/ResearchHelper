"""Market acceptance guards and independent numeric checks; no real calls."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.accept_current_live_market_research import (
    AUTHORIZATION, CASE, run, selected_candidate, verified_price_change,
)


def numeric_fixture():
    source = {'id': 'api', 'source': 'ifind', 'operation': 'cmd_history_quotation',
              'snapshot': {'tables': [{'thscode': '510300.SH', 'time': ['2026-09-21', '2026-09-30'],
                                      'table': {'close': [4.0, 4.2]}}]}}
    evidence = [{'id': 'price_' + str(row), 'artifact_id': 'api',
                 'record_locator': {'table': 0, 'row': row, 'field': 'close'},
                 'value': price, 'unit': '元', 'source_check': '通过',
                 'applicability_check': '通过', 'nature': '已实现事实'}
                for row, price in enumerate([4.0, 4.2])]
    calculation = {'id': 'change', 'method': 'observed_price_return_v1',
                   'input_bindings': {'start_price': 'price_0', 'end_price': 'price_1'},
                   'inputs': {'start_price': 4.0, 'end_price': 4.2},
                   'output': {'value': (4.2 / 4.0 - 1) * 100}}
    return {'source_records': [source], 'evidence': evidence, 'calculations': [calculation]}


class LiveMarketEntryGuards(unittest.TestCase):
    def test_missing_authorization_never_opens_runtime(self):
        with patch.dict(os.environ, {}, clear=True), patch('tests.accept_current_live_market_research.AuthorizedRuntime') as runtime:
            with self.assertRaises(RuntimeError):
                run('FAKE_NEVER_SENT')
            runtime.assert_not_called()

    def test_missing_candidate_never_opens_runtime(self):
        with patch.dict(os.environ, {'RESEARCHHELPER_AUTHORIZED_LIVE_CASE': AUTHORIZATION}, clear=True), patch('tests.accept_current_live_market_research.AuthorizedRuntime') as runtime:
            with self.assertRaises(RuntimeError):
                run('FAKE_NEVER_SENT')
            runtime.assert_not_called()

    def test_registered_case_is_never_dispatched_again(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = root / 'result/acceptance/live-market-research-fixture/receipt.json'
            receipt.parent.mkdir(parents=True)
            candidate = (root / 'candidate.json').resolve()
            receipt.write_text(json.dumps({'candidate': str(candidate), 'case': CASE, 'status': 'executing'}))
            with patch('tests.accept_current_live_market_research.ROOT', root), patch.dict(os.environ, {
                    'RESEARCHHELPER_AUTHORIZED_LIVE_CASE': AUTHORIZATION,
                    'RESEARCHHELPER_CURRENT_RECEIPT': str(candidate)}, clear=True):
                with self.assertRaises(RuntimeError):
                    selected_candidate()

    def test_missing_supplier_secret_never_opens_runtime(self):
        with patch('tests.accept_current_live_market_research.selected_candidate', return_value=Path('/fake')), patch('tests.accept_current_live_market_research.AuthorizedRuntime') as runtime:
            with self.assertRaises(ValueError):
                run('')
            runtime.assert_not_called()

    def test_numeric_check_recomputes_original_cells(self):
        result = verified_price_change(numeric_fixture())
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0]['price_change_percent'], 5.0)

    def test_numeric_check_rejects_mutated_claims(self):
        for mutate in (
            lambda task: task['calculations'][0]['output'].update(value=99.0),
            lambda task: task['evidence'][0].update(value=3.0),
            lambda task: task['evidence'][0].update(source_check='待核验'),
            lambda task: task['evidence'][0].update(usage_scope='document_content'),
            lambda task: task['evidence'][0].update(nature='预测'),
            lambda task: task['source_records'][0]['snapshot']['tables'][0].update(thscode='510500.SH'),
            lambda task: task['source_records'][0]['snapshot']['tables'][0]['time'].__setitem__(0, '2026-09-22'),
        ):
            task = copy.deepcopy(numeric_fixture())
            mutate(task)
            with self.assertRaises(AssertionError):
                verified_price_change(task)


if __name__ == '__main__':
    unittest.main()
