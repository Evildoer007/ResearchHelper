"""Actual native WebKit snapshots with disposable accounts and offline research."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
OH = Path('/Users/haoranxu/Desktop/OptionHelper')


class RenderRuntime:
    """Source mode or an explicitly selected read-only DMG; both use a fresh Store."""
    def __init__(self, output, record, *, candidate_path=None, discard_temporary_store=False):
        self.output, self.record = output, record
        self.candidate_path = candidate_path
        self.discard_temporary_store = discard_temporary_store
        self.store_owned = not (output / 'store').exists()
        self.service = self.worker = self.process = self.errors = self.mount_folder = None
        self.mount = None
        self.attached = False

    def start(self):
        selected = self.candidate_path or os.environ.get('RESEARCHHELPER_CURRENT_RECEIPT')
        if not selected:
            from core.bootstrap import configure_imports
            configure_imports()
            from researchhelper.service import make_server
            self.service = make_server(self.output / 'store', offline=True, authentication=True,
                                       preset_accounts=True, runtime_configurable=True)
            self.worker = threading.Thread(target=self.service.serve_forever, daemon=True)
            self.worker.start()
            self.record['backend_mode'] = 'development_source'
            return 'http://127.0.0.1:' + str(self.service.server_port) + '/'
        sys.path.insert(0, str(ROOT / 'packaging'))
        sys.path.insert(0, str(ROOT / 'maintenance'))
        from build_current import verify_current_build
        from candidate_acceptance import verify_mounted_bundle
        candidate = verify_current_build(selected, ROOT)
        self.record.update(candidate=str(Path(selected).resolve()), backend_mode='readonly_dmg',
                           installed=False, bundled_backend_isolated=True)
        self.mount_folder = tempfile.TemporaryDirectory(prefix='mount-', dir=self.output)
        self.mount = Path(self.mount_folder.name)
        subprocess.run(['/usr/bin/hdiutil', 'attach', candidate['artifacts']['dmg']['path'],
                        '-readonly', '-nobrowse', '-mountpoint', str(self.mount)],
                       check=True, capture_output=True, timeout=30)
        self.attached = True
        bundle = self.mount / 'ResearchHelper.app'
        verify_mounted_bundle(bundle, candidate)
        subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(bundle)],
                       check=True, capture_output=True, timeout=30)
        self.record['signature_verified'] = True
        policy = '(version 1)(allow default)(deny network*)(allow network* (local ip "localhost:*"))(allow network* (remote ip "localhost:*"))(allow network* (local unix-socket))(allow network* (remote unix-socket))'
        for denied in [ROOT / 'core', ROOT / 'modules', ROOT / 'products', Path('/opt/anaconda3'), OH,
                       Path('/Users/haoranxu/Desktop/DataFetcher')]:
            policy += '(deny file-read* (subpath ' + json.dumps(str(denied)) + '))'
        executable = bundle / 'Contents/Resources/backend/researchhelper-server/researchhelper-server'
        self.errors = (self.output / 'backend.log').open('w')
        environment = {'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8',
                       'RESEARCHHELPER_STORE': str(self.output / 'store'),
                       'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONTZPATH': str(self.output / 'no-system-tzdata')}
        self.process = subprocess.Popen(['/usr/bin/sandbox-exec', '-p', policy, str(executable)],
                                        cwd=self.output, env=environment, stdout=subprocess.PIPE, stderr=self.errors)
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=20):
                raise RuntimeError('Bundled render service did not become ready')
            base = json.loads(self.process.stdout.readline())['url']
        if not base.startswith('http://127.0.0.1:'):
            raise RuntimeError('Bundled render service returned an unexpected origin')
        return base.rstrip('/') + '/'

    def stop(self):
        if self.service:
            self.service.shutdown()
            self.service.server_close()
            self.worker.join(timeout=3)
            self.record['service_stopped'] = not self.worker.is_alive()
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            self.process.stdout.close()
            self.record['service_stopped'] = self.process.poll() is not None
        if self.errors:
            self.errors.close()
        if self.attached:
            subprocess.run(['/usr/bin/hdiutil', 'detach', str(self.mount)], check=True, capture_output=True, timeout=30)
            self.attached = False
        self.record['mount_detached'] = not self.attached
        if self.mount_folder:
            self.mount_folder.cleanup()
        # Opt in only for reproducible synthetic/host fixtures. Failed stores
        # and any store which predated this runtime remain available to inspect.
        store = self.output / 'store'
        if (self.discard_temporary_store and self.store_owned
                and self.record.get('status') == 'scoped_passed'
                and self.record.get('service_stopped') is True):
            if store.is_dir() and not store.is_symlink() and store.resolve().parent == self.output.resolve():
                shutil.rmtree(store)
            self.record['temporary_research_removed'] = not store.exists()


def run():
    output = Path(tempfile.mkdtemp(prefix='native-render-', dir=ROOT / 'result/acceptance'))
    record = {'status': 'failed', 'native_window_opened': False, 'system_dialog_presented': False,
              'production_store_touched': False, 'model_calls': 0, 'real_data_requests': 0,
              'actual_webkit_pixels': False, 'system_titlebar_pixels': False,
              'finite_animations_finished_for_capture': True}
    runtime = RenderRuntime(output, record)
    try:
        base = runtime.start()
        references = [OH / 'products/app/frontend/shared' / name for name in ('styles.css', 'refinement.css', 'theme-overrides.css', 'choice-controls.css')]
        references += [OH / 'products/app/frontend/optchat/index.html', OH / 'products/app/desktop/macos/OptionHelperApp.swift']
        record['oh_reference_hashes'] = {str(file): hashlib.sha256(file.read_bytes()).hexdigest() for file in references}
        for file in references[:4]:
            if file.read_bytes() != (ROOT / 'products/app/frontend/ui' / file.name).read_bytes():
                raise RuntimeError('OH shared CSS drift: ' + file.name)
        files = [ROOT / 'products/app/desktop/macos/ResearchHelperApp.swift', ROOT / 'packaging/tests/native_workbench_render.swift']
        files += sorted(file for file in (ROOT / 'products/app/frontend').rglob('*') if file.is_file())
        record['source_hashes'] = {str(file.relative_to(ROOT)): hashlib.sha256(file.read_bytes()).hexdigest() for file in files}
        spec = importlib.util.spec_from_file_location('rh_native_builder', ROOT / 'packaging/app/build_app.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory(prefix='compile-', dir=output) as directory:
            builder.OUTPUT = Path(directory)
            binary = Path(directory) / 'native-render-probe'
            command = ['xcrun', 'swiftc', '-parse-as-library', '-DRESEARCHHELPER_NATIVE_UI_TEST', *builder.swift_options(),
                       str(files[0]), str(files[1]), '-framework', 'AppKit', '-framework', 'WebKit', '-o', str(binary)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=120)
            (output / 'compiler.log').write_text(result.stdout + result.stderr)
            if result.returncode:
                raise RuntimeError('Native render compilation failed; see compiler.log')
            result = subprocess.run([str(binary), base, str(output)], capture_output=True, text=True, timeout=180)
            (output / 'probe.log').write_text(result.stdout + '\n' + result.stderr)
            record.update(json.loads(result.stdout))
            record['actual_webkit_pixels'] = bool(record.get('screens'))
            if result.returncode:
                raise RuntimeError(record.get('error', 'Native render failed'))
        if any(hashlib.sha256(Path(file).read_bytes()).hexdigest() != digest for file, digest in record['oh_reference_hashes'].items()):
            raise RuntimeError('OH reference changed during comparison')
        if any(hashlib.sha256((ROOT / file).read_bytes()).hexdigest() != digest for file, digest in record['source_hashes'].items()):
            raise RuntimeError('RH render source changed during comparison')
        if record.get('candidate'):
            from build_current import verify_current_build
            candidate = verify_current_build(record['candidate'], ROOT)
            for name, digest in candidate['source_hashes'].items():
                if name in record['source_hashes'] and record['source_hashes'][name] != digest:
                    raise RuntimeError('Native rendered source does not match candidate: ' + name)
    except Exception as error:
        record.update(status='failed', error=str(error))
    finally:
        try:
            runtime.stop()
        except Exception as error:
            record.update(status='failed', cleanup_error=str(error))
        record.update(compile_workspace_removed=not any(output.glob('compile-*')))
        (output / 'receipt.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps({'status': record['status'], 'checks': len(record.get('checks', [])), 'screens': len(record.get('screens', [])),
                      'receipt': str(output / 'receipt.json'), 'error': record.get('error')}, ensure_ascii=False))
    if record['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    run()
