"""Planning guidance cannot turn a method limitation into a proven fact."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, METHOD_REGISTRY, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.planning import question_contract, question_specs
from researchhelper.reasoning import PHASE_FIELDS, model_object, validate_plan

ROOT = Path(__file__).resolve().parents[2]


class CapturingPlanner:
    def __init__(self, questions):
        self.output = {'questions': questions, 'data_requests': [], 'calculations': []}
        self.messages = None

    def reason_phase(self, task, messages, phase):
        self.messages = copy.deepcopy(messages)
        return json.dumps(self.output)


class PlanningLimitationBoundaryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(Path(folder.name)), ROOT, offline=True)
        self.task = self.agent.create('核对两日未复权价格变化并说明公司行为未知与总收益的限制。',
                                      entry='ETF', objects=['合成测试ETF'])

    def test_contract_distinguishes_recorded_unknown_from_necessary_original(self):
        policy = question_contract()['obligation_policy']
        self.assertEqual(policy['limitation_guidance_version'], 2)
        rule = policy['limitation_boundary']
        self.assertIn('不登记为必须取证的事实', rule['explain_only'])
        self.assertIn('不能以unknown替代', rule['verify_requested'])
        self.assertIn('不得删除', rule['adopted_plan'])

    def test_price_example_does_not_require_verified_company_action_state(self):
        example = question_contract()['obligation_policy']['requested_outputs']['区间未复权价格变化']
        self.assertIn('允许unknown', example['necessary'])
        self.assertIn('不是额外原件义务', example['necessary'])
        self.assertIn('同证券两端价格', example['necessary'])
        self.assertEqual(example['method'], 'observed_price_return_v1')

    def test_actual_plan_prompt_and_receipt_identify_the_guidance(self):
        original = copy.deepcopy(self.task)
        payload = {'question_contract': question_contract()}
        model = CapturingPlanner(question_specs(self.task))
        output, receipt = model_object(model, self.task, 'plan', payload, PHASE_FIELDS['plan'])
        self.assertIn('说明未知限制不等于核验未知事实', model.messages[0]['content'])
        self.assertEqual(receipt['planning_limitation_guidance_version'], 2)
        self.assertEqual(receipt['context_hash'], fingerprint(payload))
        self.assertEqual(output, model.output)
        self.assertEqual(self.task, original)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 0)

    def test_guidance_does_not_edit_a_model_proposal_or_approve_it(self):
        questions = question_specs(self.task)
        questions[0]['kind'] = 'fact'
        questions[0]['evidence_needs'] = ['同证券两端价格及已核验口径', '明确核验公司行为原件']
        model = CapturingPlanner(questions)
        output, _ = model_object(model, self.task, 'plan',
                                 {'question_contract': question_contract()}, PHASE_FIELDS['plan'])
        self.assertEqual(output['questions'], questions)
        self.assertEqual(validate_plan(self.task, output)[0]['evidence_needs'], questions[0]['evidence_needs'])
        self.assertFalse(self.task['evidence'])

    def test_adopted_missing_fact_cannot_be_removed_under_new_guidance(self):
        questions = [{'id': 'q1', 'question': '核验区间是否分红并计算持有人总回报。',
                      'kind': 'comparison', 'required': True,
                      'evidence_needs': ['区间分红及公司行为原件', '适用的总回报口径']}]
        task = self.agent.create(questions[0]['question'], questions=questions)
        changed = question_specs(task)
        changed[0]['evidence_needs'] = ['适用的总回报口径']
        with self.assertRaises(ContractError):
            self.agent.revise_plan(task['id'], changed, '不能借新指南删除事实义务', task['input_hash'])
        self.assertEqual(self.agent.archive.get(task['id']), task)

    def test_no_method_scope_registry_or_return_semantics_change(self):
        methods = copy.deepcopy(METHOD_REGISTRY)
        contract = question_contract()
        contract['obligation_policy']['requested_outputs'].clear()
        self.assertEqual(METHOD_REGISTRY, methods)
        self.assertIn('未知', methods['observed_price_return_v1']['limitations'])
        self.assertIn('任何状态均不是总收益', methods['observed_price_return_v1']['limitations'])
        self.assertIn('现金分配', question_contract()['obligation_policy']['requested_outputs']['持有人总回报']['necessary'])

    def test_adopted_checkpoint_is_not_replanned_or_charged(self):
        task = copy.deepcopy(self.task)
        task['reasoning_state'] = {'plan': {'adopted_input_hash': task['input_hash']}}
        before = copy.deepcopy(task)
        with patch.object(self.agent, '_active', return_value=task), \
                patch('researchhelper.engine.model_object') as model:
            self.agent._prepare_model_plan(task['id'], task['batch'])
        model.assert_not_called()
        self.assertEqual(task, before)
