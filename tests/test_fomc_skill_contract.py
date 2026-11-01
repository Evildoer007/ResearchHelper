"""The actual packaged host entry exposes the same new source contract."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from tests import ROOT


class FOMCSkillContractTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='fomc-skill-', dir=ROOT / 'result/acceptance')
        self.addCleanup(folder.cleanup)
        base = Path(folder.name)
        installation = base / 'installation'
        (installation / 'core').mkdir(parents=True)
        with zipfile.ZipFile(ROOT / 'dist/ResearchHelper-Skill-0.10.0.zip') as package:
            manifest = json.loads(package.read('researchhelper/manifest.json'))
            payload = package.read('researchhelper/core/session.py')
            self.assertEqual(hashlib.sha256(payload).hexdigest(), manifest['files']['core/session.py'])
        entry = installation / 'core/session.py'
        entry.write_bytes(payload)
        spec = importlib.util.spec_from_file_location('fomc_packaged_skill_fixture', entry)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        self.session = module.Session(base / 'project')
        self.task = self.session.create('核对FOMC声明', entry='事件')

    def test_packaged_session_exposes_only_registered_fomc_parameters(self):
        tools = self.session.tools(self.task['id'])
        catalogue = next(tool for tool in tools['data_requests']['tools']
                         if tool['source'] == 'official' and tool['operation'] == 'release_catalog')
        self.assertIn(dict(channel='fed_fomc', page='0'), catalogue['available_parameter_sets'])
        self.assertNotIn(dict(channel='fed_fomc', page='1'), catalogue['available_parameter_sets'])
        self.assertIsNone(self.session.agent.model)
        self.assertTrue(self.session.agent.offline)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 0)
        self.assertEqual(self.task['ledger']['used']['data_requests'], 0)

    def test_old_project_task_is_not_auto_upgraded_by_the_host_entry(self):
        self.task.pop('official_policy_channels_version')
        self.session.archive.save(self.task)
        tools = self.session.tools(self.task['id'])
        catalogue = next(tool for tool in tools['data_requests']['tools']
                         if tool['source'] == 'official' and tool['operation'] == 'release_catalog')
        self.assertNotIn('available_parameter_sets', catalogue)
        self.assertEqual(catalogue['parameter_options']['channel'], ['nbs_release'])
