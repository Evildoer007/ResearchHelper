"""Fresh scope guards, with the real executor replaced. No credentials read."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests import review_ifind_etf_calculation_revalidation as probe


class CalculationRevalidationTests(unittest.TestCase):
    def test_requires_literal_human_approval(self):
        with patch.object(probe.executor, 'run') as execute:
            for value in (False, None, 1, 'yes'):
                with self.assertRaises(ValueError):
                    probe.run(confirmed=value)
            execute.assert_not_called()

    def test_previously_dispatched_scope_is_not_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / 'authorization-dispatch.json').touch()
            with patch.object(probe, 'OUTPUT', path), patch.object(probe.executor, 'run') as execute:
                with self.assertRaises(ValueError):
                    probe.run(confirmed=True)
                execute.assert_not_called()

    def test_binds_current_release_without_changing_old_executor(self):
        original = probe.executor.OUTPUT, probe.executor.RELEASE
        with tempfile.TemporaryDirectory() as folder:
            def execute(*, confirmed):
                self.assertIs(confirmed, True)
                self.assertEqual(probe.executor.OUTPUT, Path(folder))
                self.assertEqual(probe.executor.RELEASE, probe.RELEASE)
                return {'status': 'offline fixture'}
            with patch.object(probe, 'OUTPUT', Path(folder)), patch.object(probe.executor, 'run', execute):
                self.assertEqual(probe.run(confirmed=True)['status'], 'offline fixture')
        self.assertEqual((probe.executor.OUTPUT, probe.executor.RELEASE), original)

    def test_executor_failure_restores_old_bindings(self):
        original = probe.executor.OUTPUT, probe.executor.RELEASE
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(probe, 'OUTPUT', Path(folder)), patch.object(probe.executor, 'run', side_effect=RuntimeError):
                with self.assertRaises(RuntimeError):
                    probe.run(confirmed=True)
        self.assertEqual((probe.executor.OUTPUT, probe.executor.RELEASE), original)
