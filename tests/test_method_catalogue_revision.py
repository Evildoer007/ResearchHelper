"""New methods cannot rewrite the vocabulary of retained planning attempts."""
import copy
import json
import tempfile
import unittest

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, METHOD_REGISTRY, fingerprint, methods_for_task
from researchhelper.engine import ResearchAgent
from researchhelper.output_schema import planning_output_schema
from researchhelper.planning import question_contract, question_specs, validate_question_revision, build_plan
from researchhelper.reasoning import calculation_plan_contract, validate_plan
from researchhelper.reports import compose


DAILY = 'etf_daily_nav_premium_v1'
RETAINED = ROOT / 'result/acceptance/one-sentence-research-vm0gp5kr/task.json'


class MethodCatalogueRevisionTests(unittest.TestCase):
    def setUp(self):
        self.original = RETAINED.read_bytes()
        self.task = json.loads(self.original)

    def tearDown(self):
        self.assertEqual(RETAINED.read_bytes(), self.original)

    def test_retained_actual_rejected_planning_schema_hash_is_identical(self):
        event = self.task['ledger']['dispatches'][-1]
        self.assertEqual(fingerprint(planning_output_schema(self.task)), event['output_schema_hash'])
        self.assertNotIn(DAILY, methods_for_task(self.task))
        self.assertNotIn(DAILY, calculation_plan_contract(self.task)['methods'])
        self.assertNotIn(DAILY, question_contract(self.task)['registered_method_ids'])

    def test_legacy_plan_cannot_inject_new_method_through_plain_json(self):
        question = copy.deepcopy(question_specs(self.task)[0])
        question['method_needs'] = [DAILY]
        with self.assertRaisesRegex(ContractError, '目录版本'):
            validate_plan(self.task, dict(questions=[question], data_requests=[], calculations=[]))

    def test_host_revision_and_direct_execution_plan_keep_the_same_boundary(self):
        question = copy.deepcopy(question_specs(self.task)[0])
        question['method_needs'] = [DAILY]
        with self.assertRaisesRegex(ContractError, '目录版本'):
            validate_question_revision(self.task, [question])
        task = copy.deepcopy(self.task)
        task['questions'] = [question]
        with self.assertRaises(ContractError): build_plan(task)

    def test_method_descriptors_are_detached_and_unknown_versions_are_rejected(self):
        methods_for_task()['etf_daily_nav_premium_v1']['parameters'].clear()
        self.assertTrue(METHOD_REGISTRY[DAILY]['parameters'])
        for version in (True, '2', 0, 3):
            with self.subTest(version=version), self.assertRaises(ContractError):
                methods_for_task(dict(method_catalogue_version=version))

    def test_new_formal_task_tools_schema_and_frozen_content_share_catalogue(self):
        with tempfile.TemporaryDirectory(prefix='method-catalogue-') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            try:
                task = agent.create('解释ETF折溢价', entry='ETF', questions=[dict(id='q1', kind='fact',
                    question='解释ETF折溢价', answer_scope='general_definition',
                    knowledge_needs=['rh_k_etf_premium_measures'])])
                self.assertEqual(task['method_catalogue_version'], 2)
                methods = calculation_plan_contract(task)['methods']
                self.assertEqual(set(methods), set(question_contract(task)['registered_method_ids']))
                shapes = planning_output_schema(task)['properties']['questions']['items']['anyOf']
                ordinary = next(shape for shape in shapes if 'method_needs' in shape['properties'])
                self.assertEqual(set(ordinary['properties']['method_needs']['items']['enum']), set(methods))
                self.assertIn(DAILY, methods)
                done = agent.start(task['id'])
                self.assertEqual(done['status'], '已完成')
                self.assertEqual(compose(done)['method_catalogue_version'], 2)
            finally:
                for worker in agent.close_runtime():
                    worker.join(timeout=5)
                    self.assertFalse(worker.is_alive())


if __name__ == '__main__':
    unittest.main()
