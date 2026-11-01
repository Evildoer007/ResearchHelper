"""New build-cache regression; preserve original native startup acceptance.

    No AppKit, foreground window, real model or data provider is started.
"""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class SwiftCacheRelocationTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('isolated_swift_builder', ROOT/'packaging/app/build_app.py')
        self.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.builder)
        folder = tempfile.TemporaryDirectory(prefix='swift-relocation-', dir=ROOT/'result/acceptance')
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name)

    def test_cache_identity_is_stable_but_output_and_toolchain_are_not_interchangeable(self):
        self.builder.OUTPUT = self.directory/'candidate'
        compiler, sdk, version = '/fixture/compiler/swiftc', '/fixture/sdk', 'Swift fixture 1'
        first = self.builder.swift_cache_directory(compiler, sdk, version)
        self.assertEqual(first, self.builder.swift_cache_directory(compiler, sdk, version))
        self.assertNotEqual(first, self.builder.swift_cache_directory(compiler, sdk, 'Swift fixture 2'))
        self.assertNotEqual(first, self.builder.swift_cache_directory(compiler, '/fixture/sdk2', version))
        self.assertNotEqual(first, self.builder.swift_cache_directory('/fixture/compiler2/swiftc', sdk, version))
        self.builder.OUTPUT = self.directory/'published'
        self.assertNotEqual(first.name, self.builder.swift_cache_directory(compiler, sdk, version).name)

    def test_actual_foundation_probe_compiles_after_relocating_the_build_directory(self):
        first = self.directory/'candidate'
        second = self.directory/'published'
        first.mkdir()
        source = first/'probe.swift'
        source.write_text('import Foundation\nprint("ResearchHelper-cache-probe")\n')
        self.builder.OUTPUT = first
        old_flags = self.builder.swift_options()
        subprocess.run(['xcrun', 'swiftc', *old_flags, str(source), '-o', str(first/'probe')],
                       check=True, capture_output=True, timeout=45)
        shutil.copytree(first, second)
        self.builder.OUTPUT = second
        new_flags = self.builder.swift_options()
        self.assertNotEqual(Path(old_flags[1]).name, Path(new_flags[1]).name)
        # Do not remove or rewrite the copied cache to make this pass.
        copied = second/'swift-cache'/Path(old_flags[1]).name
        self.assertTrue(copied.is_dir())
        subprocess.run(['xcrun', 'swiftc', *new_flags, str(second/'probe.swift'), '-o', str(second/'probe')],
                       check=True, capture_output=True, timeout=45)
        completed = subprocess.run([str(second/'probe')], check=True, capture_output=True, text=True, timeout=5)
        self.assertEqual(completed.stdout.strip(), 'ResearchHelper-cache-probe')
        self.assertTrue(copied.is_dir())


if __name__ == '__main__':
    unittest.main()
