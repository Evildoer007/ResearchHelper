"""Independent native-market harness checks; no UI, credentials or networking."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests import accept_native_app_market_report as probe
from tests.test_current_live_market_acceptance_offline import numeric_fixture


class NativeMarketReportGuards(unittest.TestCase):
    def test_native_selection_required_before_runtime(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(probe, 'ActualNativeApp') as app:
            with self.assertRaises(RuntimeError):
                probe.run('FAKE_NEVER_SENT')
            app.assert_not_called()

    def test_exact_candidate_required(self):
        with patch.dict(os.environ, {'RESEARCHHELPER_ALLOW_VISIBLE_ACCEPTANCE': '1'}, clear=True):
            with self.assertRaises(RuntimeError):
                probe.select_candidate()

    def test_registered_same_candidate_cannot_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = root / 'result/acceptance/actual-native-market-report-fixture/receipt.json'
            receipt.parent.mkdir(parents=True)
            selected = str((root / 'candidate.json').resolve())
            receipt.write_text(json.dumps({'candidate': selected, 'case': probe.CASE, 'status': 'failed'}))
            with patch.object(probe, 'ROOT', root), patch.dict(os.environ,
                    {'RESEARCHHELPER_ALLOW_VISIBLE_ACCEPTANCE': '1',
                     'RESEARCHHELPER_CURRENT_RECEIPT': selected}, clear=True):
                with self.assertRaises(RuntimeError):
                    probe.select_candidate()

    def test_missing_credential_is_rejected_before_runtime(self):
        with patch.object(probe, 'select_candidate', return_value=Path('/explicit/candidate.json')):
            with patch.object(probe, 'ActualNativeApp') as app:
                with self.assertRaises(ValueError):
                    probe.run('')
                app.assert_not_called()

    def test_original_cells_validate_registered_price_change(self):
        self.assertEqual(len(probe.verified_price_change(numeric_fixture())), 1)

    def test_counterfeit_evidence_or_company_scope_rejected(self):
        fixture = numeric_fixture()
        for location in ('value', 'unit', 'applicability_check'):
            changed = copy.deepcopy(fixture)
            changed['evidence'][0][location] = {'value': 4.8, 'unit': '%', 'applicability_check': '待核验'}[location]
            with self.subTest(location=location), self.assertRaises(AssertionError):
                probe.verified_price_change(changed)
        changed = copy.deepcopy(fixture)
        changed['source_records'][0]['snapshot']['tables'][0]['thscode'] = '159919.SZ'
        with self.assertRaises(AssertionError):
            probe.verified_price_change(changed)

    def test_document_only_values_cannot_become_market_facts(self):
        changed = numeric_fixture()
        changed['evidence'][0]['usage_scope'] = 'document_content'
        with self.assertRaises(AssertionError):
            probe.verified_price_change(changed)

    def test_unregistered_numeric_result_is_not_repaired(self):
        changed = numeric_fixture()
        changed['calculations'][0]['output']['value'] = 25
        with self.assertRaises(AssertionError):
            probe.verified_price_change(changed)


if __name__ == '__main__':
    unittest.main()
