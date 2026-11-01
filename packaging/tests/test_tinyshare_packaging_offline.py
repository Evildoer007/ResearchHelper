"""Inspect the vendor bytecode and staged loader without modifying its installation."""
import dis
import hashlib
import importlib.util
import json
import marshal
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("tinyshare_release_builder", ROOT / "packaging/app/build_app.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def code_tree(code):
    yield code
    for constant in code.co_consts:
        if isinstance(constant, types.CodeType):
            yield from code_tree(constant)


class TinysharePackagingTests(unittest.TestCase):
    def setUp(self):
        root = ROOT / "result/acceptance/tinyshare-packaging"
        root.mkdir(parents=True, exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(prefix="isolated-", dir=root)
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)

    def test_only_six_matching_distribution_files_are_selected(self):
        payload = builder.tinyshare_payload()
        self.assertEqual({source.name for source, _ in payload}, set(builder.TINYSHARE_BYTECODE))
        self.assertEqual(len(payload), 6)
        for source, destination in payload:
            self.assertEqual(source.parent.name, "py312")
            self.assertEqual(destination, "tinyshare")
            self.assertEqual(source.read_bytes()[:4], importlib.util.MAGIC_NUMBER)
            self.assertIsInstance(marshal.loads(source.read_bytes()[16:]), types.CodeType)
        arguments = builder.tinyshare_build_options()
        self.assertEqual(arguments.count("--add-data"), 6)
        self.assertNotIn("--collect-all", arguments)
        self.assertNotIn("--collect-data tinyshare", " ".join(arguments))

    def test_opaque_bytecode_imports_are_covered_or_explicitly_optional(self):
        imported = set()
        siblings = set()
        for source, _ in builder.tinyshare_payload():
            for code in code_tree(marshal.loads(source.read_bytes()[16:])):
                instructions = list(dis.get_instructions(code))
                for index, instruction in enumerate(instructions):
                    if instruction.opname != "IMPORT_NAME":
                        continue
                    level, names = instructions[index - 2].argval, instructions[index - 1].argval
                    if level == 0:
                        imported.add(instruction.argval.split(".")[0])
                    elif instruction.argval:
                        siblings.add(instruction.argval.split(".")[0] + ".pyc")
                    elif isinstance(names, tuple):
                        siblings.update(name + ".pyc" for name in names if name != "*")
        external = imported - sys.stdlib_module_names
        self.assertEqual(external - set(builder.TINYSHARE_HIDDEN_IMPORTS), {"IPython", "wmi"})
        self.assertTrue(siblings <= set(builder.TINYSHARE_BYTECODE))
        self.assertTrue({"pandas", "numpy", "requests", "urllib3"} <= set(builder.TINYSHARE_HIDDEN_IMPORTS))
        self.assertFalse(set(builder.TINYSHARE_HIDDEN_IMPORTS) & set(builder.EXCLUDED_MODULES))
        self.assertTrue({"torch", "scipy", "tensorflow", "IPython", "wmi"} <= set(builder.EXCLUDED_MODULES))

    def test_staged_physical_bytecode_loader_imports_real_dependencies(self):
        package = self.root / "tinyshare"
        package.mkdir()
        source = Path(importlib.util.find_spec("tinyshare").origin)
        before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path, _ in builder.tinyshare_payload()}
        shutil.copyfile(source, package / "__init__.py")
        for path, _ in builder.tinyshare_payload():
            shutil.copyfile(path, package / path.name)
        store = self.root / "store"
        store.mkdir()
        profile = ('(version 1)(allow default)(deny network*)(deny file-write*)'
                   '(allow file-write* (subpath ' + json.dumps(str(store.resolve())) + '))'
                   '(allow file-write* (literal "/dev/null"))'
                   '(deny file-read* (subpath ' + json.dumps(str(Path.home() / ".tinyshare")) + '))'
                   '(deny file-read* (subpath ' + json.dumps(str(Path.home() / ".tinyshare_cache")) + '))')
        program = (
            "import sys,json; from pathlib import Path; "
            f"sys.path.insert(0,{str(self.root)!r}); "
            "import tinyshare; import tinyshare.config,tinyshare.formula,tinyshare.token_utils,tinyshare.cache_utils,tinyshare.device_id; "
            "import pandas,numpy,requests,urllib3; "
            "client=tinyshare.pro_api('abcdef0g'+('x'*56),timeout=1); "
            "assert callable(client.daily); assert hasattr(tinyshare,'TinyShareProApi'); "
            "calls=[]; original_post=requests.post; "
            "requests.post=lambda *a,**k:(calls.append(True),original_post(*a,**k))[1]\n"
            "try:\n client.daily(ts_code='600000.SH',start_date='20260901',end_date='20260902')\n"
            "except Exception:\n pass\n"
            "assert len(calls)==1, 'Expected one real transport dispatch'\n"
            "try:\n Path(tinyshare.__file__).with_name('forbidden-write').write_text('blocked')\n"
            "except PermissionError:\n pass\n"
            "else:\n raise AssertionError('Package directory was writable')\n"
            "print(json.dumps({'path':tinyshare.__file__,'pandas':pandas.__version__,'numpy':numpy.__version__,'dispatches':len(calls)}))"
        )
        environment = {**os.environ, "TINYSHARE_CACHE_DIR": str(store / "cache"), "TMPDIR": str(store)}
        result = subprocess.run(["/usr/bin/sandbox-exec", "-p", profile, sys.executable, "-I", "-B", "-c", program], cwd=self.root, env=environment,
                                text=True, capture_output=True, check=True, timeout=30)
        state = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(Path(state["path"]), package / "__init__.py")
        self.assertTrue(state["pandas"] and state["numpy"])
        self.assertEqual(state["dispatches"], 1)
        self.assertEqual({path.name for path in package.iterdir()}, set(builder.TINYSHARE_BYTECODE) | {"__init__.py"})
        for path, _ in builder.tinyshare_payload():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before[str(path)])

    def test_license_inventory_covers_new_dependencies_without_name_collisions(self):
        builder.licenses(self.root)
        inventory = json.loads((self.root / "licenses/inventory.json").read_text())
        packages = {item["package"] for item in inventory}
        self.assertTrue({"tinyshare", "pandas", "numpy", "requests", "urllib3", "certifi", "idna", "python-dateutil", "six", "pytz", "tzdata"} <= packages)
        self.assertEqual(len({item["file"] for item in inventory}), len(inventory))
        for item in inventory:
            self.assertTrue((self.root / item["file"]).is_file())

    def test_runtime_version_mismatch_is_rejected_before_payload_copy(self):
        with patch.object(builder.sys, "version_info", (3, 13)), self.assertRaises(RuntimeError):
            builder.tinyshare_payload()
