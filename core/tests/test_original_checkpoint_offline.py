"""Independent raw-original durability checks; no models or vendor requests."""

import base64
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import extract_material
from tests import test_auth_roles_offline as roles
from tests import test_material_parser_offline as parsing

ROOT = Path(__file__).resolve().parents[2]
PAYLOAD = '条款金额100万元。'.encode()


class OriginalCheckpointTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='raw-checkpoint-', dir=ROOT / 'result/acceptance')
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.agent = ResearchAgent(ResearchArchive(self.root), ROOT, offline=True)
        self.task = self.agent.create('核对文件内容', intent='原件内容核对')

    def retain(self, name='policy.txt', payload=PAYLOAD):
        return self.agent.archive.retain_original(self.task, 'material', name, payload)

    def test_commit_is_visible_to_another_archive_before_parser_starts(self):
        def parse(name, payload):
            reader = ResearchArchive(self.root)
            pending = reader.retained_originals(self.task, pending_only=True)
            self.assertEqual(len(pending), 1)
            self.assertEqual(reader.original(self.task, pending[0]['id'])[1], payload)
            self.assertEqual(reader.get(self.task['id'])['materials'], [])
            return extract_material(name, payload)
        with patch('researchhelper.engine.extract_material', side_effect=parse):
            result = self.agent.add_material(self.task['id'], 'policy.txt', PAYLOAD)
        self.assertEqual(self.agent.archive.retained_originals(result, pending_only=True), [])
        self.assertEqual(result['evidence'], [])

    def test_parent_process_exit_during_parse_preserves_exact_original(self):
        script = '''
import os,sys
from pathlib import Path
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
class ExitParser:
    def parse(self, name, payload):
        os._exit(73)
agent=ResearchAgent(ResearchArchive(sys.argv[1]),Path.cwd(),offline=True)
agent.material_parser=ExitParser()
agent.add_material(sys.argv[2], 'policy.txt', '条款金额100万元。'.encode())
'''
        result = subprocess.run([sys.executable, '-B', '-c', script, str(self.root), self.task['id']], cwd=ROOT, timeout=10, capture_output=True)
        self.assertEqual(result.returncode, 73, result.stderr.decode())
        restored = ResearchAgent(ResearchArchive(self.root), ROOT, offline=True)
        saved = restored.archive.get(self.task['id'])
        pending = restored.source_catalog(saved['id'])['retained_originals']
        self.assertEqual(len(pending), 1)
        self.assertEqual(saved['materials'], [])
        final = restored.retry_original(saved['id'], pending[0]['id'])
        self.assertEqual(final['materials'][0]['original_base64'], base64.b64encode(PAYLOAD).decode())
        self.assertEqual(final['ledger'], self.task['ledger'])
        self.assertEqual(final['evidence'], [])

    def test_failed_raw_save_never_starts_parser(self):
        with patch.object(self.agent.archive, 'retain_original', side_effect=OSError('disk full')), patch('researchhelper.engine.extract_material') as parser:
            with self.assertRaises(OSError):
                self.agent.add_material(self.task['id'], 'policy.txt', PAYLOAD)
        parser.assert_not_called()

    def test_failed_task_commit_leaves_recoverable_original(self):
        with patch.object(self.agent.archive, 'save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.agent.add_material(self.task['id'], 'policy.txt', PAYLOAD)
        self.assertEqual(self.agent.archive.get(self.task['id'])['materials'], [])
        original = self.agent.source_catalog(self.task['id'])['retained_originals'][0]
        self.assertEqual(self.agent.retry_original(self.task['id'], original['id'])['materials'][0]['parts'][0]['text'], PAYLOAD.decode())

    def test_downloaded_filing_is_saved_before_parse_and_retry_does_not_download(self):
        task, host = parsing.MaterialParserTests.filing_task(self)
        collector = self.agent.collector
        collector.original_keeper = self.agent.archive.retain_original
        with patch.object(collector, '_original_record', side_effect=RuntimeError('local parser failed')), patch.object(collector, '_download_original', wraps=collector._download_original) as download:
            result = self.agent.start(task['id'], host)
        self.assertEqual(download.call_count, 1)
        saved = self.agent.archive.get(task['id'])
        originals = self.agent.source_catalog(task['id'])['retained_originals']
        self.assertEqual(len(originals), 1, result)
        self.assertEqual(saved['ledger']['used']['data_requests'], 1)
        before = json.loads(json.dumps(saved['ledger']))
        with patch.object(collector, '_download_original', side_effect=AssertionError('must not redownload')):
            final = self.agent.retry_original(task['id'], originals[0]['id'])
        self.assertEqual(final['ledger'], before)
        records = final['source_records'] + final.get('pending_source_records', [])
        self.assertEqual(records[-1]['snapshot']['material']['status'], '已解析')
        self.assertEqual(self.agent.source_catalog(task['id'])['retained_originals'], [])

    def test_adopted_unreadable_original_remains_recoverable(self):
        self.agent.material_parser = parsing.MaterialParser()
        result = self.agent.add_material(self.task['id'], 'bad.pdf', b'not a pdf')
        original = self.agent.source_catalog(result['id'])['retained_originals'][0]
        self.assertIsNotNone(original['artifact_id'])
        self.assertIn('解析受限', original['status'])
        self.assertEqual(result['materials'][0]['parts'], [])

    def test_invalid_or_oversize_file_has_no_receipt(self):
        for name, payload in [('file.exe', b'bad'), ('file.txt', b'a' * (25 * 1024 * 1024 + 1))]:
            with self.subTest(name=name), self.assertRaises(ContractError):
                self.agent.add_material(self.task['id'], name, payload)
        self.assertEqual(self.agent.archive.retained_originals(self.task), [])

    def test_receipt_is_task_and_version_scoped(self):
        original = self.retain()
        other = self.agent.create('另一个原件问题', intent='原件内容核对')
        for task, identity in [(other, original), (dict(self.task, version=2), original), (self.task, None)]:
            with self.subTest(identity=identity), self.assertRaises(ContractError):
                self.agent.archive.original(task, identity)

    def test_corrupted_bytes_and_identity_are_rejected(self):
        original = self.retain()
        body, _ = self.agent.archive.original(self.task, original)
        for changes in [{'original_base64': 'YmFk'}, {'name': None}, {'kind': 'api'}, {'byte_count': 0}, {'original_base64': None}]:
            with self.subTest(changes=changes):
                with self.agent.archive.connect() as db:
                    db.execute('UPDATE originals SET body=? WHERE id=?', (json.dumps(dict(body, **changes)), original))
                with self.assertRaises(ContractError):
                    self.agent.retry_original(self.task['id'], original)

    def test_duplicate_and_legacy_material_receipt_are_adopted(self):
        task = self.agent.add_material(self.task['id'], 'policy.txt', PAYLOAD)
        with self.agent.archive.connect() as db:
            db.execute('DELETE FROM originals')  # isolated legacy fixture only
        final = self.agent.add_material(task['id'], 'policy.txt', PAYLOAD)
        self.assertEqual(len(final['materials']), 1)
        self.assertEqual(len(self.agent.archive.retained_originals(final)), 1)
        self.assertEqual(self.agent.archive.retained_originals(final, pending_only=True), [])

    def test_raw_metadata_has_no_bytes_or_approval(self):
        self.retain()
        directory = self.agent.source_catalog(self.task['id'])
        self.assertEqual(directory['items'], [])
        text = json.dumps(directory)
        for forbidden in ['original_base64', 'source_check', 'applicability_check', PAYLOAD.decode()]:
            self.assertNotIn(forbidden, text)
        self.assertEqual(self.agent.archive.get(self.task['id'])['evidence'], [])

    def test_retry_releases_engine_lock_during_parse(self):
        original = self.retain()
        entered, release = threading.Event(), threading.Event()
        errors = []
        def parse(name, payload):
            entered.set()
            if not release.wait(3):
                raise RuntimeError('lock test timed out')
            return extract_material(name, payload)
        def retry():
            try:
                self.agent.retry_original(self.task['id'], original)
            except BaseException as error:
                errors.append(error)
        with patch('researchhelper.engine.extract_material', side_effect=parse):
            worker = threading.Thread(target=retry)
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                acquired = self.agent.lock.acquire(timeout=.3)
                self.assertTrue(acquired)
                if acquired:
                    self.agent.lock.release()
            finally:
                release.set()
                worker.join(4)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])

    def test_version_change_before_or_during_parse_is_rejected(self):
        original = self.retain()
        with self.assertRaises(ContractError):
            self.agent.add_material(self.task['id'], 'policy.txt', PAYLOAD, expected_version=2)
        def parse(name, payload):
            task = self.agent.archive.get(self.task['id'])
            task['version'] = 2
            self.agent.archive.save(task)
            return extract_material(name, payload)
        with patch('researchhelper.engine.extract_material', side_effect=parse), self.assertRaises(ContractError):
            self.agent.retry_original(self.task['id'], original)
        self.assertEqual(self.agent.archive.get(self.task['id'])['materials'], [])

    def test_running_task_cannot_retry(self):
        original = self.retain()
        task = self.agent.archive.get(self.task['id'])
        task['status'] = '运行中'
        self.agent.archive.save(task)
        with patch('researchhelper.engine.extract_material') as parser, self.assertRaises(ContractError):
            self.agent.retry_original(self.task['id'], original)
        parser.assert_not_called()

    def test_frozen_report_is_not_changed_by_recovery(self):
        task = self.agent.start(self.agent.create_case('value')['id'])
        before = self.agent.export(task['id'])
        original = self.agent.archive.retain_original(task, 'material', 'policy.txt', PAYLOAD)
        final = self.agent.retry_original(task['id'], original)
        self.assertEqual(len(final['pending_materials']), 1)
        self.assertEqual(self.agent.export(task['id']), before)
        self.assertEqual(final['ledger'], task['ledger'])

    def test_skill_reuses_same_local_recovery_without_model(self):
        spec = importlib.util.spec_from_file_location('checkpoint_session', ROOT / 'core/session.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        session = module.Session(self.root / 'skill-project')
        task = session.create('核对文件', intent='原件内容核对')
        original = session.archive.retain_original(task, 'material', 'policy.txt', PAYLOAD)
        final = session.retry_original(task['id'], original)
        self.assertEqual(final['materials'][0]['parts'][0]['text'], PAYLOAD.decode())
        self.assertIsNone(session.agent.model)
        self.assertEqual(final['ledger'], task['ledger'])


class OriginalRecoveryInterfaceTests(unittest.TestCase):
    setUp = roles.RoleIsolationTests.setUp
    tearDown = roles.RoleIsolationTests.tearDown
    call = roles.RoleIsolationTests.call
    setup_accounts = roles.RoleIsolationTests.setup_accounts

    def test_recovery_obeys_ownership_csrf_and_updates_metadata(self):
        admin, sales = self.setup_accounts()
        task = self.call('/api/tasks', {'question': '核对文件', 'intent': '原件内容核对'}, admin)[1]
        original = self.server.agent.archive.retain_original(task, 'material', 'policy.txt', PAYLOAD)
        route = '/api/tasks/' + task['id']
        self.assertEqual(self.call(route, cookie=admin)[1]['retained_originals'][0]['id'], original)
        self.assertEqual(self.call(route + '/retry-original', {'original_id': original}, sales)[0], 403)
        self.assertEqual(self.call(route + '/retry-original', {'original_id': original}, admin, token=False)[0], 400)
        code, final, _ = self.call(route + '/retry-original', {'original_id': original}, admin)
        self.assertEqual(code, 200, final)
        self.assertEqual(final.get('retained_originals', []), [])
        self.assertEqual(final['evidence'], [])
        self.assertEqual(final['ledger'], task['ledger'])


if __name__ == '__main__':
    unittest.main()
