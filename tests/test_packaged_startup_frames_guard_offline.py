"""No-window checks for the separate, explicitly authorized startup observer."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests import accept_packaged_startup_frames as acceptance


class StartupFramesGuardTests(unittest.TestCase):
    def test_without_current_authorization_nothing_is_created_or_launched(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(acceptance.tempfile, 'mkdtemp') as create, \
                patch.object(acceptance.subprocess, 'Popen') as launch:
            with self.assertRaisesRegex(RuntimeError, '明确授权'):
                acceptance.run()
            create.assert_not_called()
            launch.assert_not_called()

    def test_candidate_is_mandatory_even_when_authorization_is_present(self):
        with patch.dict(os.environ, {'RESEARCHHELPER_ALLOW_VISIBLE_ACCEPTANCE': '1'}, clear=True), \
                patch.object(acceptance.tempfile, 'mkdtemp') as create, \
                patch.object(acceptance.subprocess, 'Popen') as launch:
            with self.assertRaisesRegex(RuntimeError, '候选回执'):
                acceptance.run()
            create.assert_not_called()
            launch.assert_not_called()

    def test_inherited_truthy_values_do_not_count_as_explicit_authorization(self):
        for value in ('true', 'yes', '0', ''):
            with self.subTest(value=value), \
                    patch.dict(os.environ, {'RESEARCHHELPER_ALLOW_VISIBLE_ACCEPTANCE': value}, clear=True), \
                    patch.object(acceptance.subprocess, 'Popen') as launch:
                with self.assertRaisesRegex(RuntimeError, '明确授权'):
                    acceptance.run()
                launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
