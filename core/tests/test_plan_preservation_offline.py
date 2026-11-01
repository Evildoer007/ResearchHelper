"""Exact planning obligations and retry classification, no external calls."""

import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.planning import plan_preservation_contract, question_specs
from researchhelper.reasoning import validate_plan

ROOT = Path(__file__).resolve().parents[2]


class PlanPreservationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='plan-preservation-')
        self.addCleanup(directory.cleanup)
        self.agent = ResearchAgent(ResearchArchive(directory.name), ROOT, offline=True)

    def task(self):
        return self.agent.create('核对两类利率', questions=[{'id': 'rates', 'question': '两类利率分别是多少？',
            'kind': 'fact', 'evidence_needs': ['一年期LPR', '五年期以上LPR']}])

    def proposal(self, task):
        return {'questions': question_specs(task), 'data_requests': [], 'calculations': []}

    def test_contract_preserves_exact_strings_not_semantic_substitutions(self):
        task = self.task()
        contract = plan_preservation_contract(task)
        self.assertEqual(contract['binding']['input_hash'], task['input_hash'])
        protected = contract['protected_questions'][0]
        self.assertEqual(protected['retained_list_items']['evidence_needs'], ['一年期LPR', '五年期以上LPR'])
        proposal = self.proposal(task)
        proposal['questions'][0]['evidence_needs'][0] = '一年期LPR的数值及期限标识'
        with self.assertRaises(ContractError):
            validate_plan(task, proposal)
        proposal['questions'][0]['evidence_needs'].append('一年期LPR')
        self.assertEqual(validate_plan(task, proposal)[0]['evidence_needs'][0], '一年期LPR的数值及期限标识')

    def test_exact_scalar_fields_and_known_dependencies_still_protected(self):
        task = self.task()
        for field, value in [('question', '改写的问题'), ('kind', 'investment'), ('required', False), ('group', 'other')]:
            proposal = self.proposal(task)
            proposal['questions'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                validate_plan(task, proposal)

    def test_optional_questions_are_not_made_mandatory_by_contract(self):
        task = self.agent.create('核对日期', questions=[{'id': 'core', 'question': '日期？', 'kind': 'fact'},
            {'id': 'optional', 'question': '金额？', 'kind': 'fact', 'required': False}])
        self.assertEqual([q['id'] for q in plan_preservation_contract(task)['protected_questions']], ['core'])

    def test_default_first_decomposition_can_classify_without_rewriting_user_question(self):
        task = self.agent.create('政策何时生效？')
        self.assertEqual(plan_preservation_contract(task)['protected_questions'][0]['exact_fields'],
                         {'question': task['question'], 'required': True})
        proposal = self.proposal(task)
        proposal['questions'][0]['kind'] = 'fact'
        self.assertEqual(validate_plan(task, proposal)[0]['kind'], 'fact')

    def test_rejected_default_first_proposal_does_not_lock_default_investment_kind_on_retry(self):
        task = self.agent.create('政策何时生效？')
        task['batch'] = 2
        task['ledger']['used']['model_calls'] = 1
        before = copy.deepcopy(task)
        proposal = self.proposal(task)
        proposal['questions'][0]['kind'] = 'fact'
        self.assertEqual(validate_plan(task, proposal)[0]['kind'], 'fact')
        self.assertTrue(plan_preservation_contract(task)['initial_decomposition'])
        self.assertEqual(task, before)

    def test_adopted_plan_or_new_research_version_cannot_use_default_reclassification(self):
        for field in ('plan_version', 'version'):
            task = self.agent.create('政策何时生效？')
            task[field] = 2
            proposal = self.proposal(task)
            proposal['questions'][0]['kind'] = 'fact'
            with self.subTest(field=field), self.assertRaises(ContractError):
                validate_plan(task, proposal)
            self.assertFalse(plan_preservation_contract(task)['initial_decomposition'])

    def test_pure_legacy_validator_context_is_not_a_versioned_request_contract(self):
        task = self.agent.create('政策何时生效？')
        del task['version']
        proposal = self.proposal(task)
        proposal['questions'][0]['kind'] = 'fact'
        self.assertEqual(validate_plan(task, proposal)[0]['kind'], 'fact')
        with self.assertRaises(KeyError):
            plan_preservation_contract(task)

    def test_actual_app_planner_receives_same_contract_without_changing_task(self):
        task = self.task()
        received = []
        def model_call(model, current, phase, payload, fields):
            received.append(payload['plan_preservation_contract'])
            return self.proposal(current), {'phase': phase}
        self.agent.offline = False
        self.agent.model = object()
        with patch('researchhelper.engine.model_object', side_effect=model_call):
            self.agent.start(task['id'])
        self.assertEqual(received, [plan_preservation_contract(task)])

    def test_skill_tools_use_current_task_contract_readonly_without_second_model(self):
        spec = importlib.util.spec_from_file_location('preservation_skill', ROOT / 'core/session.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix='preservation-project-') as directory:
            session = module.Session(directory)
            task = session.create('核对利率', questions=[{'id': 'q1', 'question': '利率？', 'kind': 'fact',
                'evidence_needs': ['一年期LPR']}])
            before = copy.deepcopy(session.archive.get(task['id']))
            self.assertEqual(session.tools(task['id'])['plan_preservation'], plan_preservation_contract(task))
            self.assertEqual(session.archive.get(task['id']), before)
            self.assertIsNone(session.agent.model)


if __name__ == '__main__':
    unittest.main()
