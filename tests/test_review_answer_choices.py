"""Offline answer-choice controls, not autonomous financial quality evidence.

The existing official NAV fixture and synthetic close pass the normal data,
numeric and archive pipeline. Reviewer decisions here are deliberately authored.
"""
import copy
import unittest
from unittest.mock import patch

try:
    from . import test_sse_nav_delivery_chain as chain
except ImportError:
    import test_sse_nav_delivery_chain as chain
from researchhelper import argument_support as support
from researchhelper import review_answer_choices as choices
from researchhelper.contracts import ContractError, fingerprint


class ReviewAnswerChoicesTests(unittest.TestCase):
    def setUp(self):
        self.control = chain.SSENavDeliveryChainTests()
        self.control.setUp()
        self.addCleanup(self.control.doCleanups)
        task = self.control.task
        task[choices.POLICY] = 1
        self.control.archive.save(task)
        proposal = self.control.proposal()
        answer = proposal['answers']['q1']
        answer['numeric_segments']['answer'][2]['text'] = '，示例收盘价为'
        answer['numeric_segments']['answer'][4]['text'] = '。二者比较不证明可实现套利或投资收益。'
        proposal['theses'] = [dict(question_ids=['q1'], support=answer['refs'], counter=[],
            calculation_refs=answer['calculation_refs'], judgment='证据不足',
            numeric_segments=dict(title=[dict(text='日终比较不等于可实现套利')],
                investment_meaning=copy.deepcopy(answer['numeric_segments']['answer']),
                limitation=[dict(text='未核验交易成本和盘中可成交条件，不能推导买卖建议。')]))]
        answer['numeric_segments']['answer'] = [dict(text='不应采纳的合成错误回答。')]
        self.control.reading_bundle()
        result = self.control.agent.start(task['id'], proposal, defer_delivery=True)
        self.assertTrue(result.get('host_checkpoint'), result['gaps'])
        self.task = result
        self.proposal = copy.deepcopy(result['host_result'])
        self.request = support.review_request(self.task, self.proposal)

    def wire(self, status=choices.STATUS, rejected=()):
        return dict(version=support.VERSION, review_hash=self.request['review_hash'],
            questions=[dict(question_id='q1', status=status, missing_evidence=[],
                reason='人为编写的完整性判断，仅验收离线协议和归档。')],
            reviews=[dict(target_id=t['id'], segments=[dict(text=t['text'],
                verdict='insufficient' if t['id'].startswith('answer:') or t['id'] in rejected else 'supported',
                basis_refs=['scope:task'], explanation='合成复核，不代表真实模型质量。')])
                for t in self.request['targets']])

    def record(self, wire=None):
        result = support.decode_review(wire or self.wire(), support.review_schema(self.request), self.request)
        return support.make_record(self.task, self.proposal, result, actor='host')

    def test_candidate_copies_all_fields_and_keeps_original_draft(self):
        before = copy.deepcopy(self.proposal)
        candidate = choices.answer(self.task, self.proposal, 'q1')
        self.assertEqual(candidate['donor_targets'], ['thesis:0.'+f for f in
            ('title', 'investment_meaning', 'limitation')])
        self.assertEqual(len(self.request['targets']), 4)
        self.assertIn('q1', self.request['scope']['answer_choices'])
        self.assertEqual(self.proposal, before)
        self.assertNotIn('不应采纳', str(candidate['answer']))
        self.assertIn('不能推导买卖建议', str(candidate['answer']))

    def test_explicit_choice_keeps_rejection_and_revalidates_after_save(self):
        record = self.record()
        self.assertFalse(record['result']['supported'])
        self.assertTrue(record['result']['answer_complete'])
        self.assertEqual(record['result']['question_assessments'][0]['status'], 'answered')
        support.validate_record(self.task, record)
        filtered = support.filter_proposal(self.task, self.proposal, record)
        self.assertEqual(filtered['answers']['q1'], choices.answer(self.task, self.proposal, 'q1')['answer'])
        self.assertEqual(len(choices.effective_assessments(self.request, record['result'])), 3)

    def test_supported_theses_without_explicit_choice_do_not_repair_draft(self):
        record = self.record(self.wire(status='answered'))
        self.assertNotIn('q1', support.filter_proposal(self.task, self.proposal, record)['answers'])
        self.assertEqual(len(choices.effective_assessments(self.request, record['result'])), 4)

    def test_any_rejected_donor_blocks_choice(self):
        for field in ('title', 'investment_meaning', 'limitation'):
            with self.subTest(field=field), self.assertRaises(ContractError):
                self.record(self.wire(rejected=['thesis:0.'+field]))

    def test_no_candidate_without_explicit_policy_or_question_relation(self):
        task = copy.deepcopy(self.task)
        task.pop(choices.POLICY)
        self.assertEqual(choices.alternatives(task, self.proposal), {})
        proposal = copy.deepcopy(self.proposal)
        proposal['theses'][0]['question_ids'] = ['another-question']
        self.assertIsNone(choices.answer(self.task, proposal, 'q1'))
        task = copy.deepcopy(self.task)
        task['questions'][0]['answer_scope'] = 'general_definition'
        self.assertIsNone(choices.answer(task, self.proposal, 'q1'))

    def test_unknown_policy_and_changed_content_cannot_reuse_review(self):
        task = copy.deepcopy(self.task)
        task[choices.POLICY] = True
        with self.assertRaises(ContractError): choices.alternatives(task, self.proposal)
        record = self.record()
        changed = copy.deepcopy(self.proposal)
        changed['theses'][0]['numeric_segments']['limitation'].append(dict(text='修改后的正文'))
        with self.assertRaises(ContractError): support.filter_proposal(self.task, changed, record)

    def test_forged_origin_or_hash_is_rejected(self):
        for field, value in [('candidate_hash', 'forged'), ('donor_targets', ['thesis:0.title'])]:
            record = self.record()
            record['result']['question_assessments'][0]['answer_origin'][field] = value
            with self.assertRaises(ContractError): support.validate_record(self.task, record)

    def test_foreign_question_evidence_is_not_offered_as_answer(self):
        task = copy.deepcopy(self.task)
        task['evidence'][0]['question_ids'] = ['another-question']
        self.assertIsNone(choices.answer(task, self.proposal, 'q1'))

    def test_all_related_theses_are_copied_without_sentence_selection(self):
        proposal = copy.deepcopy(self.proposal)
        proposal['theses'].append(copy.deepcopy(proposal['theses'][0]))
        proposal['theses'][1]['numeric_segments']['title'] = [dict(text='另一个完整论点')]
        candidate = choices.answer(self.task, proposal, 'q1')
        self.assertEqual(len(candidate['donor_targets']), 6)
        self.assertIn('另一个完整论点', str(candidate['answer']))

    def test_reference_unit_protocol_preserves_explicit_choice(self):
        units = support.reference_units(self.request)
        encoding = self.request['scope'].get('reference_encoding', 1)
        reverse = {value:key for key,value in support.reference_basis_choices(self.request).items()}
        basis = reverse['scope:task'] if encoding == 2 else 'scope:task'
        wire = dict(version=encoding, binding_ref=self.request['review_hash'][:16],
            questions=self.wire()['questions'], reviews=[dict(target_id=ident,
                segments=[dict(unit_ref=unit['unit_ref'],
                    verdict='insufficient' if ident.startswith('answer:') else 'supported',
                    basis_refs=[basis], explanation='离线协议检查，不代表金融质量。') for unit in values])
                for ident, values in units.items()])
        result = support.decode_reference_review(wire, support.reference_review_schema(self.request), self.request)
        self.assertTrue(choices.selected(result['question_assessments'][0]))
        support.validate_record(self.task, support.make_record(self.task, self.proposal, result, actor='host'))

    def test_incomplete_donor_or_unbound_number_is_not_offered(self):
        for parts in ([], [dict(text='没有登记依据的收益率99%。')]):
            proposal = copy.deepcopy(self.proposal)
            proposal['theses'][0]['numeric_segments']['title'] = parts
            self.assertIsNone(choices.answer(self.task, proposal, 'q1'))

    def test_request_without_candidate_cannot_submit_new_status(self):
        request = copy.deepcopy(self.request)
        request['scope'].pop('answer_choices')
        request.pop('review_hash')
        request['review_hash'] = fingerprint(request)
        wire = self.wire()
        wire['review_hash'] = request['review_hash']
        with self.assertRaises(ContractError):
            support.decode_review(wire, support.review_schema(request), request)

    def test_calculation_applicability_still_blocks_adoption(self):
        self.control.agent.submit_argument_review(self.task['id'], self.wire())
        with patch('researchhelper.contracts.calculation_applies_to_answer', return_value=False):
            done = self.control.agent.start(self.task['id'])
        self.assertNotEqual(done['questions'][0]['status'], '已回答')
        self.assertNotEqual(done['status'], '已完成')

    def test_engine_saves_and_reopens_report_without_a_model_call(self):
        self.control.agent.submit_argument_review(self.task['id'], self.wire())
        done = self.control.agent.start(self.task['id'])
        self.assertEqual(done['status'], '已完成', done['gaps'])
        self.assertEqual(done['questions'][0]['research_coverage']['answer_origin']['kind'], 'reviewed_thesis_fields')
        report = self.control.agent.export(done['id'])
        self.assertIn('日终比较不等于可实现套利', report['html'])
        self.assertEqual(done['ledger']['used']['model_calls'], 0)
        from researchhelper.engine import ResearchAgent
        reopened = ResearchAgent(self.control.archive, self.control.resources, offline=True, require_argument_support=True)
        try:
            self.assertEqual(reopened.export(done['id']), report)
        finally:
            reopened.close_runtime()

    def test_required_missing_data_still_prevents_completion(self):
        self.control.agent.submit_argument_review(self.task['id'], self.wire())
        # Keep the real completion gate, only inject its missing-need result.
        with patch('researchhelper.engine.missing_evidence_requirements', return_value=[dict(text='必要数据未覆盖')]):
            done = self.control.agent.start(self.task['id'])
        self.assertNotEqual(done['status'], '已完成')
        self.assertNotEqual(done['questions'][0]['status'], '已回答')
        self.assertTrue(any('必要数据未覆盖' in gap for gap in done['gaps']))

    def test_selected_candidate_avoids_two_writing_calls(self):
        task = copy.deepcopy(self.task)
        task.update(status='运行中', run_mode='model', reviewed_writing_revision_version=1)
        task['ledger']['limits']['model_calls'] = 8
        task['ledger']['used']['model_calls'] = 1
        task.setdefault('reasoning_state', {}).pop('reviewed_writing_revision', None)
        task['argument_support_reviews'] = [support.make_record(task, self.proposal,
            support.decode_review(self.wire(), support.review_schema(self.request), self.request), actor='host')]
        before = copy.deepcopy(task['ledger'])
        with patch.object(self.control.agent, '_reasoning_available', return_value=True), \
                patch.object(self.control.agent, '_reason_on_verified_material') as revise, \
                patch.object(self.control.agent, '_prepare_argument_review') as review:
            result = self.control.agent._revise_reviewed_writing(task, self.proposal, ['q1'])
        self.assertEqual(result, self.proposal)
        revise.assert_not_called()
        review.assert_not_called()
        self.assertEqual(task['ledger'], before)


if __name__ == '__main__':
    unittest.main()
