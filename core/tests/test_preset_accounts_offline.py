"""Preset login, preservation and failure checks without touching user stores."""
import ast
import concurrent.futures
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from researchhelper import service
from researchhelper.auth import LocalAccount, AuthenticationRequired
from researchhelper.contracts import ContractError
from researchhelper.service import make_server


class PresetAccountsTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2] / "result/acceptance"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="preset-accounts-", dir=root)
        self.addCleanup(self.temp.cleanup)
        self.account = LocalAccount(self.temp.name)

    def test_fresh_accounts_have_reference_passwords_and_private_verifiers(self):
        self.account.ensure_default_accounts()
        for name, password in (("admin", "66666666"), ("sales", "88888888")):
            cookie, identity = self.account.login(name, password, remember=True)
            self.assertEqual(identity["role"], name)
            self.assertEqual(self.account.require(cookie)["account"], name)
        raw = self.account.profile.read_text()
        self.assertNotIn("66666666", raw)
        self.assertNotIn("88888888", raw)
        self.assertEqual(self.account.profile.stat().st_mode & 0o777, 0o600)
        owner = json.loads(raw)
        self.assertNotEqual(owner["users"]["admin"]["salt"], owner["users"]["sales"]["salt"])

    def test_existing_passwords_and_remembered_sessions_are_unchanged(self):
        self.account.setup("existing-admin-pass", "existing-sales-pass")
        cookie, _ = self.account.login("sales", "existing-sales-pass", remember=True)
        before = {p.name: p.read_bytes() for p in (self.account.profile, self.account.session_file)}
        restarted = LocalAccount(self.temp.name)
        restarted.ensure_default_accounts()
        self.assertEqual(before, {p.name: p.read_bytes() for p in (self.account.profile, self.account.session_file)})
        self.assertEqual(restarted.require(cookie)["role"], "sales")
        with self.assertRaises(AuthenticationRequired):
            restarted.login("admin", "66666666")

    def test_corruption_is_not_replaced(self):
        self.account.profile.write_text("{corrupted-record")
        with self.assertRaises(ContractError):
            self.account.ensure_default_accounts()
        self.assertEqual(self.account.profile.read_text(), "{corrupted-record")

    def test_failed_first_save_is_not_swallowed(self):
        with patch.object(self.account, "_write", side_effect=OSError("synthetic disk failure")):
            with self.assertRaises(OSError):
                self.account.ensure_default_accounts()
        self.assertFalse(self.account.profile.exists())

    def test_concurrent_launches_do_not_replace_the_winning_account(self):
        accounts = [LocalAccount(self.temp.name) for _ in range(4)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as workers:
            list(workers.map(lambda account: account.ensure_default_accounts(), accounts))
        before = self.account.profile.read_bytes()
        self.account.ensure_default_accounts()
        self.assertEqual(before, self.account.profile.read_bytes())
        self.assertEqual(self.account.login("admin", "66666666")[1]["role"], "admin")

    def test_explicit_launcher_initializes_and_keeps_creation_unavailable(self):
        server = make_server(self.temp.name, authentication=True, runtime_configurable=True, preset_accounts=True)
        self.addCleanup(server.server_close)
        self.assertTrue(self.account.status()["configured"])
        with self.assertRaises(ContractError):
            self.account.setup("overwrite-admin", "overwrite-sales")
        source = Path(service.__file__).read_text()
        serve = next(node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name == 'serve')
        call, = [node for node in ast.walk(serve) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == 'make_server']
        flags = {keyword.arg: keyword.value for keyword in call.keywords}
        for flag in ('authentication', 'runtime_configurable', 'preset_accounts'):
            self.assertIsInstance(flags[flag], ast.Constant)
            self.assertIs(flags[flag].value, True)
        with self.assertRaises(ContractError):
            make_server(self.temp.name, preset_accounts=True)
