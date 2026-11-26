"""An ambiguous AXPress may finish a save; only the exact file proves it."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tests.accept_native_app_report_flow import ActualNativeApp


class NativeExportCompletionTests(unittest.TestCase):
    def app(self, output, *, written='frozen report', error=None):
        app = ActualNativeApp(output, {}, {})
        state = {'items': [{'id': 'PathTextField', 'value': str(output.resolve())},
                           {'id': 'where popup', 'value': output.name}]}
        app.control = Mock(return_value=state)
        app.wait_control = Mock()
        app.observe = Mock()
        def press(label):
            if label != 'OKButton':
                return
            if written is not None:
                (output / 'test-export.html').write_text(written)
            if error:
                raise RuntimeError(error)
        app.press = Mock(side_effect=press)
        return app

    def test_exact_file_completes_once_when_ax_response_is_lost(self):
        with tempfile.TemporaryDirectory() as folder:
            app = self.app(Path(folder), error='native control: press: OKButton AX-25204')
            app.export_report('test', 'frozen report')
            self.assertTrue(app.record['native_save_action_response_lost'])
            self.assertIn('test_native_export_sha256', app.record)
            self.assertEqual(sum(call.args == ('OKButton',) for call in app.press.call_args_list), 1)

    def test_wrong_file_is_not_accepted_after_an_ambiguous_save(self):
        with tempfile.TemporaryDirectory() as folder:
            app = self.app(Path(folder), written='different', error='native control: press: OKButton AX-25204')
            with self.assertRaisesRegex(RuntimeError, 'exact frozen report'):
                app.export_report('test', 'frozen report')
            self.assertNotIn('test_native_export_sha256', app.record)

    def test_no_file_still_fails_and_never_repeats_save(self):
        with tempfile.TemporaryDirectory() as folder:
            app = self.app(Path(folder), written=None, error='native control: press: OKButton AX-25204')
            ticks = iter(range(100))
            with patch('tests.accept_native_app_report_flow.time.monotonic', side_effect=lambda: next(ticks)), \
                 patch('tests.accept_native_app_report_flow.time.sleep'):
                with self.assertRaisesRegex(RuntimeError, 'did not create a file'):
                    app.export_report('test', 'frozen report')
            self.assertEqual(sum(call.args == ('OKButton',) for call in app.press.call_args_list), 1)

    def test_other_accessibility_failures_are_not_swallowed(self):
        with tempfile.TemporaryDirectory() as folder:
            app = self.app(Path(folder), error='native control: press: OKButton AX-25205')
            with self.assertRaisesRegex(RuntimeError, 'AX-25205'):
                app.export_report('test', 'frozen report')
            self.assertNotIn('native_save_action_response_lost', app.record)

    def test_normal_save_keeps_its_existing_completion_check(self):
        with tempfile.TemporaryDirectory() as folder:
            app = self.app(Path(folder))
            app.export_report('test', 'frozen report')
            self.assertNotIn('native_save_action_response_lost', app.record)
            self.assertIn('test_native_export_sha256', app.record)


if __name__ == '__main__':
    unittest.main()
