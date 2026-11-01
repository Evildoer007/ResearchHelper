"""Native acceptance checks the current console, including omitted unlocked flags."""
import copy
import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
from tests.accept_installed_research_workflow import desktop_state, InstalledApp


class NativeSessionGateTests(unittest.TestCase):
    def session(self, **changes):
        return dict(kCGSSessionUserIDKey=501, kCGSSessionOnConsoleKey=True,
                    kCGSessionLoginDoneKey=True, **changes)

    def state(self, *sessions):
        return desktop_state([dict(IOConsoleUsers=list(sessions))], 501)

    def test_unlocked_current_console_does_not_require_a_false_lock_property(self):
        session = self.session(); before = copy.deepcopy(session)
        self.assertEqual(self.state(session), dict(known=True,locked=False,interactive=True))
        self.assertEqual(session, before)
        self.assertEqual(self.state(self.session(CGSSessionScreenIsLocked=False)), dict(known=True,locked=False,interactive=True))

    def test_explicit_lock_is_respected(self):
        self.assertEqual(self.state(self.session(CGSSessionScreenIsLocked=True)), dict(known=True,locked=True,interactive=False))

    def test_other_users_and_off_console_sessions_do_not_supply_permission(self):
        for changes in (dict(kCGSSessionUserIDKey=502), dict(kCGSSessionOnConsoleKey=False),
                        dict(kCGSessionLoginDoneKey=False), dict(kCGSSessionUserIDKey=True)):
            item=self.session(); item.update(changes)
            with self.subTest(changes=changes): self.assertFalse(self.state(item)['interactive'])

    def test_a_locked_background_user_does_not_override_current_console(self):
        other=self.session(CGSSessionScreenIsLocked=True);other['kCGSSessionUserIDKey']=502
        self.assertTrue(self.state(self.session(), other)['interactive'])

    def test_ambiguous_missing_or_malformed_session_is_unknown(self):
        for sessions in ([], [self.session(),self.session()], [self.session(CGSSessionScreenIsLocked='false')], [{}]):
            with self.subTest(sessions=sessions):
                self.assertEqual(self.state(*sessions), dict(known=False,locked=None,interactive=False))


class NativeWorkspaceReadinessTests(unittest.TestCase):
    def navigation(self):
        return dict(items=[dict(role='AXCheckBox', title=title, description='', actions=['AXPress'])
                           for title in ('ResChat','ResDesk')])

    def test_restored_desk_with_no_visible_composer_is_ready(self):
        app=InstalledApp(Path('.'),{},None)
        app.control=Mock(return_value=self.navigation())
        app.login()
        app.control.assert_called_once_with('snapshot')

    def test_login_is_sent_once_and_pressed_modes_are_not_plain_ax_buttons(self):
        app=InstalledApp(Path('.'),{},None)
        snapshots=iter([dict(items=[dict(title='登录',description='')]),self.navigation()])
        app.control=Mock(side_effect=lambda command,*args:next(snapshots) if command=='snapshot' else {})
        app.login()
        self.assertEqual(sum(call.args==('press','登录') for call in app.control.call_args_list),1)



class NativeCandidateIdentityTests(unittest.TestCase):
    def setUp(self):
        from tests import accept_installed_research_workflow as workflow
        self.workflow=workflow
        folder=tempfile.TemporaryDirectory(prefix='rh-native-candidate-test-')
        self.addCleanup(folder.cleanup)
        self.output=Path(folder.name)/'acceptance'
        self.candidate=Path(folder.name)/'selected/candidate.json'
        replacements=dict(CANDIDATE=self.candidate,OUTPUT=self.output,
            recorded_manifest=Mock(return_value={'files':{'Contents/example':'fixture'}}),
            verify_bundle=Mock(return_value={'Contents/example':'fixture'}),signature=Mock(),
            ModelSettings=Mock(return_value=Mock(read=Mock(return_value={'model':'deepseek-flash','has_key':True}))),
            DataSettings=Mock(return_value=Mock(read=Mock(return_value={'ifind':{'fixture_configured':True}}))),
            stored_tasks=Mock(return_value=[]))
        for name,value in replacements.items():
            helper=patch.object(workflow,name,value);helper.start();self.addCleanup(helper.stop)

    def test_absent_selection_has_no_dated_fallback(self):
        self.assertIsNone(self.workflow.candidate_from_environment({}))
        self.assertIsNone(self.workflow.candidate_from_environment({'RESEARCHHELPER_ACCEPT_CANDIDATE':''}))

    def test_explicit_acceptance_selection_takes_priority(self):
        result=self.workflow.candidate_from_environment(dict(RESEARCHHELPER_ACCEPT_CANDIDATE=str(self.candidate),
            RESEARCHHELPER_CURRENT_RECEIPT=str(self.candidate.parent/'another.json')))
        self.assertEqual(result,self.candidate)

    def test_build_selected_receipt_is_the_only_fallback(self):
        for value in ({'RESEARCHHELPER_CURRENT_RECEIPT':str(self.candidate)},
                      {'RESEARCHHELPER_CURRENT_RECEIPT':str(self.candidate),'RESEARCHHELPER_ACCEPT_CANDIDATE':''}):
            with self.subTest(value=value):self.assertEqual(self.workflow.candidate_from_environment(value),self.candidate)

    def test_missing_selection_stops_before_store_or_window_access(self):
        self.workflow.CANDIDATE=None
        with self.assertRaisesRegex(RuntimeError,'未选择明确候选'):self.workflow.preflight()
        for name in ('recorded_manifest','verify_bundle','signature','ModelSettings','DataSettings','stored_tasks'):
            getattr(self.workflow,name).assert_not_called()
        self.assertFalse(self.output.exists())

    def test_preflight_records_the_exact_manifest_checked_selection(self):
        result=self.workflow.preflight()
        self.assertEqual(result['status'],'preflight_passed')
        self.assertEqual(result['candidate'],str(self.candidate))
        self.workflow.recorded_manifest.assert_called_once_with(self.candidate)
        self.workflow.verify_bundle.assert_called_once_with(self.workflow.BUNDLE,self.workflow.recorded_manifest.return_value)
        self.assertEqual((result['model_calls'],result['data_requests']),(0,0))
        self.assertFalse(result['real_task_started']);self.assertFalse(result['native_window_opened'])
        self.assertEqual(json.loads((self.output/'preflight.json').read_text()),result)

    def test_invalid_manifest_cannot_read_model_or_source_configuration(self):
        self.workflow.recorded_manifest.side_effect=RuntimeError('Invalid release identity')
        with self.assertRaisesRegex(RuntimeError,'Invalid release identity'):self.workflow.preflight()
        for name in ('verify_bundle','signature','ModelSettings','DataSettings','stored_tasks'):
            getattr(self.workflow,name).assert_not_called()
        self.assertFalse((self.output/'preflight.json').exists())

    def test_mismatched_installation_cannot_read_research_or_configuration(self):
        self.workflow.verify_bundle.side_effect=RuntimeError('Mismatched installation')
        with self.assertRaisesRegex(RuntimeError,'Mismatched installation'):self.workflow.preflight()
        for name in ('signature','ModelSettings','DataSettings','stored_tasks'):
            getattr(self.workflow,name).assert_not_called()
        self.assertFalse((self.output/'preflight.json').exists())

    def test_report_export_also_checks_identity_before_reading_tasks(self):
        from tests import accept_installed_report_delivery as delivery
        for selected in (None,self.candidate):
            with self.subTest(selected=selected),patch.object(delivery,'CANDIDATE',selected),\
                 patch.object(delivery,'recorded_manifest',side_effect=RuntimeError('Invalid export identity')) as manifest,\
                 patch.object(delivery,'task') as task,patch.object(delivery,'signature') as signature:
                app=Mock()
                with self.assertRaises(RuntimeError):delivery.run_exports(app_factory=app)
                task.assert_not_called();signature.assert_not_called();app.assert_not_called()
                if selected is None:manifest.assert_not_called()
                else:manifest.assert_called_once_with(selected)

    def test_a_new_build_does_not_reset_the_single_case_spending_journal(self):
        self.output.mkdir()
        (self.output/'session.json').write_text(json.dumps({'paid_submission_started':True}))
        with patch.object(self.workflow,'InstalledApp') as app:
            with self.assertRaisesRegex(RuntimeError,'already sent once'):
                self.workflow.run_live(confirmed_single_case=True)
            app.assert_not_called()
        self.assertTrue(json.loads((self.output/'session.json').read_text())['paid_submission_started'])


class NativeQuietWindowResultTests(unittest.TestCase):
    def export(self,launched=False):
        from tests import accept_installed_report_delivery as delivery
        from tests.accept_installed_research_workflow import NativeDesktopBusy
        folder=tempfile.TemporaryDirectory(prefix='rh-native-quiet-test-');self.addCleanup(folder.cleanup)
        output=Path(folder.name)/'qa';reports=Path(folder.name)/'reports'
        app=Mock();app.process=Mock() if launched else None
        app.launch.side_effect=NativeDesktopBusy('Desktop in use; test App was not launched')
        replacements=dict(CANDIDATE=Path(folder.name)/'candidate.json',OUTPUT=output,REPORTS=reports,
            recorded_manifest=Mock(return_value={}),verify_bundle=Mock(),signature=Mock(),
            task=Mock(return_value={'ledger':{'used':{}}}),desktop_session=Mock(return_value={'interactive':True}))
        for name,value in replacements.items():
            helper=patch.object(delivery,name,value);helper.start();self.addCleanup(helper.stop)
        return delivery,app,output

    def test_busy_before_launch_is_deferred_and_does_not_call_login(self):
        delivery,app,output=self.export()
        record=delivery.run_exports(app_factory=Mock(return_value=app))
        self.assertEqual(record['status'],'deferred_while_desktop_in_use')
        self.assertFalse(record['foreground_opened']);self.assertFalse(record['native_window_opened'])
        self.assertEqual(record['exports'],[]);self.assertTrue(record['usage_unchanged'])
        app.login.assert_not_called();app.close.assert_called_once()
        self.assertEqual(json.loads((output/'receipt.json').read_text())['status'],record['status'])

    def test_a_busy_marker_after_launch_cannot_hide_an_actual_failure(self):
        from tests.accept_installed_research_workflow import NativeDesktopBusy
        delivery,app,output=self.export(launched=True)
        with self.assertRaises(NativeDesktopBusy):delivery.run_exports(app_factory=Mock(return_value=app))
        self.assertEqual(json.loads((output/'receipt.json').read_text())['status'],'failed')
        app.close.assert_called_once()


if __name__=='__main__':unittest.main()
