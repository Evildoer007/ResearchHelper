"""Check real backend startup output and the Foundation-only native decoder."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.auth import LocalAccount
from researchhelper.runtime import StoreLease


ROOT = Path(__file__).resolve().parents[1]
MAC_HOST = ROOT / 'products/app/desktop/macos/ResearchHelperApp.swift'
WINDOWS_HOST = ROOT / 'products/app/desktop/windows/Program.cs'
START = '// BEGIN RESEARCHHELPER_BACKEND_HANDSHAKE'
END = '// END RESEARCHHELPER_BACKEND_HANDSHAKE'

FAILURES = (
    ('execution-policy.json', b'{"enabled":false,"enabled":true,"policy_version":3}',
     'execution_policy_unavailable'),
    ('model-settings.json', b'{"endpoint":"https://first.invalid","endpoint":"https://second.invalid","model":"fixture","api_key":"PRIVATE_STARTUP_FIXTURE"}',
     'model_configuration_unavailable'),
    ('source-settings.json', b'{"ifind":{"access_token":"PRIVATE_STARTUP_FIXTURE","access_token":"last","refresh_token":""},"tinyshare":{"token":""}}',
     'source_configuration_unavailable'),
    ('local-account.json', b'{"version":2,"version":3,"users":{}}', 'account_unavailable'),
    ('local-sessions.json', b'{"same":{},"same":{}}', 'sessions_unavailable'),
)


def swift_probe(directory, root=ROOT):
    host = (root / MAC_HOST.relative_to(ROOT)).read_text()
    implementation = host.split(START, 1)[1].split(END, 1)[0]
    swift = Path(directory) / 'probe.swift'
    executable = Path(directory) / 'probe'
    swift.write_text('import Foundation\n' + implementation + """
while let line = readLine() {
    switch ResearchHelperBackendStartup.decode(Data(line.utf8)) {
    case .ready(let url): print("ready " + url.absoluteString)
    case .rejected(let detail): print("rejected " + detail)
    case .invalid: print("invalid")
    }
}
""")
    spec = importlib.util.spec_from_file_location('startup_swift_builder', root / 'packaging/app/build_app.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    builder.OUTPUT = Path(directory)
    result = subprocess.run(['xcrun', 'swiftc', *builder.swift_options(), str(swift), '-o', str(executable)],
        capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise AssertionError(result.stderr)
    return executable


class BackendStartupDiagnosticsTests(unittest.TestCase):
    def launch(self, store):
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'products/app/backend/entry.py')],
            env={'PATH':'/usr/bin:/bin', 'LANG':'en_US.UTF-8', 'RESEARCHHELPER_STORE':str(store),
                 'PYTHONDONTWRITEBYTECODE':'1'}, cwd=store, capture_output=True, text=True, timeout=6)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn('PRIVATE_STARTUP_FIXTURE', result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_invalid_controls_report_only_their_fixed_code_and_keep_originals(self):
        for name, raw, code in FAILURES:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                store = Path(folder)
                path = store / name
                path.write_bytes(raw)
                self.assertEqual(self.launch(store), {'ready':False, 'failure_code':code})
                self.assertEqual(path.read_bytes(), raw)
                self.assertFalse((store / 'credentials').exists())

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX special file behavior')
    def test_invalid_account_paths_stop_instead_of_waiting_for_a_fifo_writer(self):
        for name, code in (('local-account.json', 'account_unavailable'),
                           ('local-sessions.json', 'sessions_unavailable')):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                store = Path(folder)
                path = store / name
                os.mkfifo(path)
                self.assertEqual(self.launch(store), {'ready':False, 'failure_code':code})
                self.assertTrue(path.is_fifo())

    def test_another_owner_reports_archive_unavailable(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Path(folder)
            lease = StoreLease(store)
            try:
                self.assertEqual(self.launch(store), {'ready':False, 'failure_code':'store_unavailable'})
                self.assertEqual([item.name for item in store.iterdir()], ['.researchhelper-service.lock'])
            finally:
                lease.close()

    def test_unreadable_archive_returns_store_unavailable_and_preserves_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Path(folder)
            path = store / 'researchhelper.sqlite3'
            original = b'PRIVATE_STARTUP_FIXTURE is not a SQLite database'
            path.write_bytes(original)
            self.assertEqual(self.launch(store), {'ready': False, 'failure_code': 'store_unavailable'})
            self.assertEqual(path.read_bytes(), original)
            StoreLease(store).close()

    @unittest.skipUnless(os.name == 'posix', 'Requires local symlink support')
    def test_redirected_archive_returns_store_unavailable_without_opening_target(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Path(folder) / 'store'
            store.mkdir()
            target = Path(folder) / 'untouched.sqlite3'
            path = store / 'researchhelper.sqlite3'
            path.symlink_to(target)
            self.assertEqual(self.launch(store), {'ready': False, 'failure_code': 'store_unavailable'})
            self.assertTrue(path.is_symlink())
            self.assertFalse(target.exists())
            StoreLease(store).close()

    def test_valid_account_can_be_read_during_atomic_first_publication(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Path(folder)
            account = LocalAccount(store)
            link = os.link
            observed = []

            def observe(source, destination):
                link(source, destination)
                self.assertEqual(Path(destination).stat().st_nlink, 2)
                observed.append(LocalAccount(store).status()['configured'])

            with patch('researchhelper.auth.os.link', side_effect=observe):
                account.ensure_default_accounts()
            self.assertEqual(observed, [True])
            self.assertEqual(account.profile.stat().st_nlink, 1)

    @unittest.skipUnless(sys.platform == 'darwin' and shutil.which('xcrun'), 'macOS Swift compiler')
    def test_compiled_native_decoder_handles_fixed_failures_and_loopback_ready(self):
        good = {'ready':True, 'url':'http://127.0.0.1:41234/'}
        codes = {'store_unavailable'} | {code for _, _, code in FAILURES}
        bad = [
            {'url':good['url']}, {'ready':1, 'url':good['url']},
            dict(good, url='https://127.0.0.1:41234/'),
            dict(good, url='http://localhost:41234/'),
            dict(good, url='http://name@127.0.0.1:41234/'),
            dict(good, url='http://127.0.0.1:41234/?token=PRIVATE_STARTUP_FIXTURE'),
            dict(good, url='http://127.0.0.1:0/'),
            {'ready':False, 'failure_code':'PRIVATE_STARTUP_FIXTURE'},
            {'ready':False, 'failure_code':'store_unavailable', 'url':good['url']},
        ]
        with tempfile.TemporaryDirectory() as folder:
            executable = swift_probe(folder)
            failures = [dict(ready=False, failure_code=code, raw_error='PRIVATE_STARTUP_FIXTURE')
                        for code in sorted(codes)]
            stdin = '\n'.join(json.dumps(row) for row in [good, *failures, *bad]) + '\n'
            result = subprocess.run([str(executable)], input=stdin, capture_output=True, text=True,
                                    check=True, timeout=5)
        messages = result.stdout.splitlines()
        self.assertEqual(messages[0], 'ready ' + good['url'])
        self.assertEqual(messages[-len(bad):], ['invalid'] * len(bad))
        self.assertEqual(len(set(messages[1:1 + len(codes)])), len(codes))
        self.assertTrue(all(message.startswith('rejected ') for message in messages[1:1 + len(codes)]))
        self.assertNotIn('PRIVATE_STARTUP_FIXTURE', result.stdout)
        windows = WINDOWS_HOST.read_text()
        for code in codes:
            self.assertIn('"' + code + '" =>', windows)
