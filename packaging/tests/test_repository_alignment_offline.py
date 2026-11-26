"""Independent regressions for OH-inspired development/build conventions."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import selectors
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'packaging'))


def load(relative, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RepositoryAlignmentTests(unittest.TestCase):
    def test_preflight_failure_records_diagnostics_without_overwriting_python_choice(self):
        builder = load('packaging/build_all.py', 'alignment_preflight_failure')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            saved = root / '.researchhelper/runtime/build-python-path'
            saved.parent.mkdir(parents=True); saved.write_text('previous-choice\n')
            with patch.object(builder, 'verify_environment', side_effect=RuntimeError('synthetic missing dependency')),\
                 patch.object(builder, 'persist') as persist, redirect_stdout(io.StringIO()):
                with self.assertRaises(RuntimeError):
                    builder.build_candidate(root)
            persist.assert_not_called()
            receipt = next(root.glob('result/build-staging/*/candidate.json'))
            record = json.loads(receipt.read_text())
            self.assertEqual(record['phase'], 'preflight')
            self.assertEqual(record['status'], 'failed_not_published')
            self.assertIn('missing dependency', record['error_message'])
            self.assertIn('synthetic missing dependency', Path(record['log']).read_text())
            self.assertFalse((receipt.parent / 'source').exists())
            self.assertEqual(saved.read_text(), 'previous-choice\n')

    def test_cancel_is_distinct_from_failure_and_does_not_publish(self):
        builder = load('packaging/build_all.py', 'alignment_cancel_receipt')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(builder, 'verify_environment', return_value={'platform': 'windows'}),\
                 patch.object(builder, 'source_files', return_value=[]),\
                 patch.object(builder, 'freeze_sources', return_value={}), redirect_stdout(io.StringIO()):
                with self.assertRaises(KeyboardInterrupt):
                    builder.build_candidate(root, runner=lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()))
            record = json.loads(next(root.glob('result/build-staging/*/candidate.json')).read_text())
            self.assertEqual(record['status'], 'cancelled_not_published')
            self.assertFalse(record['published'])
            self.assertFalse((root / 'dist').exists())

    def test_real_subprocess_streams_only_stages_but_keeps_complete_log(self):
        from build_progress import run_logged
        log, console = io.StringIO(), io.StringIO()
        stages = []
        with redirect_stdout(console):
            run_logged([sys.executable, '-B', '-u', '-c', "print('[3/7] fixture');print('compiler detail')"],
                       cwd=ROOT, stdout=log, on_progress=stages.append)
        self.assertEqual(stages, ['[3/7] fixture'])
        self.assertIn('compiler detail', log.getvalue())
        self.assertNotIn('compiler detail', console.getvalue())

    def test_real_subprocess_failure_keeps_its_nonzero_exit(self):
        from build_progress import run_logged
        with self.assertRaises(subprocess.CalledProcessError) as captured:
            run_logged([sys.executable, '-B', '-c', 'raise SystemExit(7)'], cwd=ROOT, stdout=io.StringIO())
        self.assertEqual(captured.exception.returncode, 7)

    @unittest.skipIf(os.name == 'nt', 'POSIX process-group acceptance is not Windows acceptance')
    def test_cancellation_closes_term_ignoring_grandchild(self):
        from build_progress import stop_process_tree
        child = "import os,signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);print(os.getpid(),flush=True);time.sleep(60)"
        script = 'import subprocess,sys,time;subprocess.Popen([sys.executable,"-u","-c",' + repr(child) + ']);time.sleep(60)'
        process = subprocess.Popen([sys.executable, '-u', '-c', script], stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True, start_new_session=True)
        try:
            with selectors.DefaultSelector() as ready:
                ready.register(process.stdout, selectors.EVENT_READ)
                self.assertTrue(ready.select(5), 'Owned fixture did not become ready')
            pid = int(process.stdout.readline().strip())
            stop_process_tree(process)
            deadline = time.monotonic() + 2
            status = ''
            while time.monotonic() < deadline:
                status = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='], capture_output=True, text=True).stdout.strip()
                if not status or status.startswith('Z'):
                    break
                time.sleep(.05)
            self.assertTrue(not status or status.startswith('Z'), 'Owned grandchild is still running')
        finally:
            stop_process_tree(process); process.stdout.close()

    def test_skill_has_one_runtime_and_guides_not_duplicate_module_sources(self):
        builder = load('packaging/skill/build_skill.py', 'alignment_skill_payload')
        with tempfile.TemporaryDirectory() as folder:
            archive = builder.skill(output=Path(folder), install=False)
            with zipfile.ZipFile(archive) as package:
                names = package.namelist()
                guides = {'researchhelper/modules/' + module + '/module.md' for module in (
                    'evidencecollector', 'researchanalyzer', 'thesisbuilder', 'reportcomposer',
                    'visualrenderer', 'artifactexporter')}
                self.assertEqual({name for name in names if name.startswith('researchhelper/modules/')}, guides)
                self.assertTrue(all(package.read(name).strip() for name in guides))
                self.assertFalse(any(name.startswith('researchhelper/modules/') and name.endswith('.py') for name in names))
                manifest = json.loads(package.read('researchhelper/manifest.json'))
                self.assertEqual(package.read('researchhelper/researchhelper/VERSION'), (ROOT / 'VERSION').read_bytes())
                self.assertEqual(manifest['version'], manifest['runtime']['version'])

    def test_packaged_runtime_reads_only_its_copied_version(self):
        from shared_payload import copy_runtime
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / 'source'
            source = project / 'core/src/researchhelper'; source.mkdir(parents=True)
            (source / '__init__.py').write_bytes((ROOT / 'core/src/researchhelper/__init__.py').read_bytes())
            (project / 'VERSION').write_text('8.9.10\n')
            target = Path(folder) / 'install/researchhelper'
            copy_runtime(target, root=project)
            script = 'import sys;sys.path.insert(0,' + repr(str(target.parent)) + ');import researchhelper;assert researchhelper.__version__=="8.9.10"'
            subprocess.run([sys.executable, '-I', '-B', '-c', script], cwd=folder, check=True, capture_output=True, timeout=10)

    def test_manifest_binding_rejects_changed_component(self):
        builder = load('packaging/build_all.py', 'alignment_manifest_binding')
        from source_snapshot import digest
        with tempfile.TemporaryDirectory() as folder:
            payload = Path(folder).resolve() / 'source/result/build-staging/current'; payload.mkdir(parents=True)
            identity = {'version': '0.10.0', 'sha256': 'fixture-identity', 'protocol_id': 'fixture'}
            manifests = {}
            for key in ('skill', 'app', 'demo'):
                file = payload / (key + '.json')
                file.write_text(json.dumps({'version': '0.10.0', 'runtime': identity, 'platform': 'macos', 'architecture': 'arm64'}))
                manifests[key] = {'path': str(file), 'sha256': digest(file)}
            record = {'platform': 'macos', 'architecture': 'arm64', 'runtime': identity,
                      'version': '0.10.0', 'component_manifests': manifests}
            with patch('shared_payload.runtime_identity', return_value=identity):
                builder.verify_release_binding(record, payload, 'macos')
                wrong_version = dict(record, version='9.9.9')
                with self.assertRaises(RuntimeError):
                    builder.verify_release_binding(wrong_version, payload, 'macos')
                Path(manifests['app']['path']).write_text('{}')
                with self.assertRaises(RuntimeError):
                    builder.verify_release_binding(record, payload, 'macos')

    def test_macos_entry_preserves_failure_exit_without_interactive_block(self):
        with patch.dict(os.environ, RESEARCHHELPER_PYTHON='/nonexistent/researchhelper-python'):
            completed = subprocess.run(['/bin/zsh', str(ROOT / 'build-researchhelper-macos.command')],
                                       cwd=ROOT, input='', text=True, capture_output=True, timeout=10)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn('构建未完成', completed.stderr)

    def test_platform_version_file_is_in_both_freezer_payloads(self):
        for path in ('packaging/app/build_app.py', 'packaging/app/windows/build_windows.py'):
            text = (ROOT / path).read_text()
            self.assertIn("str(runtime / 'researchhelper/VERSION') + os.pathsep + 'researchhelper'", text)


if __name__ == '__main__':
    unittest.main()
