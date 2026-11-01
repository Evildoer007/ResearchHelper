"""Retain account assertions after core/src migration; old tests stay untouched.

Only source location and owned test workspace differ from the retained tests.
No permission, password-preservation or schema assertion is removed.
"""
import importlib.util
import json
import tempfile
from pathlib import Path

from researchhelper import auth, service
from researchhelper.auth import LocalAccount
from researchhelper.contracts import ContractError

ROOT = Path(__file__).resolve().parents[1]


def retained(name):
    spec = importlib.util.spec_from_file_location('retained_'+name,ROOT/'core/tests'/name)
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


preset = retained('test_preset_accounts_offline.py')
hardening = retained('test_auth_hardening_offline.py')


class CurrentPresetAccountsTests(preset.PresetAccountsTests):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='current-preset-',dir=ROOT/'result/acceptance')
        self.addCleanup(self.temp.cleanup)
        self.account = LocalAccount(self.temp.name)

    def test_explicit_launcher_initializes_and_keeps_creation_unavailable(self):
        server = service.make_server(self.temp.name,authentication=True,runtime_configurable=True,preset_accounts=True)
        self.addCleanup(server.server_close)
        self.assertTrue(self.account.status()['configured'])
        with self.assertRaises(ContractError): self.account.setup('overwrite-admin','overwrite-sales')
        source = Path(service.__file__).resolve()
        self.assertEqual(source,ROOT/'core/src/researchhelper/service.py')
        self.assertIn('authentication=True, runtime_configurable=True, preset_accounts=True',source.read_text())
        with self.assertRaises(ContractError): service.make_server(self.temp.name,preset_accounts=True)


class CurrentAccountHardeningTests(hardening.LocalAccountHardeningTests):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='current-hardening-',dir=ROOT/'result/acceptance')
        self.account = LocalAccount(self.directory.name)
        self.account.setup('admin-fixture-password','sales-fixture-password')
        self.admin_cookie,_ = self.account.login('admin','admin-fixture-password')

    def test_schema_validation_does_not_rely_on_python_assertions(self):
        source = Path(auth.__file__).resolve()
        self.assertEqual(source,ROOT/'core/src/researchhelper/auth.py')
        self.assertNotIn('assert ',source.read_text())
        owner = json.loads(self.account.profile.read_text())
        owner['users']['admin']['salt']='00'
        self.account._write(self.account.profile,owner)
        with self.assertRaisesRegex(ContractError,'格式无效'): self.account.status()
