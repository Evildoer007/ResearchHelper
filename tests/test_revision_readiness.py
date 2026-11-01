"""Same-evidence revision is a pending state, never a new approval shortcut."""
import copy
import tempfile
import unittest
from unittest.mock import patch

from core.session import Session
from researchhelper import argument_support as review
from researchhelper.contracts import ContractError, charge
from researchhelper.output_schema import StructuredOutputError
from researchhelper.planning import input_fingerprint
from tests import ROOT
from tests import test_reviewed_writing_revision as routing_fixture

POLICY = 'review_revision_readiness_version'


class RevisionReadinessProtocolTests(unittest.TestCase):
    def setUp(self):
        # A host research project must be outside the read-only installation.
        temporary = tempfile.TemporaryDirectory(prefix='rh-revision-readiness-')
        self.addCleanup(temporary.cleanup)
        self.session = Session(temporary.name)
        self.addCleanup(self.session.agent.close_runtime)
        task = self.session.create('政策何时生效？', objects=['示例政策'],
            cutoff='2026-07-20T18:00:00+08:00', questions=[dict(id='q1', kind='fact',
                question='政策何时生效？', evidence_needs=['生效日期'])])
        source = self.session.material(task['id'], 'policy.txt',
            '示例政策\n\n发布日期：2026年7月19日。\n\n政策自2026年7月21日起生效。'.encode())
        evidence = dict(id='effective_date', artifact_id=source['id'], object='示例政策',
            record_locator=dict(part='段落3'), text='政策自2026年7月21日起生效。', period='政策条款',
            nature='合同政策条款', unit='不适用', question_ids=['q1'], published_at='2026-07-19',
            publication=dict(at='2026-07-19', precision='日', timezone='Asia/Shanghai', certainty='已确认',
                basis='原件公布日', basis_ref=dict(kind='material', artifact_id=source['id'],
                    snapshot_hash=source['snapshot_hash'], part='段落2', quote='发布日期：2026年7月19日。')),
            review=dict(question_ids=['q1'], allowed_natures=['合同政策条款'], conditions=['材料条款核对'],
                reason='本段明确生效日', evidence_need_texts=dict(q1=['生效日期'])))
        self.task = self.session.run(task['id'], evidence_specs=[evidence],
            answers=dict(q1=dict(answer='政策自2026年7月21日起生效。', refs=['effective_date'])),
            defer_delivery=True)
        self.proposal = self.task['host_result']
        self.request = review.review_request(self.task, self.proposal)
        self.wire = dict(version=review.VERSION, review_hash=self.request['review_hash'],
            questions=[dict(question_id='q1', status='requires_revision', missing_evidence=[],
                reason='机械夹具：已有条款足够，仍要求重新作答，不批准当前稿。')],
            reviews=[dict(target_id=t['id'], segments=[dict(text=t['text'], verdict='supported',
                basis_refs=['evidence:effective_date'], explanation='协议夹具，不是语义质量批准。')])
                for t in self.request['targets']])

    def decode(self, wire=None, request=None):
        request = request or self.request
        return review.decode_review(wire or self.wire, review.review_schema(request), request)

    def test_new_state_is_not_completed_even_if_every_sentence_is_supported(self):
        result = self.decode()
        self.assertTrue(result['supported'])
        self.assertFalse(result['answer_complete'])
        self.assertFalse(result['source_approval'])
        self.assertFalse(self.request['scope']['revision_readiness']['automatic_approval'])

    def test_pending_revision_cannot_be_delivered_as_completed(self):
        self.session.review_arguments(self.task['id'], self.wire)
        result = self.session.deliver(self.task['id'])
        self.assertEqual(result['status'], '部分完成')
        self.assertEqual(result['questions'][0]['status'], '未回答')
        self.assertTrue(any('重新分析' in gap for gap in result['gaps']))
        self.assertEqual(result['ledger']['used']['model_calls'], 0)
        frozen = self.session.archive.report(result['id'], result['version'])['content']
        self.assertEqual(frozen[POLICY], 1)
        self.assertEqual(frozen['registered_source_reading_version'], 1)

    def test_revision_must_not_hide_genuinely_missing_materials(self):
        wire = copy.deepcopy(self.wire)
        wire['questions'][0]['missing_evidence'] = ['实际必要新资料']
        with self.assertRaises(StructuredOutputError):
            self.decode(wire)
        wire['questions'][0]['status'] = 'requires_evidence'
        self.assertFalse(self.decode(wire)['answer_complete'])

    def test_missing_evidence_still_requires_a_specific_nonempty_gap(self):
        wire = copy.deepcopy(self.wire)
        wire['questions'][0]['status'] = 'requires_evidence'
        with self.assertRaises(StructuredOutputError):
            self.decode(wire)

    def test_legacy_schema_does_not_accept_new_review_labels(self):
        old = dict(self.task)
        old.pop(POLICY)
        request = review.review_request(old, self.proposal)
        wire = copy.deepcopy(self.wire)
        wire['review_hash'] = request['review_hash']
        with self.assertRaises(StructuredOutputError):
            self.decode(wire, request)
        self.assertNotIn('revision_readiness', request['scope'])
        self.assertNotEqual(self.request['review_hash'], request['review_hash'])

    def test_short_protocol_receives_the_same_pending_state(self):
        schema = review.reference_review_schema(self.request)
        statuses = schema['properties']['questions']['items']['anyOf'][0]['properties']['status']['enum']
        self.assertIn('requires_revision', statuses)
        self.assertIn('requires_revision', review.review_messages(self.request, use_reference_units=True)[0]['content'])

    def test_policy_is_formal_input_bound_but_never_added_to_old_cases(self):
        self.assertEqual(self.task[POLICY], 1)
        old = copy.deepcopy(self.task)
        old.pop(POLICY)
        self.assertNotEqual(input_fingerprint(old), input_fingerprint(self.task))
        self.assertNotIn(POLICY, self.session.agent.create_case('mechanism'))


class RevisionReadinessRoutingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = routing_fixture.ReviewedWritingRevisionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.task = self.fixture.task
        self.task.update(**{POLICY: 1}, review_delivery_call_version=1)
        self.task['ledger']['used']['model_calls'] = 6
        self.fixture.agent._prepare_argument_review.side_effect = lambda task, proposal, selected: charge(
            task['ledger'], 'model_calls', delivery=True)
        self.record = copy.deepcopy(review.find_record(self.task, self.fixture.proposal))
        # This is solely a mocked routing verdict. Protocol/adoption behavior
        # uses the small real Session fixture above, not this old transcript.
        if self.record is None:
            old = dict(self.task)
            old.pop(POLICY)
            old.pop('review_delivery_call_version', None)
            self.record = copy.deepcopy(review.find_record(old, self.fixture.proposal))
        self.assertIsNotNone(self.record)
        for q in self.record['result']['question_assessments']:
            q.update(status='requires_revision', missing_evidence=[])

    def revise(self):
        with patch.object(review, 'find_record', return_value=self.record):
            return self.fixture.run_revision()

    def test_one_new_analysis_and_independent_review_fit_original_eight_calls(self):
        before = copy.deepcopy(self.task['ledger']['reserve'])
        self.revise()
        self.assertEqual(self.task['ledger']['used']['model_calls'], 8)
        self.assertEqual(self.task['ledger']['reserve'], before)
        self.fixture.agent._reason_on_verified_material.assert_called_once()
        self.fixture.agent._prepare_argument_review.assert_called_once()

    def test_all_supported_sentences_do_not_hide_omitted_analysis(self):
        for item in self.record['result']['assessments']:
            item['supported'] = True
        self.revise()
        self.fixture.agent._reason_on_verified_material.assert_called_once()

    def test_real_missing_materials_still_block_same_evidence_revision(self):
        self.record['result']['question_assessments'][0].update(
            status='requires_evidence', missing_evidence=['原问题必要资料未取得'])
        self.revise()
        self.fixture.agent._reason_on_verified_material.assert_not_called()

    def test_no_revision_when_only_one_model_slot_remains(self):
        self.task['ledger']['used']['model_calls'] = 7
        self.revise()
        self.fixture.agent._reason_on_verified_material.assert_not_called()

    def test_identical_resubmission_cannot_seek_a_luckier_review(self):
        self.fixture.candidate = copy.deepcopy(self.fixture.proposal)
        result = self.revise()
        self.assertEqual(result, self.fixture.proposal)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 7)
        self.assertEqual(self.task['reasoning_state']['reviewed_writing_revision']['status'], '未改稿')
        self.fixture.agent._prepare_argument_review.assert_not_called()

    def test_revision_state_survives_resume_without_an_extra_loop(self):
        self.revise()
        self.task['batch'] += 1
        self.revise()
        self.fixture.agent._reason_on_verified_material.assert_called_once()

    def test_full_analysis_can_be_repaired_at_its_first_consumer_node(self):
        question = copy.deepcopy(self.task['questions'][0])
        question.update(id='q2', question='另一个原有研究问题')
        self.task['questions'].append(question)
        self.record['result']['question_assessments'].append(dict(question_id='q2',
            status='requires_revision', missing_evidence=[], reason='机械路由夹具'))
        nodes = copy.deepcopy(self.task['nodes'])
        with patch.object(review, 'review_request', return_value={'targets':[
                dict(question_ids=['q1']), dict(question_ids=['q2'])]}):
            self.revise()
        self.assertEqual(self.fixture.agent._reason_on_verified_material.call_args.args[1], ['q1', 'q2'])
        self.assertEqual(self.fixture.agent._prepare_argument_review.call_args.args[2], ['q1', 'q2'])
        self.assertEqual(self.task['nodes'], nodes)

    def test_partial_proposal_cannot_expand_to_other_original_questions(self):
        for ident in ('q2', 'q3'):
            question = copy.deepcopy(self.task['questions'][0])
            question.update(id=ident, question='原有研究问题' + ident)
            self.task['questions'].append(question)
        self.record['result']['question_assessments'].append(dict(question_id='q2',
            status='requires_revision', missing_evidence=[], reason='机械路由夹具'))
        with patch.object(review, 'review_request', return_value={'targets':[
                dict(question_ids=['q1']), dict(question_ids=['q2'])]}):
            self.revise()
        self.fixture.agent._reason_on_verified_material.assert_not_called()
