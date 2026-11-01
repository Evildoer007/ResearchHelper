"""Explicit dependency facts, not inherited conclusions or live research approval."""
import copy
import hashlib
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import Mock, patch

from researchhelper import argument_support as support
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import (ContractError, context, evidence_applies_to_answer,
    calculation_applies_to_answer, synthesis_dependency_questions, DOCUMENT_CONTENT_INTENT)
from researchhelper.engine import ResearchAgent
from researchhelper.numeric_integrity import bind_numeric_statement
from researchhelper.output_schema import analysis_output_schema, decode_analysis_citations, _matches
from researchhelper.reasoning import analysis_contract
from researchhelper.reports import compose, check_content

ROOT = Path(__file__).resolve().parents[1]
RETAINED = ROOT/'result/acceptance/one-sentence-research-tqtuu8a1/task.json'


class DependencySynthesisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_hash = hashlib.sha256(RETAINED.read_bytes()).hexdigest()
        cls.saved = json.loads(RETAINED.read_text())

    @classmethod
    def tearDownClass(cls):
        if hashlib.sha256(RETAINED.read_bytes()).hexdigest() != cls.original_hash:
            raise AssertionError('Retained failed study changed')

    def setUp(self):
        self.task = dict(self.saved, dependency_synthesis_version=1,
                         questions=copy.deepcopy(self.saved['questions']),
                         evidence=list(self.saved['evidence']),
                         calculations=copy.deepcopy(self.saved['calculations']),
                         gaps=[], gap_records=[], theses=[])
        self.parent = next(q for q in self.task['questions'] if q['id'] == 'q1')
        self.fact = self.task['evidence'][0]
        self.calculation = self.task['calculations'][0]
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No network'))
        guard.start(); self.addCleanup(guard.stop)

    def proposal(self):
        refs = list(self.calculation['input_refs'])
        return dict(answers={'q1':dict(refs=refs, calculation_refs=[self.calculation['id']],
            numeric_segments={'answer':[dict(text='以下是登记财务计算的综合描述，不代表投资结论。'),
                dict(source='calculation', ref=self.calculation['id'], path='value', format='signed', digits=2)]})},
            theses=[])

    def test_new_formal_tasks_enable_policy_but_fixture_and_document_do_not(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'result/acceptance') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            try:
                task = agent.create('核对当前财务资料', research_mode='current')
                self.assertEqual(task['dependency_synthesis_version'], 1)
                self.assertNotIn('dependency_synthesis_version', agent.create_case('mechanism'))
                self.assertNotIn('dependency_synthesis_version', agent.create('核对原件', intent=DOCUMENT_CONTENT_INTENT))
            finally:
                agent.close_runtime()

    def test_retained_legacy_parent_remains_without_eligible_references(self):
        contract = analysis_contract(self.saved, ['q1'])['by_question']['q1']
        self.assertEqual(contract['evidence_ids'], [])
        self.assertEqual(contract['calculations'], [])
        self.assertEqual(self.saved['status'], '部分完成')

    def test_actual_dependency_parent_can_explicitly_select_17_facts_and_two_calculations(self):
        before = copy.deepcopy((self.task['questions'], self.task['calculations'], self.task['ledger']))
        contract = analysis_contract(self.task, ['q1'])['by_question']['q1']
        self.assertEqual(len(contract['evidence_ids']), 17)
        self.assertEqual(len(contract['calculations']), 2)
        self.assertEqual(contract['dependency_inputs']['questions'], ['q2', 'q3', 'q4', 'q5'])
        self.assertEqual(before, (self.task['questions'], self.task['calculations'], self.task['ledger']))
        self.assertEqual([e['question_ids'] for e in self.task['evidence']],
                         [e['question_ids'] for e in self.saved['evidence']])

    def test_model_payload_contains_selected_parent_facts_not_empty_projection(self):
        values = ResearchAgent._reasoning_evidence(self.task, ['q1'])
        self.assertEqual({item['id'] for item in values}, {item['id'] for item in self.task['evidence']})
        self.assertTrue(all('q1' not in item['question_ids'] for item in values))

    def test_no_implicit_sibling_or_new_evidence_requirement_coverage(self):
        self.parent['evidence_needs'] = ['另行核验未来估值']
        self.assertFalse(evidence_applies_to_answer(self.task, self.fact, 'q1'))
        self.assertFalse(calculation_applies_to_answer(self.task, self.calculation, 'q1'))

    def test_new_method_obligation_still_requires_its_own_registration(self):
        self.parent['method_needs'] = ['gross_margin_v1']
        self.assertEqual(synthesis_dependency_questions(self.task, 'q1'), set())

    def test_unrelated_question_is_not_an_input_even_when_same_task(self):
        self.parent['dependencies'] = ['q4']
        self.assertEqual(synthesis_dependency_questions(self.task, 'q1'), {'q2', 'q4'})
        unrelated = next(item for item in self.task['evidence'] if item['question_ids'] == ['q3'])
        self.assertFalse(evidence_applies_to_answer(self.task, unrelated, 'q1'))

    def test_forecast_unverified_cross_task_and_document_sources_are_excluded(self):
        for field, value in (('nature','管理层展望'), ('nature','预测假设'), ('task_id','foreign'),
                ('research_version',999), ('source_check','待核验'), ('applicability_check','待核验'),
                ('usage_scope','document_content')):
            with self.subTest(field=field, value=value):
                item = dict(self.fact, **{field:value})
                self.assertFalse(evidence_applies_to_answer(self.task, item, 'q1'))

    def test_historical_definition_and_non_reviewed_tasks_do_not_gain_policy(self):
        for changes in (dict(research_mode='historical'),dict(intent=DOCUMENT_CONTENT_INTENT),
                        dict(argument_support_contract=None),dict(fixture='mechanism')):
            self.assertEqual(synthesis_dependency_questions(dict(self.task, **changes), 'q1'), set())
        self.parent['answer_scope'] = 'general_definition'
        self.assertEqual(synthesis_dependency_questions(self.task, 'q1'), set())

    def test_cycle_unknown_dependency_and_unknown_policy_are_rejected(self):
        for dependency in ('q1', 'not_registered'):
            self.parent['dependencies'] = [dependency]
            with self.assertRaises(ContractError): synthesis_dependency_questions(self.task, 'q1')
        with self.assertRaises(ContractError):
            synthesis_dependency_questions(dict(self.task, dependency_synthesis_version=2), 'q1')

    def test_calculation_requires_complete_approved_upstream_inputs(self):
        for changes in (dict(task_id='foreign'), dict(research_version=999), dict(method='unregistered'),
                        dict(input_refs=['not_retained'])):
            self.assertFalse(calculation_applies_to_answer(self.task, dict(self.calculation, **changes), 'q1'))
        ref = self.calculation['input_refs'][0]
        self.task['evidence'] = [dict(item, source_check='待核验') if item['id']==ref else item
                                 for item in self.task['evidence']]
        self.assertFalse(calculation_applies_to_answer(self.task, self.calculation, 'q1'))

    def test_parent_only_schema_accepts_explicit_prose_and_calculation_references(self):
        schema = analysis_output_schema(self.task, ['q1'])
        wire = dict(version=2, binding=context(self.task), answers=[dict(question_id='q1',
            segments=[dict(text='按已核验原文综合。', refs=[self.fact['id']], counter_refs=[]),
                      dict(source='calculation', ref=self.calculation['id'], path='value', format='signed', digits=2)],
            knowledge_refs=[], provenance_refs=[])], theses=[])
        self.assertTrue(_matches(wire, schema))
        answer = decode_analysis_citations(wire, schema, self.task)['answers']['q1']
        self.assertEqual(answer['calculation_refs'], [self.calculation['id']])
        self.assertEqual(set(answer['refs']), {self.fact['id'], *self.calculation['input_refs']})
        self.assertLess(len(answer['refs']), 17)

    def test_numeric_binding_accepts_explicit_result_but_not_missing_inputs(self):
        answer = self.proposal()['answers']['q1']
        value = bind_numeric_statement(self.task, dict(answer, question_ids=['q1']), ['answer'],
                                       answer['refs'], answer['calculation_refs'])
        self.assertTrue(value['record'])
        with self.assertRaises(ContractError):
            bind_numeric_statement(self.task, dict(answer, question_ids=['q1']), ['answer'],
                                   answer['refs'][:-1], answer['calculation_refs'])

    def test_review_binds_policy_without_using_mutable_answer_status(self):
        proposal = self.proposal()
        request = support.review_request(self.task, proposal)
        self.assertEqual(request['binding_version'], 6)
        self.assertEqual(request['scope']['dependency_synthesis']['inputs']['q1'], ['q2','q3','q4','q5'])
        for question in self.task['questions']:
            question['status'] = '已回答'
        self.assertEqual(support.review_request(self.task, proposal)['review_hash'], request['review_hash'])

    def test_unanswered_dependencies_and_missing_review_still_block_adoption(self):
        agent = ResearchAgent.__new__(ResearchAgent)
        agent._save_model_checkpoint = Mock()
        agent._check_run_control = Mock()
        agent._answer_questions(self.task, ['q1'], self.proposal(), {})
        self.assertNotEqual(self.parent['status'], '已回答')
        self.assertFalse(self.task['theses'])

    def record_fixture_review(self, proposal):
        """Synthetic verdict tests the pipeline only, never financial semantics."""
        request = support.review_request(self.task, proposal)
        related = sorted({ident for item in request['targets'] for ident in item['question_ids']})
        wire = dict(version=support.VERSION, review_hash=request['review_hash'],
            questions=[dict(question_id=ident, status='answered', reason='隔离流程夹具，不是金融质量验收。',
                            missing_evidence=[]) for ident in related],
            reviews=[dict(target_id=item['id'], segments=[dict(text=item['text'], verdict='supported',
                basis_refs=['scope:task'], explanation='仅检查引用和采用链路，不代表金融支持关系已获验证。')])
                for item in request['targets']])
        result = support.decode_review(wire, support.review_schema(request), request)
        self.task['argument_support_reviews'] = [support.make_record(self.task, proposal, result, actor='host')]

    def test_even_reviewed_parent_is_blocked_until_all_dependencies_are_answered(self):
        proposal = self.proposal()
        self.record_fixture_review(proposal)
        for question in self.task['questions']:
            question['status'] = '未回答'
        agent = ResearchAgent.__new__(ResearchAgent)
        agent._answer_questions(self.task, ['q1'], proposal, {})
        self.assertNotEqual(self.parent['status'], '已回答')
        self.assertTrue(any('核心问题未回答' in gap for gap in self.task['gaps']))
        self.assertTrue(all(q['status']=='未回答' for q in self.task['questions']))

    def test_reviewed_dependency_pipeline_composes_and_freezes_report(self):
        contract = analysis_contract(self.task)['by_question']
        answers = {}
        for question in self.task['questions']:
            inputs = contract[question['id']]
            answers[question['id']] = dict(refs=inputs['evidence_ids'],
                calculation_refs=[item['id'] for item in inputs['calculations']],
                numeric_segments={'answer':[dict(text='保留原件适用范围，以下为登记计算结果。')]+[
                    dict(source='calculation', ref=item['id'], path='value', format='signed', digits=2)
                    for item in inputs['calculations']]})
        proposal = dict(answers=answers, theses=[dict(id='scope_fixture', question_ids=['q1'],
            judgment='证据不足', support=contract['q1']['evidence_ids'], counter=[],
            calculation_refs=[item['id'] for item in contract['q1']['calculations']],
            numeric_segments=dict(title=[dict(text='单项比率不能独立证明盈利质量。')],
                investment_meaning=[dict(text='不据此形成买卖建议。')],
                limitation=[dict(text='此为引用和报告保存的隔离夹具，不是实际投资研究验收。')]))])
        self.record_fixture_review(proposal)
        self.task['host_result'] = proposal
        agent = ResearchAgent.__new__(ResearchAgent)
        agent._answer_questions(self.task, [q['id'] for q in self.task['questions']], proposal, {})
        self.assertTrue(all(q['status']=='已回答' for q in self.task['questions']), self.task['gaps'])
        agent._accept_theses(self.task, ['q1'], proposal, None)
        self.assertEqual(len(self.task['theses']), 1, self.task['gaps'])
        content = compose(self.task)
        check_content(content)
        self.assertEqual(content['dependency_synthesis_version'], 1)
        self.assertEqual(content['calculations'], self.task['calculations'])
        with tempfile.TemporaryDirectory(dir=ROOT/'result/acceptance') as folder:
            archive = ResearchArchive(folder)
            archive.save(dict(self.task, revision=0))
            restored = archive.get(self.task['id'])
            self.assertEqual(restored['dependency_synthesis_version'], 1)
            self.assertEqual(compose(restored)['content_hash'], content['content_hash'])
        self.task.pop('dependency_synthesis_version')
        with self.assertRaises(ContractError): compose(self.task)

    def test_missing_parent_review_cannot_reuse_an_upstream_review(self):
        self.record_fixture_review(self.proposal())
        self.task['dependency_synthesis_version'] = 2
        with self.assertRaises(ContractError): support.review_request(self.task, self.proposal())


if __name__ == '__main__':
    unittest.main()
