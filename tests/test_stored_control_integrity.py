"""Exercise RH-owned control files using disposable Stores and synthetic values."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.auth import LocalAccount
from researchhelper.contracts import ContractError
from researchhelper.model import ModelSettings
from researchhelper.runtime import ExecutionPolicy, StoreLease
from researchhelper.service import make_server
from researchhelper.settings import DataSettings


ROOT = Path(__file__).resolve().parents[1]
ENABLE = {'enabled': True, 'confirm': True}


class StoredControlIntegrityTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='rh-stored-controls-')
        self.addCleanup(folder.cleanup)
        self.store = Path(folder.name)

    def test_duplicate_policy_never_starts_or_replaces_original(self):
        policy = ExecutionPolicy(self.store, configurable=True)
        for raw in (b'{"enabled":false,"enabled":true,"policy_version":3}',
                    b'{"enabled":false,"policy_version":2,"policy_version":3}'):
            with self.subTest(raw=raw):
                policy.path.write_bytes(raw)
                with self.assertRaisesRegex(ContractError, '运行策略无法读取'):
                    policy.read()
                with self.assertRaises(ContractError):
                    policy.save(ENABLE)
                with self.assertRaises(ContractError):
                    make_server(self.store, ROOT, authentication=True, runtime_configurable=True)
                self.assertEqual(policy.path.read_bytes(), raw)
                self.assertFalse((self.store / 'local-account.json').exists())
                lease = StoreLease(self.store)
                lease.close()

    def test_policy_commit_rejects_an_external_edit_after_validation(self):
        policy = ExecutionPolicy(self.store, configurable=True)
        policy.path.write_bytes(b'{"enabled":false,"policy_version":2}')
        external = b'{"enabled":false,"policy_version":3}'
        prior = policy.read()
        original_fsync = os.fsync

        def edited(fd):
            policy.path.write_bytes(external)
            original_fsync(fd)

        with patch('researchhelper.credentials.os.fsync', side_effect=edited), self.assertRaisesRegex(ContractError, '已变化'):
            policy.save(ENABLE, expected=prior)
        self.assertEqual(policy.path.read_bytes(), external)
        self.assertFalse(list(self.store.glob('.execution-policy-*')))

    def test_model_and_data_duplicate_fields_never_migrate_or_replace(self):
        model = ModelSettings(self.store)
        data = DataSettings(self.store)
        examples = [
            (model, b'{"endpoint":"https://first.invalid","endpoint":"https://second.invalid","model":"fixture","api_key":"PRIVATE_FIXTURE"}',
             {'endpoint':'https://second.invalid','model':'fixture','api_key':'SYNTHETIC_REPLACEMENT'}),
            (data, b'{"ifind":{"access_token":"PRIVATE_FIXTURE","access_token":"SYNTHETIC_LAST","refresh_token":""},"tinyshare":{"token":""}}',
             {'ifind':{'access_token':'SYNTHETIC_REPLACEMENT'}}),
        ]
        for settings, raw, update in examples:
            with self.subTest(component=settings.__class__.__name__):
                settings.path.write_bytes(raw)
                for operation in (settings.read, lambda: settings.read(private=True),
                                  settings.migrate_legacy, lambda: settings.save(update)):
                    with self.assertRaises(ContractError) as error:
                        operation()
                    self.assertNotIn('PRIVATE_FIXTURE', str(error.exception))
                    self.assertNotIn('SYNTHETIC_LAST', str(error.exception))
                    self.assertEqual(settings.path.read_bytes(), raw)
                self.assertFalse((self.store / 'credentials').exists())
                settings.path.unlink()

    def test_ambiguous_account_does_not_authenticate_or_recreate(self):
        account = LocalAccount(self.store)
        account.ensure_default_accounts()
        original = account.profile.read_bytes()
        ambiguous = b'{"version":1,' + original[1:]
        account.profile.write_bytes(ambiguous)
        with self.assertRaises(ContractError):
            account.login('admin', '66666666')
        with self.assertRaises(ContractError):
            account.ensure_default_accounts()
        self.assertEqual(account.profile.read_bytes(), ambiguous)
        account.profile.write_bytes(original)
        _, result = account.login('admin', '66666666')
        self.assertEqual(result['role'], 'admin')

    def test_invalid_session_expiry_does_not_restore_a_login(self):
        account = LocalAccount(self.store)
        account.ensure_default_accounts()
        cookie, _ = account.login('sales', '88888888', remember=True)
        original = account.session_file.read_bytes()
        records = json.loads(original)
        next(iter(records.values()))['expires'] = 'REPLAY_EXPIRY'
        fixture = json.dumps(records).encode()
        for literal in (b'Infinity', b'-Infinity', b'NaN', b'1e999', b'true', b'0, "expires": 3000000000'):
            with self.subTest(literal=literal):
                invalid = fixture.replace(b'"REPLAY_EXPIRY"', literal)
                account.session_file.write_bytes(invalid)
                with self.assertRaises(ContractError):
                    LocalAccount(self.store)
                self.assertEqual(account.session_file.read_bytes(), invalid)
        account.session_file.write_bytes(original)
        self.assertEqual(LocalAccount(self.store).current(cookie)['role'], 'sales')

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX special file behavior')
    def test_configuration_reader_rejects_fifo_without_waiting_for_a_writer(self):
        pipe = self.store / 'execution-policy.json'
        os.mkfifo(pipe)
        code = """
import sys, tests
from researchhelper.contracts import ContractError
from researchhelper.runtime import ExecutionPolicy
try:
    ExecutionPolicy(sys.argv[1], configurable=True).read()
except ContractError:
    print('blocked')
else:
    raise AssertionError('FIFO was accepted')
"""
        result = subprocess.run([sys.executable, '-B', '-c', code, str(self.store)],
            cwd=ROOT, capture_output=True, text=True, check=True, timeout=5)
        self.assertEqual(result.stdout.strip(), 'blocked')
