"""Development launch and test settings belong to their owning directories."""
import importlib.util
import json
import sys
import unittest
from unittest.mock import patch

from tests import ROOT


class DevelopmentEntryLayoutTests(unittest.TestCase):
    def test_editor_launch_uses_the_owned_development_entry(self):
        config = json.loads((ROOT / '.vscode/launch.json').read_text())
        for launch in config['configurations']:
            self.assertIn('ResearchHelper', launch['name'])
            self.assertNotIn('Research Helper', launch['name'])
            target = launch['program'].replace('${workspaceFolder}', str(ROOT))
            self.assertEqual(target, str(ROOT / 'products/app/backend/development.py'))
            self.assertTrue((ROOT / 'products/app/backend/development.py').is_file())

    def test_editor_uses_existing_machinelearning_interpreter(self):
        settings = json.loads((ROOT / '.vscode/settings.json').read_text())
        self.assertEqual(settings['python.defaultInterpreterPath'], '/opt/anaconda3/envs/MachineLearning/bin/python')
    def load_entry(self):
        spec = importlib.util.spec_from_file_location('rh_development_layout',
            ROOT / 'products/app/backend/development.py')
        entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(entry)
        return entry

    def test_root_has_no_loose_python_entries_or_test_project_config(self):
        self.assertFalse(list(ROOT.glob('*.py')))
        self.assertFalse((ROOT / 'pyproject.toml').exists())

    def test_development_import_never_launches_a_browser_or_server(self):
        with patch('researchhelper.service.serve') as serve:
            self.assertEqual(self.load_entry().PROJECT, ROOT)
            serve.assert_not_called()

    def test_development_entry_keeps_the_existing_explicit_workspace_action(self):
        with patch('researchhelper.service.serve') as serve:
            entry = self.load_entry()
            entry.main()
            serve.assert_called_once_with(open_browser=True)

    def test_frozen_source_map_uses_owned_backend_entries(self):
        sys.path.insert(0, str(ROOT / 'packaging'))
        from source_snapshot import source_files
        names = {p.relative_to(ROOT).as_posix() for p in source_files(ROOT)}
        self.assertIn('products/app/backend/entry.py', names)
        self.assertIn('products/app/backend/development.py', names)
        self.assertFalse({'main.py', 'launcher.py', 'pyproject.toml'} & names)
        self.assertNotIn('tests/pytest.ini', names)

    def test_pytest_paths_resolve_to_the_same_active_test_directories(self):
        import pytest
        config = pytest.Config.fromdictargs({}, ['-c', str(ROOT / 'tests/pytest.ini')])
        try:
            self.assertEqual(config.rootpath, ROOT / 'tests')
            self.assertEqual({p.resolve() for p in config.getini('pythonpath')}, {ROOT, ROOT / 'core/src'})
            actual = {(config.rootpath / p).resolve() for p in config.getini('testpaths')}
            self.assertEqual(actual, {ROOT / p for p in
                ('tests', 'core/tests', 'products/app/tests', 'packaging/tests')})
        finally:
            config._ensure_unconfigure()
