"""A locked desktop must not launch Apps, dispatch UI input or look like failure."""
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
import tempfile
from unittest.mock import Mock,patch

from tests.accept_native_app_report_flow import ActualNativeApp,NativeSessionUnavailable


class NativeSessionPreflightTests(unittest.TestCase):
    def app(self,state):
        app=ActualNativeApp(Path('/unused'),{}, {})
        app.control=Mock(return_value=state)
        return app

    def test_interactive_console_permits_preflight(self):
        state=dict(session_available=True,screen_locked=False,on_console=True,interactive_console=True)
        app=self.app(state)
        self.assertEqual(app.require_interactive_console(),state)
        self.assertNotIn('native_input_unavailable',app.record)

    def test_locked_or_unknown_state_never_launches_app(self):
        for state in ({'screen_locked':True,'interactive_console':False},
                      {'on_console':False,'interactive_console':False}, {},
                      {'interactive_console':1},{'interactive_console':'true'}):
            with self.subTest(state=state):
                app=self.app(state)
                with patch('tests.accept_native_app_report_flow.subprocess.Popen') as process:
                    with self.assertRaises(NativeSessionUnavailable):app.launch()
                process.assert_not_called()
                self.assertTrue(app.record['native_input_unavailable'])
                self.assertEqual(app.record['desktop_session'],state)
                self.assertNotIn('actual_packaged_launcher',app.record)

    def test_locked_state_prevents_window_capture(self):
        app=self.app({'interactive_console':False})
        with patch('tests.accept_native_app_report_flow.subprocess.run') as command:
            with self.assertRaises(NativeSessionUnavailable):app.capture('unavailable')
        command.assert_not_called()

    def test_screen_lock_between_controls_is_not_an_app_failure(self):
        app=ActualNativeApp(Path('/unused'),{}, {})
        app.binary=Path('/unused/controller');app.process=SimpleNamespace(pid=123)
        response=SimpleNamespace(stdout=json.dumps({'desktop_unavailable':True}),returncode=1)
        with patch('tests.accept_native_app_report_flow.subprocess.run',return_value=response):
            with self.assertRaises(NativeSessionUnavailable):app.control('press','发送研究问题')

    def test_session_query_has_no_owned_pid_requirement(self):
        app=ActualNativeApp(Path('/unused'),{}, {})
        app.binary=Path('/unused/controller')
        response=SimpleNamespace(stdout=json.dumps({'interactive_console':False}),returncode=0)
        with patch('tests.accept_native_app_report_flow.subprocess.run',return_value=response) as command:
            self.assertEqual(app.control('session'),{'interactive_console':False})
        self.assertEqual(command.call_args.args[0],['/unused/controller','session'])


    def test_native_export_refuses_to_overwrite_existing_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary);target=output/'native-export.html'
            target.write_text('original frozen evidence')
            app=ActualNativeApp(output,{},{});app.control=Mock()
            with self.assertRaisesRegex(RuntimeError,'already exists'):
                app.export_report('native','different report')
            app.control.assert_not_called()
            self.assertEqual(target.read_text(),'original frozen evidence')

    def test_native_export_never_saves_to_unconfirmed_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            app = ActualNativeApp(output, {}, {})
            app.press = Mock(); app.wait_control = Mock(); app.observe = Mock()
            state = {'items': [{'id': 'PathTextField', 'value': str(output.resolve())},
                               {'id': 'where popup', 'value': 'different-directory'}]}
            app.control = Mock(side_effect=lambda command, *args: state if command == 'snapshot' else {'ok': True})
            ticks = iter(index * .25 for index in range(200))
            with patch('tests.accept_native_app_report_flow.time.monotonic', side_effect=lambda: next(ticks)), \
                 patch('tests.accept_native_app_report_flow.time.sleep'):
                with self.assertRaisesRegex(RuntimeError, 'did not reach the private acceptance folder'):
                    app.export_report('native', 'frozen report')
            self.assertFalse(any(call.args[:2] == ('set', 'saveAsNameTextField') for call in app.control.call_args_list))
            self.assertFalse(any(call.args == ('OKButton',) for call in app.press.call_args_list))
            self.assertFalse((output / 'native-export.html').exists())

    def test_native_export_rejects_path_input_that_did_not_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = ActualNativeApp(Path(temporary), {}, {})
            app.press = Mock(); app.wait_control = Mock(); app.observe = Mock()
            state = {'items': [{'id': 'PathTextField', 'value': '/unrelated-history'}]}
            app.control = Mock(side_effect=lambda command, *args: state if command == 'snapshot' else {'ok': True})
            with self.assertRaisesRegex(RuntimeError, 'did not retain the private directory'):
                app.export_report('native', 'frozen report')
            self.assertFalse(any(call.args == ('native-folder-confirm',) for call in app.control.call_args_list))
            self.assertFalse(any(call.args == ('OKButton',) for call in app.press.call_args_list))

    def test_native_export_does_not_accept_an_open_path_sheet_as_a_location(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            app = ActualNativeApp(output, {}, {})
            app.press = Mock(); app.wait_control = Mock(); app.observe = Mock()
            state = {'items': [{'id': 'PathTextField', 'value': str(output.resolve())},
                               {'id': 'where popup', 'value': output.name}, {'id': 'GoToWindow'}]}
            app.control = Mock(side_effect=lambda command, *args: state if command == 'snapshot' else {'ok': True})
            ticks = iter(index * .25 for index in range(200))
            with patch('tests.accept_native_app_report_flow.time.monotonic', side_effect=lambda: next(ticks)), \
                 patch('tests.accept_native_app_report_flow.time.sleep'):
                with self.assertRaisesRegex(RuntimeError, 'did not reach the private acceptance folder'):
                    app.export_report('native', 'frozen report')
            self.assertFalse(any(call.args == ('OKButton',) for call in app.press.call_args_list))


if __name__=='__main__':unittest.main()
