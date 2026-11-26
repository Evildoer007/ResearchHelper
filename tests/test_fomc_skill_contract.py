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
        version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
        with zipfile.ZipFile(ROOT / 'dist' / f'ResearchHelper-Skill-{version}.zip') as package:
            manifest = json.loads(package.read('researchhelper/manifest.json'))
            self.assertEqual(manifest['version'], version)
            self.assertEqual(manifest['runtime']['version'], version)
            payload = package.read('researchhelper/core/session.py')
            self.assertEqual(hashlib.sha256(payload).hexdigest(), manifest['files']['core/session.py'])
        entry = installation / 'core/session.py'
        entry.write_bytes(payload)
        spec = importlib.util.spec_from_file_location('fomc_packaged_skill_fixture', entry)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        self.session = module.Session(base / 'project')
        self.addCleanup(self.session.agent.close_runtime)
        self.task = self.session.create('核对FOMC声明', entry='事件')

    def test_packaged_session_exposes_only_registered_fomc_parameters(self):
        tools = self.session.tools(self.task['id'])
        catalogue = next(tool for tool in tools['data_requests']['tools']
                         if tool['source'] == 'official' and tool['operation'] == 'release_catalog')
        self.assertEqual(next(item for item in catalogue['page_number_profiles'] if item['channel']=='fed_fomc'),
                         dict(channel='fed_fomc',minimum=0,maximum=0))
        from researchhelper.output_schema import _planned_request_schemas, _matches
        shapes=[shape['properties']['parameters'] for shape in _planned_request_schemas(self.task)
                if shape['properties']['operation']['enum']==['release_catalog']]
        self.assertTrue(any(_matches(dict(channel='fed_fomc',page='0'),shape) for shape in shapes))
        self.assertFalse(any(_matches(dict(channel='fed_fomc',page='1'),shape) for shape in shapes))
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
        self.assertEqual(catalogue['page_number_profiles'],[dict(channel='nbs_release',minimum=0,maximum=3)])
        self.assertNotIn('official_policy_channels_version',self.session.status(self.task['id']))
