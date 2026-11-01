"""Bounded revision eligibility and accounting, not model-quality acceptance."""
import copy
import json
import tempfile
import unittest
from unittest.mock import Mock, patch
from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, charge
from researchhelper.engine import ResearchAgent
from researchhelper.model import ModelCompletionError
from researchhelper.planning import input_fingerprint
from researchhelper.reports import compose
from researchhelper import argument_support

POLICY = 'reviewed_writing_revision_version'
SOURCE = ROOT / 'result/acceptance/one-sentence-research-hkjs04kz/task.json'


class ReviewedWritingRevisionTests(unittest.TestCase):
    def setUp(self):
        self.original = SOURCE.read_bytes()
        self.task = json.loads(self.original)
        self.task.update(status='运行中', **{POLICY: 1})
        self.proposal = copy.deepcopy(self.task['host_result'])
        self.candidate = copy.deepcopy(self.proposal)
        fields = self.candidate['answers']['q1']['numeric_segments']['answer']
        text = next(item for item in fields if 'text' in item)
        text['text'] = '本次研究的观测如下。' + text['text']
        self.agent = ResearchAgent.__new__(ResearchAgent)
        self.agent._reasoning_available = Mock(return_value=True)
        self.agent._save_model_checkpoint = Mock()
        self.agent._check_run_control = Mock()
        def revise(task, selected):
            charge(task['ledger'], 'model_calls')
            return {key: copy.deepcopy(self.candidate[key]) for key in ('answers', 'theses')}
        def review(task, proposal, selected):
            # A synthetic review dispatch, deliberately not a financial
            # approval record. The real pipeline still requires make_record.
            charge(task['ledger'], 'model_calls')
        self.agent._reason_on_verified_material = Mock(side_effect=revise)
        self.agent._prepare_argument_review = Mock(side_effect=review)

    def tearDown(self):
        self.assertEqual(SOURCE.read_bytes(), self.original)

    def run_revision(self):
        return self.agent._revise_reviewed_writing(self.task, self.proposal, ['q1'])

    def test_one_new_analysis_and_review_keep_original_reserve(self):
        result = self.run_revision()
        self.assertEqual(result['answers'], self.candidate['answers'])
        self.assertEqual(self.task['ledger']['used']['model_calls'], 7)
        self.assertEqual(self.task['ledger']['reserve']['model_calls'], 1)
        self.agent._prepare_argument_review.assert_called_once()
        state = self.task['reasoning_state']['reviewed_writing_revision']
        self.assertEqual(state['status'], '复核未完成')
        self.assertFalse(state['automatic_repair']); self.assertFalse(state['further_acquisition_allowed'])
        self.assertEqual(self.task['questions'][0]['status'], '未回答')

    def test_original_evidence_sources_calculations_and_proposal_remain_unchanged(self):
        original = copy.deepcopy((self.task['evidence'], self.task['source_records'], self.task['calculations'], self.proposal))
        self.run_revision()
        self.assertEqual((self.task['evidence'], self.task['source_records'], self.task['calculations'], self.proposal), original)

    def test_at_most_one_revision_across_resume_batches(self):
        self.run_revision(); self.task['batch'] += 1; self.run_revision()
        self.agent._reason_on_verified_material.assert_called_once()

    def test_unknown_prior_revision_does_not_redispatch(self):
        self.task['reasoning_state']['reviewed_writing_revision'] = {'status': '已派发'}
        self.assertIs(self.run_revision(), self.proposal)
        self.agent._reason_on_verified_material.assert_not_called()

    def test_insufficient_budget_or_time_preserves_original_proposal(self):
        self.task['ledger']['used']['model_calls'] = 6
        self.assertIs(self.run_revision(), self.proposal)
        self.task['ledger']['used']['model_calls'] = 5
        self.agent._reasoning_available.return_value = False
        self.assertIs(self.run_revision(), self.proposal)
        self.agent._reason_on_verified_material.assert_not_called()

    def test_missing_or_incomplete_review_cannot_trigger_revision(self):
        self.task['argument_support_reviews'] = []
        self.assertIs(self.run_revision(), self.proposal)
        self.agent._reason_on_verified_material.assert_not_called()

    def test_missing_evidence_is_not_reclassified_as_a_writing_issue(self):
        record = argument_support.find_record(self.task, self.proposal)
        record = copy.deepcopy(record)
        record['result']['question_assessments'][0].update(status='requires_evidence', missing_evidence=['实际缺少的资料'])
        with patch.object(argument_support, 'find_record', return_value=record):
            self.assertIs(self.run_revision(), self.proposal)
        self.agent._reason_on_verified_material.assert_not_called()

    def test_old_and_host_tasks_do_not_change_or_invoke_model(self):
        for changes in ({POLICY: None}, {POLICY: True}, {'run_mode': 'host'}, {'status': '部分完成'}):
            task = copy.deepcopy(self.task); task.update(changes); before = copy.deepcopy(task)
            self.assertIs(self.agent._revise_reviewed_writing(task, self.proposal, ['q1']), self.proposal)
            self.assertEqual(task, before)
        self.agent._reason_on_verified_material.assert_not_called()

    def test_known_model_failure_consumes_slot_without_loop_or_fake_review(self):
        self.agent._reason_on_verified_material.side_effect = ModelCompletionError('Synthetic terminal failure')
        self.assertEqual(self.run_revision()['answers'], self.proposal['answers'])
        self.run_revision()
        self.agent._reason_on_verified_material.assert_called_once()
        self.agent._prepare_argument_review.assert_not_called()
        self.assertEqual(self.task['reasoning_state']['reviewed_writing_revision']['status'], '未采用')

    def test_changed_inputs_reject_late_rewrite_without_review(self):
        def changed(task, selected):
            task['source_records'].pop()
            return self.candidate
        self.agent._reason_on_verified_material.side_effect = changed
        self.run_revision()
        self.agent._prepare_argument_review.assert_not_called()
        self.assertEqual(self.task['reasoning_state']['reviewed_writing_revision']['status'], '未采用')

    def test_feedback_uses_completed_review_and_no_new_acquisition(self):
        payloads = []
        def capture(model, task, phase, payload, fields, **kwargs):
            payloads.append(payload)
            raise ModelCompletionError('No external call')
        self.agent.model = object()
        self.agent._reason_on_verified_material = ResearchAgent._reason_on_verified_material.__get__(self.agent)
        with patch('researchhelper.engine.model_object', side_effect=capture): self.run_revision()
        feedback = payloads[0]['completed_review_feedback']
        self.assertFalse(feedback['automatic_repair'])
        self.assertEqual(feedback['original_proposal'], {key: self.proposal[key] for key in ('answers', 'theses')})
        self.assertEqual(feedback['review']['question_assessments'][0]['status'], 'answered')

    def test_new_policy_is_hashed_and_frozen_not_added_to_old_cases(self):
        legacy = dict(self.task); legacy.pop(POLICY)
        self.assertNotEqual(input_fingerprint(legacy), input_fingerprint(self.task))
        with tempfile.TemporaryDirectory(dir=ROOT / 'result/acceptance') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            task = agent.create('解释价格变化', questions=[dict(id='q1', kind='fact', question='解释价格变化',
                answer_scope='general_definition', knowledge_needs=['rh_k_price_return_measures'])])
            self.assertEqual(compose(agent.start(task['id']))[POLICY], 1)
            self.assertNotIn(POLICY, agent.create_case('mechanism'))


if __name__ == '__main__':
    unittest.main()
