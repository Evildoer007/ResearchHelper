"""Formal product policy, adoption and durable host review; offline only.

Mock reviewer verdicts test the workflow, not financial reasoning quality.
"""
import copy
import json
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from researchhelper import argument_support as support
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, charge, DOCUMENT_CONTENT_INTENT
from researchhelper.engine import ResearchAgent
from researchhelper.knowledge import ResearchKnowledge
from researchhelper.model import ModelCompletionError
from researchhelper.reports import compose, render

ROOT = Path(__file__).resolve().parents[1]
RETAINED = ROOT / 'result/acceptance/one-sentence-research-v799vtkq/task.json'


def assessed(task, proposal, rejected=(), completion=None):
    request = support.review_request(task, proposal)
    completion = completion or {}
    related = sorted({ident for target in request['targets'] for ident in target['question_ids']})
    wire = dict(version=support.VERSION, review_hash=request['review_hash'], questions=[
        dict(question_id=ident, status=completion.get(ident,'answered'), reason='隔离充分性夹具，不是金融质量批准。',
             missing_evidence=['本问题必要的现金流组成依据'] if completion.get(ident)=='requires_evidence' else [])
        for ident in related], reviews=[
        dict(target_id=item['id'], segments=[dict(text=item['text'],
            verdict='insufficient' if item['id'] in rejected else 'supported',
            basis_refs=['scope:task'], explanation='隔离流程夹具，不代表语义质量验收。')])
        for item in request['targets']])
    return wire, support.decode_review(wire, support.review_schema(request), request)


class ArgumentSupportWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.original = RETAINED.read_bytes()
        self.task = json.loads(self.original)
        self.task['argument_support_contract'] = support.VERSION
        self.task['gaps'], self.task['gap_records'] = [], []
        self.proposal = copy.deepcopy(self.task['host_result'])
        self.agent = ResearchAgent.__new__(ResearchAgent)
        self.agent.knowledge = ResearchKnowledge()
        self.agent._save_model_checkpoint = Mock()
        self.agent._check_run_control = Mock()
        self.model_call = Mock(side_effect=self.model_review)
        self.agent.model = SimpleNamespace(reason_argument_support=self.model_call)
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No networking'))
        guard.start(); self.addCleanup(guard.stop)

    def tearDown(self):
        self.assertEqual(RETAINED.read_bytes(), self.original)

    def model_review(self, task, proposal, *, delivery):
        charge(task['ledger'], 'model_calls', delivery=delivery)
        return json.dumps(assessed(task, proposal)[1], ensure_ascii=False)

    def record(self, rejected=(), completion=None):
        wire, result = assessed(self.task, self.proposal, rejected, completion)
        record = support.make_record(self.task, self.proposal, result, actor='host')
        self.task.setdefault('argument_support_reviews', []).append(record)
        return wire, record

    def consume(self):
        self.agent._answer_questions(self.task, ['q1'], self.proposal, {})
        self.agent._accept_theses(self.task, ['q1'], self.proposal, {})

    def test_formal_engine_enables_policy_and_document_review_is_exempt(self):
        with tempfile.TemporaryDirectory(prefix='support-policy-', dir=ROOT/'result/acceptance') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, require_argument_support=True)
            self.assertTrue(support.enabled(agent.create('研究公开数据')))
            self.assertFalse(support.enabled(agent.create('核对原文', intent=DOCUMENT_CONTENT_INTENT)))
            self.assertFalse(support.enabled(agent.create_case('mechanism')))
            agent.close_runtime()

    def test_unreviewed_direct_consumers_cannot_keep_old_answers_or_theses(self):
        self.consume()
        self.assertNotEqual(self.task['questions'][0]['status'], '已回答')
        self.assertNotIn('answer', self.task['questions'][0])
        self.assertEqual(self.task['theses'], [])

    def test_report_rejects_unreviewed_preexisting_adoption(self):
        with self.assertRaises(ContractError): compose(self.task)

    def test_withheld_answer_is_not_reported_as_missing_financial_documents(self):
        self.consume()
        self.assertTrue(any('未通过论证复核' in gap for gap in self.task['gaps']))
        self.assertFalse(any('未覆盖' in gap for gap in self.task['gaps']))
        self.assertEqual(len(self.task['calculations']), 2)

    def test_reviewed_adoption_passes_full_source_and_report_checks(self):
        self.record(); self.consume()
        self.assertEqual(self.task['questions'][0]['status'], '已回答')
        self.assertEqual(len(self.task['theses']), 1)
        content = compose(self.task)
        self.assertEqual(len(content['calculations']), 2)

    def test_rejected_claim_is_not_adopted_or_rendered(self):
        title = '美联储降息已经直接改善该公司的利润'
        self.proposal['theses'][0]['numeric_segments']['title'] = [{'text': title}]
        self.record(['thesis:0.title']); self.consume()
        self.assertEqual(self.task['theses'], [])
        self.assertNotIn(title, render(compose(self.task), ROOT))

    def test_one_rejected_limitation_rejects_whole_thesis_without_rewriting(self):
        before = copy.deepcopy(self.proposal)
        self.record(['thesis:0.limitation']); self.consume()
        self.assertFalse(self.task['theses'])
        self.assertEqual(self.proposal, before)

    def test_resume_reuses_completed_review_without_another_call(self):
        self.record(); self.task['batch'] += 1
        self.agent._prepare_argument_review(self.task, self.proposal, ['q1'])
        self.consume()
        self.model_call.assert_not_called()
        support.validate_adopted(self.task)

    def test_output_status_changes_do_not_invalidate_semantic_inputs(self):
        _, record = self.record()
        self.task['questions'][0]['status'] = '未回答'
        self.task['questions'][0]['answer'] = '未采用的旧输出'
        support.validate_record(self.task, record)

    def test_judgment_tracking_or_wording_changes_require_new_review(self):
        self.record()
        for key, value in (('judgment', '反对'), ('tracking', dict(indicator='new'))):
            proposal = copy.deepcopy(self.proposal); proposal['theses'][0][key] = value
            self.assertIsNone(support.find_record(self.task, proposal))

    def test_changed_fact_invalidates_old_review_and_adopted_report(self):
        self.record()
        self.task['evidence'][0]['text'] += '修改'
        with self.assertRaises(ContractError): support.validate_adopted(self.task)

    def test_forged_summary_cannot_override_rejected_segments(self):
        _, record = self.record(['thesis:0.title'])
        record['result']['supported'] = True
        with self.assertRaises(ContractError): support.validate_record(self.task, record)

    def test_truthful_answer_can_still_be_incomplete_research(self):
        self.record(completion={'q1':'requires_evidence'}); self.consume()
        question=self.task['questions'][0]
        self.assertEqual(question['status'],'未回答')
        self.assertTrue(question.get('answer'))
        self.assertEqual(question['research_coverage']['status'],'requires_evidence')
        compose(self.task)  # A truthful limited report remains deliverable.

    def test_incomplete_research_cannot_be_forced_to_completed(self):
        self.record(completion={'q1':'requires_evidence'}); self.consume()
        self.task['questions'][0]['status']='已回答'
        with self.assertRaises(ContractError): compose(self.task)

    def test_genuinely_unidentifiable_is_distinct_from_missing_material(self):
        self.record(completion={'q1':'unidentifiable'}); self.consume()
        self.assertEqual(self.task['questions'][0]['status'],'已回答')
        self.assertEqual(self.task['questions'][0]['research_coverage']['missing_evidence'],[])
        compose(self.task)

    def test_latest_review_revokes_previous_approval_for_same_input(self):
        self.record(); self.record(['thesis:0.title'])
        with self.assertRaises(ContractError): support.validate_adopted(self.task)

    def test_last_call_is_consumed_once_and_checkpoint_retained(self):
        self.task['run_mode'] = 'model'
        self.agent._prepare_argument_review(self.task, self.proposal, ['q1'])
        self.assertEqual(self.task['ledger']['used']['model_calls'], 8)
        self.assertEqual(self.task['reasoning_state']['final_delivery']['reason'], 'argument_support')
        self.task['batch'] += 1
        self.agent._prepare_argument_review(self.task, self.proposal, ['q1'])
        self.model_call.assert_called_once()
        self.assertEqual(len(self.task['argument_support_reviews']), 1)

    def test_unknown_or_failed_result_is_not_redispatched_after_resume(self):
        def fail(task, proposal, *, delivery):
            charge(task['ledger'], 'model_calls', delivery=delivery)
            raise ModelCompletionError('synthetic transport failure')
        self.task['run_mode'] = 'model'; self.model_call.side_effect = fail
        self.agent._prepare_argument_review(self.task, self.proposal, ['q1'])
        self.task['batch'] += 1
        self.agent._prepare_argument_review(self.task, self.proposal, ['q1'])
        self.model_call.assert_called_once()
        self.assertFalse(self.task.get('argument_support_reviews'))
        self.consume(); self.assertFalse(self.task['theses'])

    def test_exhausted_budget_or_save_margin_never_dispatches(self):
        for kind, value in (('model_calls', 8), ('seconds', 598)):
            task = copy.deepcopy(self.task); task['run_mode'] = 'model'
            task['ledger']['used'][kind] = value
            self.agent._prepare_argument_review(task, self.proposal, ['q1'])
        self.model_call.assert_not_called()

    def test_cancellation_prevents_adoption_and_keeps_unknown_attempt(self):
        def cancel(task, proposal, *, delivery):
            charge(task['ledger'], 'model_calls', delivery=delivery)
            raise ContractError('synthetic cancellation')
        self.task['run_mode'] = 'model'; self.model_call.side_effect = cancel
        self.agent._check_run_control.side_effect = ContractError('cancelled')
        with self.assertRaises(ContractError):
            self.agent._prepare_argument_review(self.task, self.proposal, ['q1'])
        self.assertFalse(self.task.get('argument_support_reviews'))
        self.assertEqual(next(iter(self.task['reasoning_state']['argument_support_attempts'].values()))['status'], '已派发')

    def host_archive(self, folder):
        task = copy.deepcopy(self.task)
        for key in ('delivery','pending_content','pending_html','host_checkpoint'):
            task.pop(key, None)
        task.update(status='待开始', run_mode='host', revision=0)
        for node in task['nodes']:
            if node.get('kind') in ('answer','thesis') or node['id'] in ('compose','render'):
                node['status'] = '待执行'
        agent = ResearchAgent(ResearchArchive(folder), ROOT, require_argument_support=True)
        agent.archive.save(task)
        return agent, task['id']

    def test_durable_host_checkpoint_review_and_delivery_without_second_model(self):
        with tempfile.TemporaryDirectory(prefix='support-host-', dir=ROOT/'result/acceptance') as folder:
            agent, ident = self.host_archive(folder)
            self.addCleanup(agent.close_runtime)
            checkpoint = agent.start(ident, defer_delivery=True)
            self.assertEqual(checkpoint['status'], '待开始')
            description = agent.argument_review_inputs(ident)
            wire, _ = assessed(agent.archive.get(ident), self.proposal)
            self.assertEqual(wire['review_hash'], description['request']['review_hash'])
            agent.submit_argument_review(ident, wire)
            result = agent.start(ident)
            self.assertEqual(result['status'], '已完成', result.get('gaps'))
            self.assertEqual(result['ledger']['used']['model_calls'], self.task['ledger']['used']['model_calls'])
            self.assertIsNone(agent.model)
            self.assertIn('html', agent.archive.report(ident, result['version']))

    def test_host_stale_submission_and_running_state_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix='support-stale-', dir=ROOT/'result/acceptance') as folder:
            agent, ident = self.host_archive(folder); self.addCleanup(agent.close_runtime)
            task = agent.archive.get(ident); wire, _ = assessed(task, self.proposal)
            task['host_result']['theses'][0]['judgment'] = '反对'
            agent.archive.save(task)
            with self.assertRaises(ContractError): agent.submit_argument_review(ident, wire)
            task = agent.archive.get(ident); task['status'] = '运行中'; agent.archive.save(task)
            with self.assertRaises(ContractError): agent.submit_argument_review(ident, wire)

    def test_malformed_reference_containers_are_contract_failures(self):
        for field, value in (('support', {}), ('counter', 'not-a-list'), ('question_ids', [{}]),
                              ('calculation_refs', [{}])):
            proposal = copy.deepcopy(self.proposal); proposal['theses'][0][field] = value
            with self.assertRaises(ContractError): support.review_request(self.task, proposal)

    def test_incomplete_or_untrusted_review_record_cannot_approve(self):
        for record in ({}, {'status':'已复核'}, {'status':'已派发'}):
            with self.assertRaises(ContractError): support.validate_record(self.task, record)


if __name__ == '__main__':
    unittest.main()
