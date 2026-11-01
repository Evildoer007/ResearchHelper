"""A known failed source shard stops work without faking complete coverage."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests import accept_argument_support_runtime as acceptance


class SourceFailureStopTests(unittest.TestCase):
    def exercise(self, returncode, reported_count):
        modules = ['test_analysis_segment_citations', 'test_task_read_snapshot']
        with tempfile.TemporaryDirectory(prefix='source-stop-',
                dir=acceptance.ROOT / 'result/acceptance') as folder:
            root = Path(folder)
            (root / 'result/acceptance').mkdir(parents=True)
            command = [sys.executable, '-B', '-m', 'unittest', *('tests.' + name for name in modules)]

            def dispatch():
                result = subprocess.run(command, capture_output=True, text=True)
                return {'status': 'scoped_passed' if result.returncode == 0 else 'failed'}

            simulated = subprocess.CompletedProcess(command, returncode, '',
                f'Ran {reported_count} tests in 0.001s\n' + ('FAILED (errors=1)' if returncode else 'OK'))
            with patch.object(acceptance, 'ROOT', root), \
                    patch.object(acceptance, 'MODULES', modules), \
                    patch.object(acceptance, 'TEST_COUNT', 28), \
                    patch.object(acceptance, 'ISOLATED_MODULES', tuple(modules)), \
                    patch.object(acceptance.previous, 'run', side_effect=dispatch), \
                    patch.object(subprocess, 'run', return_value=simulated) as launch:
                result = acceptance.run()
            receipts = list((root / 'result/acceptance').glob('argument-support-runtime-*/receipt.json'))
            self.assertEqual(len(receipts), 1)
            record = json.loads(receipts[0].read_text())
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(record['status'], 'failed')
            self.assertEqual(record['tests_expected'], 28)
            self.assertEqual(len(record['source_shards']), 1)
            self.assertEqual(launch.call_count, 1)
            self.assertLess(record['source_shards'][0]['tests_run'], 28)
            self.assertEqual(record['source_shards'][0]['tests_run'], reported_count)
            self.assertFalse(record['app_installed'])

    def test_failing_shard_stops_remaining_work_and_keeps_failure(self):
        self.exercise(returncode=1, reported_count=4)

    def test_wrong_case_count_stops_even_if_subprocess_claims_success(self):
        self.exercise(returncode=0, reported_count=0)
