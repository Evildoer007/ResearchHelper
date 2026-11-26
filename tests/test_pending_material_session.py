"""Skill selection uses the same unstarted input fence as App, without a model."""
from pathlib import Path
import importlib.util,tempfile,unittest
from tests import ROOT
from tests.skill_fixture_support import build_skill_fixture

class PendingMaterialSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory(prefix='material-session-',dir=ROOT/'result/acceptance')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root=Path(cls.temporary.name);installation=build_skill_fixture(cls.root/'installation')
        spec=importlib.util.spec_from_file_location('pending_material_session',installation/'core/session.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);cls.Session=module.Session
    def setUp(self):
        self.session=self.Session(self.root/self._testMethodName)
        self.addCleanup(self.session.agent.close_runtime)
        self.task=self.session.create('核对本次保留的材料，不作投资判断。')
        self.payload=b'EXCLUDED_SESSION_INPUT_ONLY'
        self.material=self.session.material(self.task['id'],'remove.txt',self.payload)
    def remove(self):
        state=self.session.status(self.task['id']);contract=self.session.tools(state['id'],scope='first_report')['material_selection']
        return self.session.remove_material(state['id'],'remove.txt',self.material['hash'],
            expected_version=contract['expected_version'],expected_revision=contract['expected_revision'])
    def test_local_selection_retains_the_exact_original_without_model_or_budget_reset(self):
        before=self.session.status(self.task['id']);self.remove();after=self.session.status(self.task['id'])
        self.assertEqual(after['materials'],[]);self.assertEqual(before['ledger'],after['ledger']);self.assertIsNone(self.session.agent.model)
        raw=self.session.archive.retained_originals(after)[0]
        self.assertTrue(raw['excluded_before_start']);self.assertEqual(self.session.archive.original(after,raw['id'])[1],self.payload)
    def test_the_host_can_deliver_without_adopting_the_removed_text(self):
        self.remove()
        done=self.session.draft(self.task['id'],data_requests=[],report=dict(title='材料范围核对',logics=[dict(title='已选范围',analysis='只核对当前保留的输入，不补造原件内容。')]))
        self.assertTrue(done.get('delivery'),done.get('error'))
        report=self.session.archive.report(done['id'],done['version'])
        self.assertNotIn(self.payload.decode(),report['html'])
        self.assertEqual(done['materials'],[])
        self.assertEqual(done['ledger']['used']['model_calls'],0);self.assertEqual(done['ledger']['used']['data_requests'],0)
        self.assertNotIn('material_selection',self.session.tools(done['id'],scope='first_report'))
