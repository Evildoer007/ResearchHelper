"""Build ordering, failed acceptance, isolation and actual offline report flow."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json,os,sys,threading,unittest
from tests import ROOT
sys.path.insert(0,str(ROOT/'packaging'))
import build_current as delivery
import accept_current as acceptance

class BuildThenAcceptTests(unittest.TestCase):
    def test_build_then_accept_then_update_skill(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);payload=root/'result/build-candidates/current';payload.mkdir(parents=True)
            report=root/'acceptance.json';report.write_text('{}');events=[]
            def build(*args,**kwargs):
                self.assertFalse(kwargs['install_skill']);events.append('build');return {'payload':str(payload)}
            def accept(*args):events.append('accept');return {'status':'scoped_passed','receipt':str(report)}
            def install(*args):events.append('skill');return {'status':'installed'}
            with patch.object(delivery,'build_current',side_effect=build),patch.object(acceptance,'accept_current',side_effect=accept),patch.object(delivery,'update_project_skill',side_effect=install),patch.object(delivery,'verify_current_build',return_value={'status':'built_local_candidate'}):
                result=delivery.build_and_accept(root)
            self.assertEqual(events,['build','accept','skill']);self.assertEqual(result['postbuild_acceptance']['status'],'scoped_passed')
    def test_acceptance_failure_never_updates_skill(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            with patch.object(delivery,'build_current',return_value={'payload':str(root)}),patch.object(acceptance,'accept_current',side_effect=RuntimeError('report failed')),patch.object(delivery,'update_project_skill') as install:
                with self.assertRaisesRegex(RuntimeError,'report failed'):delivery.build_and_accept(root)
                install.assert_not_called()
    def test_package_failure_closes_runtime_and_preserves_failed_receipt(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);(root/'result/acceptance').mkdir(parents=True);selected=root/'result/build-candidates/current/candidate.json';selected.parent.mkdir(parents=True)
            candidate={'platform':'macos','manifest_sha256':'a','payload':str(selected.parent)};stopped=[]
            class FakeRuntime:
                def __init__(self,workspace,record):self.mount=workspace
                def start(self):return 'http://127.0.0.1:1/'
                def stop(self):stopped.append(True)
            prior=os.environ.get('RESEARCHHELPER_CURRENT_RECEIPT')
            with patch.object(acceptance,'verify_current_build',return_value=candidate),patch('tests.accept_native_workbench_render.RenderRuntime',FakeRuntime),patch.object(acceptance,'accept_package',side_effect=RuntimeError('bad package')),patch.object(acceptance,'accept_app_reports') as app,patch.object(acceptance,'accept_skill_reports') as skill:
                with self.assertRaisesRegex(RuntimeError,'bad package'):acceptance.accept_current(selected,root)
                app.assert_not_called();skill.assert_not_called()
            self.assertEqual(stopped,[True]);self.assertEqual(os.environ.get('RESEARCHHELPER_CURRENT_RECEIPT'),prior)
            receipt=next((root/'result/acceptance').glob('postbuild-*/receipt.json'));self.assertEqual(json.loads(receipt.read_text())['status'],'failed')
    def test_isolated_python_resolves_this_checkout_acceptance_modules(self):
        import subprocess
        program = (f"import sys,importlib.util;sys.path.insert(0,{str(ROOT / 'packaging')!r});"
                   "import build_current;"
                   f"assert importlib.util.find_spec('tests.accept_native_workbench_render').origin=={str(ROOT / 'tests/accept_native_workbench_render.py')!r}")
        subprocess.run([sys.executable,'-I','-B','-c',program],check=True,capture_output=True,text=True,timeout=15)
    def test_unknown_or_empty_report_document_is_rejected(self):
        for source in ('<main></main>','<main></main><main></main>'):
            with self.assertRaises(RuntimeError):acceptance.document(source,'standard')
    def test_skill_worker_is_valid_python_and_uses_shipped_cases(self):
        compile(acceptance.SKILL_WORKER,'skill-acceptance-worker','exec')
        self.assertNotIn('one-sentence-research',acceptance.SKILL_WORKER)
        self.assertNotIn('DataSettings',acceptance.SKILL_WORKER)
    def test_all_app_cases_and_formats_with_an_isolated_offline_service(self):
        from researchhelper.service import make_server
        with TemporaryDirectory() as folder:
            server=make_server(Path(folder)/'store',ROOT,offline=True,authentication=True,runtime_configurable=True,preset_accounts=True)
            worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            try:
                rows=acceptance.accept_app_reports('http://127.0.0.1:'+str(server.server_port))
                self.assertEqual([r['case'] for r in rows],list(acceptance.CASES));self.assertTrue(all(len(r['exports'])==10 for r in rows))
            finally:
                server.shutdown();server.server_close();worker.join(timeout=3)
            self.assertFalse(worker.is_alive())

if __name__=='__main__':unittest.main()
