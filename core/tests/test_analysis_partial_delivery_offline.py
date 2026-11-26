"""Reject malformed model output without losing independently verified work."""

import copy
import unittest
from unittest.mock import patch

from researchhelper.reasoning import QueryReferenceFormatError, analysis_contract
from researchhelper.reports import compose
from tests import test_autonomous_reasoning_offline as autonomous
from tests import test_numeric_composition_cache_offline as numeric


class AnalysisPartialDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = autonomous.AutonomousReasoningTests()
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)

    def policy(self):
        task = self.fixture.policy()
        spec = self.fixture.policy_spec(task)
        spec['publication']['basis_ref']['kind'] = 'material'
        return task, spec

    def test_invalid_reference_shape_keeps_real_fixture_evidence_and_saves_partial_report(self):
        task, spec = self.policy()
        proposal = self.fixture.answer()
        proposal['answers']['q1']['provenance_refs'] = 'DO_NOT_ARCHIVE_REJECTED_REFERENCE'
        done, model = self.fixture.run_model(task, [self.fixture.plan(task), {'evidence_specs': [spec]}, proposal])
        self.assertEqual(done['status'], '部分完成', done.get('error'))
        self.assertEqual(len(done['evidence']), 1)
        self.assertEqual(done['questions'][0]['status'], '未回答')
        self.assertFalse(done['theses'])
        self.assertIsNotNone(done['delivery'])
        self.assertEqual(done['ledger']['used']['model_calls'], 3)
        self.assertEqual(len(model.requests), 3)
        checkpoint = done['reasoning_state']['analysis']
        self.assertTrue(checkpoint['output_rejected'])
        self.assertNotIn('input_digest', checkpoint)
        self.assertFalse(checkpoint['numeric_review']['accepted'])
        self.assertNotIn('DO_NOT_ARCHIVE_REJECTED_REFERENCE', str(done))
        diagnostic = done['ledger']['dispatches'][-1]['output_contract']['item_structure']
        self.assertEqual(diagnostic['invalid_fields'], ['answers.*.provenance_refs'])
        report = self.fixture.agent.export(done['id'])
        self.assertIn('未采用本次回答', report['html'])
        self.assertIn(spec['text'], report['html'])

    def test_independent_knowledge_answer_is_not_lost_to_bad_fact_answer(self):
        task, spec = self.policy()
        plan = self.fixture.plan(task)
        plan['questions'].append({'id': 'definition', 'question': '解释ETF口径', 'kind': 'fact',
            'answer_scope': 'general_definition', 'knowledge_needs': ['rh_k_etf_measures']})
        proposal = self.fixture.answer(); proposal['answers']['q1']['provenance_refs'] = {'invalid': 'not-an-array'}
        done, _ = self.fixture.run_model(task, [plan, {'evidence_specs': [spec]}, proposal])
        self.assertEqual(done['status'], '部分完成', done.get('error'))
        self.assertEqual([q['status'] for q in done['questions']], ['未回答', '已回答'])
        self.assertTrue(done['delivery'])
        self.assertFalse(done['questions'][1]['knowledge_integrity']['fact_approval'])

    def test_scope_injection_is_still_hard_rejected_not_saved_as_model_answer(self):
        task, spec = self.policy()
        proposal = dict(self.fixture.answer(), evidence=[{'id': 'untrusted-new-source'}])
        done, _ = self.fixture.run_model(task, [self.fixture.plan(task), {'evidence_specs': [spec]}, proposal])
        self.assertEqual(done['status'], '失败')
        self.assertIsNone(done['delivery'])
        self.assertEqual(len(done['evidence']), 1)

    def test_rejected_attempt_does_not_call_model_again_in_the_same_batch(self):
        fixture = numeric.NumericCompositionCacheTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        task = fixture.execution_task(); used = copy.deepcopy(task['ledger']['used'])
        agent = fixture.fixture.agent
        run = (task['id'], task['version'], task['batch'])
        agent._begin_run_control(*run)
        self.addCleanup(agent._finish_run_control, *run)
        with patch.object(agent, '_reasoning_available', return_value=True), \
                patch.object(agent, '_save_model_checkpoint'), \
                patch.object(agent, '_reason_on_verified_material', side_effect=QueryReferenceFormatError('invalid')) as model:
            done = agent._step(task, 'answer_q1', {})
            agent._step(done, 'answer_q1', {})
        self.assertEqual(model.call_count, 1)
        self.assertEqual(done['ledger']['used'], used)
        self.assertFalse(done['reasoning_state']['analysis']['numeric_review']['accepted'])
        self.assertTrue(compose(done)['calculations'])

    def test_query_submission_shape_is_explicit_without_promoting_a_candidate(self):
        task, _ = self.policy()
        grammar = analysis_contract(task)['answer_item_structure']['provenance_refs']
        self.assertEqual(grammar['type'], 'array')
        self.assertEqual(grammar['items']['type'], 'object')
        self.assertFalse(grammar['items']['additional_fields'])
        self.assertEqual(grammar['items']['evidence_need_ids']['type'], 'array')

    def test_rejected_supplement_cannot_reuse_old_affected_answer_as_a_new_answer(self):
        fixture = numeric.NumericCompositionCacheTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        task = fixture.execution_task()
        prior = fixture.proposal()
        task['host_result'] = copy.deepcopy(prior)
        task['supplement_history'] = [{'status': '执行中', 'round': 1, 'affected_questions': ['q1'],
            'before': {'evidence': [], 'calculations': []}}]
        agent = fixture.fixture.agent
        run = (task['id'], task['version'], task['batch'])
        agent._begin_run_control(*run)
        self.addCleanup(agent._finish_run_control, *run)
        with patch.object(agent, '_reasoning_available', return_value=True), \
                patch.object(agent, '_save_model_checkpoint'), \
                patch.object(agent, '_reason_on_verified_material', side_effect=QueryReferenceFormatError('invalid')):
            done = agent._step(task, 'answer_q1', {})
        self.assertNotIn('q1', done['host_result']['answers'])
        self.assertEqual(done['questions'][0]['status'], '未回答')
        self.assertFalse(done['reasoning_state']['analysis']['numeric_review']['accepted'])
        self.assertNotIn('input_digest', done['reasoning_state']['analysis'])
