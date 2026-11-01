"""The host Session exposes the same saved-reading contract as the App."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.reasoning import evidence_contract
from tests.test_material_publication_reference import TEXT
from tests.skill_fixture_support import build_skill_fixture

ROOT = Path(__file__).resolve().parents[1]


class MaterialPublicationSessionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='publication-session-', dir=ROOT / 'result/acceptance')
        self.addCleanup(directory.cleanup)
        installation = Path(directory.name) / 'installation'
        build_skill_fixture(installation)
        specification = importlib.util.spec_from_file_location('publication_session', installation / 'core/session.py')
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        self.session = module.Session(Path(directory.name) / 'project')
        task = self.session.create('这份政策何时生效？', objects=['材料示例政策'], cutoff='2026-07-20T18:00:00+08:00')
        self.session.material(task['id'], 'policy.txt', TEXT.encode())
        self.task = self.session.status(task['id'])

    def test_default_tools_remains_the_full_original_contract(self):
        self.assertEqual(self.session.tools(self.task['id'])['evidence'], evidence_contract(self.task))
        self.assertIsNone(self.session.agent.model)

    def test_saved_reading_tools_is_bound_to_actual_views(self):
        directory = self.session.sources(self.task['id'])
        original = self.task['materials'][0]
        bundle = self.session.read(self.task['id'], [dict(artifact_id=original['id'], snapshot_hash=original['snapshot_hash'],
            queries=['生效'], parts=[], reason='核对条款和发布说明', question_ids=['q1'])], directory['binding'])
        with patch('socket.socket.connect', side_effect=AssertionError('No network')):
            contract = self.session.tools(self.task['id'], reading_hash=bundle['reading_hash'])
        self.assertEqual(contract['evidence'], evidence_contract(self.session.status(self.task['id']), bundle['views']))
        self.assertIsNone(self.session.agent.model)

    def test_unknown_reading_does_not_change_task_or_dispatch(self):
        before = fingerprint(self.task)
        with self.assertRaises(ContractError):
            self.session.tools(self.task['id'], reading_hash='not-saved')
        self.assertEqual(fingerprint(self.session.status(self.task['id'])), before)


if __name__ == '__main__':
    unittest.main()
