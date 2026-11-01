"""Probe disposable compiler-cache ownership without invoking PyInstaller."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('rh_cache_probe_builder', ROOT / 'packaging/app/build_app.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class PyinstallerCacheIsolationTests(unittest.TestCase):
    def setUp(self):
        self.owner = tempfile.TemporaryDirectory(prefix='compiler-cache-fixture-', dir=ROOT / 'result/acceptance')
        self.addCleanup(self.owner.cleanup)
        self.root = Path(self.owner.name)
        self.command = [str(builder.PYTHON), '-m', 'PyInstaller', '--clean', '--onedir', 'fixture.py']
        self.caches = []

    def runner(self, command, **kwargs):
        self.assertEqual(command, self.command)
        self.assertEqual(kwargs['cwd'], self.root)
        self.assertTrue(kwargs['check'])
        cache = Path(kwargs['env']['PYINSTALLER_CONFIG_DIR'])
        self.assertTrue(cache.is_dir())
        self.assertFalse(cache.is_relative_to(self.root))
        self.assertTrue(cache.name.startswith('researchhelper-pyinstaller-'))
        self.assertEqual(kwargs['env']['RESEARCHHELPER_BUILD_PYTHON'], str(builder.PYTHON))
        (cache / 'binary-cache').write_bytes(b'synthetic compiler output')
        self.caches.append(cache)
        return subprocess.CompletedProcess(command, 0)

    def freeze(self, runner=None):
        with patch.object(builder, 'ROOT', self.root):
            return builder.freeze_python_backend(self.command, runner=runner or self.runner)

    def test_success_cleans_only_owned_compiler_cache(self):
        existing = self.root / 'user-cache'
        existing.mkdir(); (existing / 'protected.txt').write_text('preserve')
        result = self.freeze()
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.caches[0].exists())
        self.assertEqual((existing / 'protected.txt').read_text(), 'preserve')

    def test_build_failure_cleans_cache_and_preserves_error(self):
        def fail(*args, **kwargs):
            self.runner(*args, **kwargs)
            raise subprocess.CalledProcessError(7, self.command)
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            self.freeze(fail)
        self.assertEqual(caught.exception.returncode, 7)
        self.assertFalse(self.caches[0].exists())

    def test_cancel_cleans_cache_without_claiming_success(self):
        def cancel(*args, **kwargs):
            self.runner(*args, **kwargs)
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.freeze(cancel)
        self.assertFalse(self.caches[0].exists())

    def test_cache_identity_is_per_invocation(self):
        self.freeze(); self.freeze()
        self.assertEqual(len(set(self.caches)), 2)
        self.assertTrue(all(not cache.exists() for cache in self.caches))

    def test_parent_environment_is_not_reconfigured(self):
        with patch.dict(os.environ, {'PYINSTALLER_CONFIG_DIR': 'untrusted-shared-cache',
                                    'RESEARCHHELPER_BUILD_PYTHON': 'previous-interpreter'}):
            before = dict(os.environ)
            self.freeze()
            self.assertEqual(dict(os.environ), before)
        self.assertFalse((self.root / 'untrusted-shared-cache').exists())

    def test_cache_cleanup_does_not_remove_collected_artifact(self):
        artifact = self.root / 'collected-backend'
        def collect(*args, **kwargs):
            value = self.runner(*args, **kwargs)
            artifact.write_bytes(b'owned frozen executable')
            return value
        self.freeze(collect)
        self.assertEqual(artifact.read_bytes(), b'owned frozen executable')
        self.assertFalse(self.caches[0].exists())

    def test_freezing_does_not_write_source_cache_directory(self):
        self.freeze()
        self.assertFalse((self.root / '.cache').exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_real_macos_path_uses_the_single_freezer(self):
        import ast
        tree = ast.parse((ROOT / 'packaging/app/build_app.py').read_text())
        macos = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'macos')
        calls = [node for node in ast.walk(macos) if isinstance(node, ast.Call) and
                 isinstance(node.func, ast.Name) and node.func.id == 'freeze_python_backend']
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
