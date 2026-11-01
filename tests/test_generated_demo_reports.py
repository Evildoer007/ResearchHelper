"""Generated file demos must carry frozen reports after checkpoint release."""
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT


class GeneratedDemoReportTests(unittest.TestCase):
    def test_all_eight_file_cases_include_saved_report_without_pending_copies(self):
        sys.path.insert(0, str(ROOT/'packaging/demo'))
        self.addCleanup(lambda: sys.path.remove(str(ROOT/'packaging/demo')))
        from build_demo import demo
        with tempfile.TemporaryDirectory(prefix='generated-demo-') as folder, \
                patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline only')):
            target = demo(Path(folder))
            source = (target/'public/demo-data.js').read_text()
            payload = json.loads(source.removeprefix('window.RH_DEMO = ').removesuffix(';'))
            self.assertEqual(len(payload['tasks']), 8)
            for task in payload['tasks']:
                self.assertEqual(task['status'], '已完成')
                self.assertTrue(task['synthetic'])
                self.assertTrue(task['delivery']['digest'])
                self.assertIn('<!doctype html>', task['report_html'])
                self.assertIn("id='main'", task['report_html'])
                self.assertNotIn('pending_content', task)
                self.assertNotIn('pending_html', task)


if __name__ == '__main__':
    unittest.main()
