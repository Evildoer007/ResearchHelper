"""Entry guards never read production credentials or perform real requests."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import review_requirement_contract_live_research as entry


class RequirementLiveEntryTests(unittest.TestCase):
    def test_dispatch_is_blocked_without_matching_source_gate(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(entry.gate, 'BASE', Path(folder)), \
                patch.object(entry.direct, 'run', side_effect=AssertionError('must not dispatch')):
            with self.assertRaises(ValueError):
                entry.source_candidate()

    def test_closed_run_cannot_be_redispatched(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'authorization-dispatch.json').write_text('{}')
            with patch.object(entry, 'OUTPUT', root), \
                    patch.object(entry, 'source_candidate', side_effect=AssertionError('must not create study')):
                with self.assertRaises(ValueError):
                    entry.run()

    def test_entry_uses_existing_finite_ledger_and_keeps_current_scope(self):
        from tests import review_authorized_ifind_etf as executor
        self.assertEqual(entry.direct.executor, executor)
        self.assertIn('不据此作买卖推荐', executor.QUESTION)
        self.assertNotEqual(entry.OUTPUT, entry.direct.OUTPUT)

    def test_numeric_format_study_cannot_redispatch_a_closed_run(self):
        from tests import review_numeric_contract_live_research as numeric
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'authorization-dispatch.json').write_text('{}')
            with patch.object(numeric, 'OUTPUT', root), patch.object(entry, 'source_candidate',
                    side_effect=AssertionError('must not reuse the study')):
                with self.assertRaises(ValueError):
                    numeric.run()
