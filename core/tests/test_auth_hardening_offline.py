"""Independent local-account validation, with no UI or external connections."""
import inspect
import json
import tempfile
import unittest
from pathlib import Path

from tests import ROOT
from researchhelper.auth import LocalAccount
from researchhelper.contracts import ContractError


class LocalAccountHardeningTests(unittest.TestCase):
    def setUp(self):
        root = ROOT / "result/acceptance"
        root.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(prefix="account-hardening-", dir=root)
        self.account = LocalAccount(self.directory.name)
        self.account.setup("admin-fixture-password", "sales-fixture-password")
        self.admin_cookie, _ = self.account.login("admin", "admin-fixture-password")

    def tearDown(self):
        self.directory.cleanup()

    def test_reset_keeps_distinct_passwords_and_rejected_change_keeps_session(self):
        sales_cookie, _ = self.account.login("sales", "sales-fixture-password", remember=True)
        original = self.account.profile.read_bytes()
        with self.assertRaisesRegex(ContractError, "不同密码"):
            self.account.reset_sales(self.admin_cookie, "admin-fixture-password")
        self.assertEqual(self.account.profile.read_bytes(), original)
        self.assertEqual(self.account.require(sales_cookie)["role"], "sales")
        self.account.reset_sales(self.admin_cookie, "sales-next-fixture-password")
        self.assertIsNone(self.account.current(sales_cookie))
        _, identity = self.account.login("sales", "sales-next-fixture-password")
        self.assertEqual(identity["role"], "sales")

    def test_create_sales_also_requires_distinct_password(self):
        owner = json.loads(self.account.profile.read_text())
        del owner["users"]["sales"]
        self.account._write(self.account.profile, owner)
        with self.assertRaisesRegex(ContractError, "不同密码"):
            self.account.create_sales(self.admin_cookie, "sales", "admin-fixture-password")
        self.assertNotIn("sales", json.loads(self.account.profile.read_text())["users"])

    def test_schema_validation_does_not_rely_on_python_assertions(self):
        source = inspect.getsource(inspect.getmodule(LocalAccount))
        self.assertNotIn("assert ", source)
        owner = json.loads(self.account.profile.read_text())
        owner["users"]["admin"]["salt"] = "00"
        self.account._write(self.account.profile, owner)
        with self.assertRaisesRegex(ContractError, "格式无效"):
            self.account.status()
