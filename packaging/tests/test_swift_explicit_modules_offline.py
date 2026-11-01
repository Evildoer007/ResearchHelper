"""Build-option contracts; no SDK mutation, model or foreground App."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


class SwiftExplicitModuleTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('rh_swift_explicit_fixture',
                                                      ROOT / 'packaging/app/build_app.py')
        self.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.builder)
        folder = tempfile.TemporaryDirectory(prefix='swift-options-', dir=ROOT / 'result/acceptance')
        self.addCleanup(folder.cleanup)
        self.builder.OUTPUT = Path(folder.name)

    def options(self, help_text):
        responses = ['/fixture/toolchain/bin/swiftc\n', '/fixture/sdk\n',
                     'Swift fixture 6\n', help_text]
        with patch.object(self.builder.subprocess, 'check_output', side_effect=responses) as command:
            flags = self.builder.swift_options()
        self.assertEqual(command.call_count, 4)
        self.assertEqual(command.call_args.args[0], ['/fixture/toolchain/bin/swiftc', '-help-hidden'])
        return flags

    def test_supported_toolchain_prebuilds_dependencies_with_bounded_parallelism(self):
        flags = self.options('  -explicit-module-build  Prebuild module dependencies\n')
        self.assertEqual(flags[0], '-module-cache-path')
        self.assertTrue(Path(flags[1]).is_relative_to(self.builder.OUTPUT))
        self.assertEqual(flags[2:4], ['-sdk', '/fixture/sdk'])
        self.assertEqual(flags[4:], ['-explicit-module-build', '-j', '4'])

    def test_older_toolchain_uses_supported_implicit_mode(self):
        flags = self.options('  -module-cache-path <value>  Specifies cache path\n')
        self.assertEqual(flags[2:], ['-sdk', '/fixture/sdk'])
        self.assertNotIn('-explicit-module-build', flags)

    def test_help_prose_is_not_a_supported_flag_declaration(self):
        flags = self.options('Notes mention -explicit-module-build but do not advertise it.\n')
        self.assertNotIn('-explicit-module-build', flags)


if __name__ == '__main__':
    unittest.main()
