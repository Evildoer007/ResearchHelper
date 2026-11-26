"""Independent platform dispatch, isolated-store and installer safety checks.

No real Windows compilation, installation, model or data request is performed.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'packaging'))


def load(relative, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DualPlatformTests(unittest.TestCase):
    def test_both_double_click_entries_are_explicit_snapshot_inputs(self):
        from source_snapshot import source_files
        names = {str(path.relative_to(ROOT)) for path in source_files(ROOT)}
        required = {'build-researchhelper-macos.command', 'build-researchhelper-windows.bat',
                    'products/app/desktop/windows/Program.cs',
                    'products/app/desktop/windows/ResearchHelper.Windows.csproj',
                    'packaging/app/windows/ResearchHelper.iss',
                    'packaging/app/windows/build_windows.py', 'packaging/app/windows/check_build_tools.py'}
        self.assertTrue(required <= names)

    def test_platform_selection_rejects_cross_compile_and_wrong_architecture(self):
        from build_environment import build_platform
        for system, machine, expected in (('Darwin', 'arm64', 'macos'), ('Windows', 'AMD64', 'windows')):
            with patch('build_environment.platform.system', return_value=system), patch('build_environment.platform.machine', return_value=machine):
                self.assertEqual(build_platform(), expected)
        for system, machine in (('Darwin', 'x86_64'), ('Windows', 'ARM64'), ('Linux', 'x86_64')):
            with patch('build_environment.platform.system', return_value=system), patch('build_environment.platform.machine', return_value=machine):
                with self.assertRaises(RuntimeError):
                    build_platform()

    def test_windows_store_is_separate_and_explicit_override_still_wins(self):
        from researchhelper import service
        fake = SimpleNamespace(name='nt', environ={'LOCALAPPDATA': '/synthetic-user/AppData/Local'})
        with patch.object(service, 'os', fake):
            self.assertEqual(service.app_store(), Path('/synthetic-user/AppData/Local/ResearchHelper/research-v1'))
            fake.environ['RESEARCHHELPER_STORE'] = '/synthetic-project/store'
            self.assertEqual(service.app_store(), Path('/synthetic-project/store'))

    def test_macos_store_is_unchanged(self):
        from researchhelper import service
        with patch.object(service, 'os', SimpleNamespace(name='posix', environ={})):
            self.assertEqual(service.app_store(), Path.home() / 'Library/Application Support/ResearchHelper/research-v1')

    def test_windows_builder_refuses_to_run_on_mac_before_creating_output(self):
        builder = load('packaging/app/windows/build_windows.py', 'rh_windows_refusal')
        with tempfile.TemporaryDirectory(dir=ROOT / 'result/acceptance') as folder:
            target = Path(folder) / 'candidate'
            with patch.object(builder.platform, 'system', return_value='Darwin'):
                with self.assertRaises(RuntimeError):
                    builder.windows(target)
            self.assertFalse(target.exists())

    def test_assemble_dispatches_only_selected_platform_and_never_installs_skill(self):
        builder = load('packaging/assemble_release.py', 'rh_platform_assembly')
        with tempfile.TemporaryDirectory(dir=ROOT / 'result/acceptance') as folder:
            for platform_name, artifact in (('macos', 'dmg'), ('windows', 'setup')):
                with self.subTest(platform=platform_name):
                    with patch.object(builder, 'OUTPUT', Path(folder) / platform_name),\
                         patch.object(builder, 'build_platform', return_value=platform_name),\
                         patch.object(builder, '_builder') as component,\
                         patch.object(builder, 'skill', return_value='skill') as skill,\
                         patch.object(builder, 'demo', return_value='demo'),\
                         patch.object(builder, 'macos', return_value='mac.dmg') as mac,\
                         patch.object(builder, 'windows', return_value='win.exe') as win,\
                         patch.object(builder, 'release_record', side_effect=lambda value: value):
                        result = builder.build()
                    renderer=component.return_value.macos_pdf.return_value if platform_name=='macos' else None
                    skill.assert_called_once_with(install=False,pdf_renderer=renderer)
                    self.assertEqual(component.call_count,int(platform_name=='macos'))
                    self.assertIn(artifact, result)
                    self.assertEqual(mac.call_count, int(platform_name == 'macos'))
                    self.assertEqual(win.call_count, int(platform_name == 'windows'))
                    if platform_name == 'windows':
                        win.assert_called_once_with('skill')

    def test_windows_receipt_requires_setup_and_does_not_invoke_mac_sandbox(self):
        builder = load('packaging/build_all.py', 'rh_windows_receipt')
        with tempfile.TemporaryDirectory(dir=ROOT / 'result/acceptance') as folder:
            project = Path(folder)
            calls = []
            def runner(command, **options):
                calls.append(command)
                snapshot = Path(options['cwd'])
                payload = snapshot / 'result/build-staging/current'
                payload.mkdir(parents=True)
                artifacts = {}
                for key, name in (('skill', 'synthetic.zip'), ('setup', 'synthetic.exe')):
                    path = payload / name
                    path.write_bytes(b'synthetic unit fixture, not an installer')
                    artifacts[key] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                from artifact_integrity import directory_record
                demo = payload / 'demo'
                demo.mkdir()
                (demo / 'manifest.json').write_text('{}')
                (payload / 'release-manifest.json').write_text(json.dumps({'artifacts': artifacts,
                    'directory_artifacts': {'demo': directory_record(demo)}}))
            with patch.object(builder, 'verify_environment', return_value={'platform': 'windows'}),\
                 patch.object(builder, 'verify_release_binding'),\
                 patch.object(builder, 'source_files', return_value=[]),\
                 patch.object(builder, 'freeze_sources', return_value={}):
                result = builder.build_candidate(project, runner=runner)
            self.assertEqual(result['platform'], 'windows')
            self.assertFalse(result['installed'])
            self.assertNotIn('/usr/bin/sandbox-exec', calls[0])
            self.assertFalse((project / 'dist').exists())

    def test_installer_version_and_arguments_are_rh_only(self):
        installer = load('packaging/app/windows/windows_installer.py', 'rh_installer_contract')
        command = installer.installer_command('ISCC.exe', 'ResearchHelper.iss', '/payload', Path('/output/setup.exe'), '0.10.0', '/icon.ico')
        self.assertIn('/DAppNumericVersion=0.10.0.0', command)
        self.assertNotIn('OptionHelper', ' '.join(command))
        for version in ('bad', '1.2', '99999.0.0', '0.10.0&command'):
            with self.assertRaises(ValueError):
                installer.installer_command('x', 'x', 'x', Path('/setup.exe'), version, 'x')

    def test_missing_windows_installer_cannot_be_marked_built(self):
        builder = load('packaging/build_all.py', 'rh_windows_missing_setup')
        with tempfile.TemporaryDirectory(dir=ROOT / 'result/acceptance') as folder:
            project = Path(folder)
            def runner(command, **options):
                payload = Path(options['cwd']) / 'result/build-staging/current'
                payload.mkdir(parents=True)
                (payload / 'release-manifest.json').write_text(json.dumps({'artifacts': {'skill': {}}}))
            with patch.object(builder, 'verify_environment', return_value={'platform': 'windows'}),\
                 patch.object(builder, 'source_files', return_value=[]),\
                 patch.object(builder, 'freeze_sources', return_value={}):
                with self.assertRaises(RuntimeError):
                    builder.build_candidate(project, runner=runner)
            receipt = json.loads(next(project.glob('result/build-staging/*/candidate.json')).read_text())
            self.assertEqual(receipt['status'], 'failed_not_published')
            self.assertFalse(receipt['published'])
            self.assertFalse(receipt['installed'])

    def test_verification_install_does_not_register_or_make_shortcuts(self):
        source = (ROOT / 'packaging/app/windows/ResearchHelper.iss').read_text(encoding='utf-8-sig')
        self.assertIn('PrivilegesRequired=lowest', source)
        self.assertIn('Uninstallable=NormalInstall', source)
        self.assertIn('CreateUninstallRegKey=NormalInstall', source)
        self.assertIn("{param:VERIFYINSTALL|0}", source)
        self.assertNotIn('OptionHelper', source)

    def test_ico_preserves_approved_source_and_all_sizes(self):
        checker = load('packaging/app/windows/check_build_tools.py', 'rh_icon_validation')
        icon = checker.verify_icon(ROOT)
        with Image.open(icon) as image:
            sizes = image.ico.sizes()
        expected = {(size, size) for size in (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)}
        self.assertEqual(sizes, expected)

    def test_native_host_uses_rh_protocol_and_origin_guard(self):
        source = (ROOT / 'products/app/desktop/windows/Program.cs').read_text()
        self.assertIn('root.TryGetProperty("ready"', source)
        self.assertIn('process.StartInfo.Environment["RESEARCHHELPER_STORE"] = state', source)
        self.assertIn('process.StartInfo.Environment["RESEARCHHELPER_DESKTOP_LIFETIME"] = "stdin"', source)
        self.assertIn('RedirectStandardInput = true', source)
        self.assertIn('process.StandardInput.Close()', source)
        self.assertNotIn('process.Kill(', source)
        self.assertIn('!IsSameOrigin(source) || source.AbsolutePath != "/"', source)
        self.assertIn('researchhelper_action', source)
        self.assertIn('ShowPrintUI', source)
        self.assertNotIn('OPTIONHELPER_URL', source)
        self.assertNotIn('/opt/anaconda3', source)

    def test_windows_keeps_opaque_vendor_com_dependency_without_mac_exclusion(self):
        source = (ROOT / 'packaging/app/windows/build_windows.py').read_text()
        self.assertIn("if package != 'wmi':", source)
        self.assertIn("('wmi', 'win32com.client', 'pythoncom', 'pywintypes')", source)
        requirements = (ROOT / 'packaging/app/windows/requirements.lock').read_text()
        self.assertIn('WMI==1.5.1', requirements)
        self.assertIn('pywin32==311', requirements)

    def test_inno_preflight_reads_version_metadata_not_unreliable_help_text(self):
        checker = load('packaging/app/windows/check_build_tools.py', 'rh_inno_version_probe')
        outputs = [SimpleNamespace(stdout='8.0.408 [C:/dotnet/sdk]'), SimpleNamespace(stdout='6.4.3.0')]
        with patch.object(checker.platform, 'system', return_value='Windows'),\
             patch.object(checker.platform, 'machine', return_value='AMD64'),\
             patch.object(checker.shutil, 'which', side_effect=lambda name: 'C:/tools/' + name),\
             patch.object(checker, 'find_installer_compiler', return_value=Path('/synthetic/ISCC.exe')),\
             patch.object(checker.subprocess, 'run', side_effect=outputs) as run:
            record = checker.check(ROOT)
        self.assertEqual(record['inno_version'], '6.4.3')
        self.assertIn('RESEARCHHELPER_ISCC_PROBE', run.call_args.kwargs['env'])
        self.assertNotIn('/?', run.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
