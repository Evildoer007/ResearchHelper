"""Offline routing controls, not new model output or semantic approval."""
import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from researchhelper import argument_support as review
from researchhelper.contracts import ContractError, canonical, charge, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.model import ModelCompletionError
from researchhelper.planning import input_fingerprint

ROOT = Path(__file__).resolve().parents[1]
POLICY = review.MIXED_REVISION_POLICY


def prepare_fixture():
    folder = ROOT/'result/acceptance/one-sentence-research-m4v02ejg'
    names = ('receipt.json', 'task.json', 'report.json', 'report.html')
    protected = {name: hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in names}
    task = json.loads((folder/'task.json').read_text())
    proposal = copy.deepcopy(task['host_result'])
    request = review.review_request(task, proposal)
    record = review.find_record(task, proposal)
    assert record is not None
    assert protected == {name: hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in names}
    return dict(task=task, proposal=proposal, request=request, record=record,
                protected=protected, synthetic_routing_control=True, original_study_reopened=False)


class MixedQuestionRevisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        supplied = os.environ.get('RESEARCHHELPER_MIXED_REVISION_FIXTURE')
        cls.fixture = json.loads(gzip.decompress(Path(supplied).read_bytes())) if supplied else prepare_fixture()

    def setUp(self):
        network = patch('socket.socket.connect', side_effect=AssertionError('Offline revision control only'))
        network.start(); self.addCleanup(network.stop)
        if os.environ.get('RESEARCHHELPER_FROZEN_TEST_EXECUTABLE'):
            from frozen_archive_test_loader import frozen_pdf_worker
            from researchhelper.material_parser import MaterialParser
            worker = patch.object(MaterialParser, 'worker_target', staticmethod(frozen_pdf_worker))
            worker.start(); self.addCleanup(worker.stop)
        self.fixture_before = canonical(self.fixture)
        self.task = copy.deepcopy(self.fixture['task'])
        self.task.update(status='运行中', **{POLICY: 1})
        self.task['input_hash'] = input_fingerprint(self.task)
        self.proposal = copy.deepcopy(self.fixture['proposal'])
        self.record, self.request = copy.deepcopy(self.fixture['record']), copy.deepcopy(self.fixture['request'])
        self.candidate = copy.deepcopy(self.proposal)
        self.candidate['answers']['q1']['numeric_segments']['answer'][0]['text'] += '离线路由夹具，不作为研究结论。'
        self.agent = object.__new__(ResearchAgent)
        self.agent._reasoning_available = Mock(return_value=True)
        self.agent._save_model_checkpoint = Mock()
        self.agent._check_run_control = Mock()
        def rewrite(task, selected):
            charge(task['ledger'], 'model_calls')
            return copy.deepcopy(self.candidate)
        self.agent._reason_on_verified_material = Mock(side_effect=rewrite)
        self.agent._prepare_argument_review = Mock(side_effect=lambda task, result, selected:
                                                  charge(task['ledger'], 'model_calls', delivery=True))

    def tearDown(self):
        self.assertEqual(canonical(self.fixture), self.fixture_before)

    def invoke(self, selected=None):
        def retained_only(task, proposal):
            return self.record if fingerprint(proposal) == fingerprint(self.proposal) else None
        with patch.object(review, 'find_record', side_effect=retained_only), \
                patch.object(review, 'review_request', return_value=self.request):
            return self.agent._revise_reviewed_writing(self.task, self.proposal, ['q1'] if selected is None else selected)

    def test_mixed_verdicts_use_two_existing_slots_and_still_require_a_new_review(self):
        result = self.invoke()
        self.assertEqual(result, self.candidate)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 8)
        self.assertEqual(self.agent._reason_on_verified_material.call_args.args[1], ['q1', 'q2'])
        self.assertEqual(self.agent._prepare_argument_review.call_args.args[2], ['q1', 'q2'])
        journal = self.task['reasoning_state']['reviewed_writing_revision']
        self.assertEqual(journal['status'], '复核未完成')
        scope = journal['readiness_scope']
        self.assertEqual(scope['ready_question_ids'], ['q1'])
        self.assertEqual(scope['evidence_limited_question_ids'], ['q2'])
        self.assertEqual(scope['trigger_question_ids'], ['q1'])
        self.assertFalse(scope['automatic_approval'] or scope['further_acquisition_allowed'])
        self.assertTrue(all(question['status'] == '未回答' for question in self.task['questions']))

    def test_sources_values_questions_limits_reserves_and_original_proposal_are_not_repaired(self):
        keys = ('evidence', 'source_records', 'calculations', 'questions', 'nodes')
        before = copy.deepcopy({key: self.task[key] for key in keys})
        limits, reserve = copy.deepcopy(self.task['ledger']['limits']), copy.deepcopy(self.task['ledger']['reserve'])
        supplements = self.task['ledger']['supplements']
        original = copy.deepcopy(self.proposal)
        self.invoke()
        self.assertEqual({key: self.task[key] for key in keys}, before)
        self.assertEqual(self.task['ledger']['limits'], limits)
        self.assertEqual(self.task['ledger']['reserve'], reserve)
        self.assertEqual(self.task['ledger']['supplements'], supplements)
        self.assertEqual(self.task['ledger']['used']['data_requests'], self.fixture['task']['ledger']['used']['data_requests'])
        self.assertEqual(self.proposal, original)

    def test_only_a_missing_evidence_problem_does_not_spend_two_writing_calls(self):
        self.record['result']['question_assessments'][0]['status'] = 'answered'
        for item in self.record['result']['assessments']: item['supported'] = True
        target = next(target for target in self.request['targets'] if target['question_ids'] == ['q2'])
        next(item for item in self.record['result']['assessments'] if item['target_id'] == target['id'])['supported'] = False
        self.assertIs(self.invoke(), self.proposal)
        self.agent._reason_on_verified_material.assert_not_called()

    def test_all_questions_missing_evidence_are_not_reclassified_as_writing(self):
        self.record['result']['question_assessments'][0].update(status='requires_evidence', missing_evidence=['确缺必要资料'])
        self.assertIs(self.invoke(), self.proposal)
        self.agent._reason_on_verified_material.assert_not_called()

    def test_required_sibling_stays_required_and_missing_without_blocking_independent_writing(self):
        self.task['questions'][1]['required'] = True
        self.invoke()
        self.assertTrue(self.task['questions'][1]['required'])
        self.assertEqual(self.task['questions'][1]['status'], '未回答')
        self.assertEqual(self.record['result']['question_assessments'][1]['status'], 'requires_evidence')
        self.assertTrue(self.record['result']['question_assessments'][1]['missing_evidence'])
        self.agent._prepare_argument_review.assert_called_once()

    def test_old_tasks_and_unregistered_policies_do_not_gain_the_new_route(self):
        for value in (None, True, 0, 2, '1'):
            task = copy.deepcopy(self.task)
            if value is None: task.pop(POLICY)
            else: task[POLICY] = value
            with self.subTest(value=value):
                before = canonical(task)
                with patch.object(review, 'find_record', return_value=self.record), \
                        patch.object(review, 'review_request', return_value=self.request):
                    self.assertIs(self.agent._revise_reviewed_writing(task, self.proposal, ['q1']), self.proposal)
                self.assertEqual(canonical(task), before)
        self.agent._reason_on_verified_material.assert_not_called()

    def test_one_remaining_call_or_unavailable_time_does_not_start_unreviewable_prose(self):
        self.task['ledger']['used']['model_calls'] = 7
        self.assertIs(self.invoke(), self.proposal)
        self.task['ledger']['used']['model_calls'] = 6
        self.agent._reasoning_available.return_value = False
        self.assertIs(self.invoke(), self.proposal)
        self.agent._reason_on_verified_material.assert_not_called()

    def test_host_closed_or_unreviewed_tasks_never_dispatch(self):
        for changes in ({'run_mode': 'host'}, {'status': '部分完成'}):
            task = dict(copy.deepcopy(self.task), **changes)
            with patch.object(review, 'find_record', return_value=self.record):
                self.assertIs(self.agent._revise_reviewed_writing(task, self.proposal, ['q1']), self.proposal)
        with patch.object(review, 'find_record', return_value=None):
            self.assertIs(self.agent._revise_reviewed_writing(self.task, self.proposal, ['q1']), self.proposal)
        self.agent._reason_on_verified_material.assert_not_called()

    def test_resume_does_not_reset_ledger_or_create_another_revision(self):
        self.invoke(); self.task['batch'] += 1
        self.invoke()
        self.agent._reason_on_verified_material.assert_called_once()
        self.agent._prepare_argument_review.assert_called_once()
        self.assertEqual(self.task['ledger']['used']['model_calls'], 8)

    def test_unknown_inflight_revision_never_redispatches(self):
        self.task['reasoning_state']['reviewed_writing_revision'] = {'status': '已派发'}
        self.assertIs(self.invoke(), self.proposal)
        self.agent._reason_on_verified_material.assert_not_called()

    def test_unchanged_candidate_does_not_seek_a_more_favorable_verdict(self):
        self.candidate = copy.deepcopy(self.proposal)
        self.assertEqual(self.invoke(), self.proposal)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 7)
        self.assertEqual(self.task['reasoning_state']['reviewed_writing_revision']['status'], '未改稿')
        self.agent._prepare_argument_review.assert_not_called()

    def test_changed_inputs_reject_a_late_result(self):
        def changed(task, selected):
            charge(task['ledger'], 'model_calls'); task['source_records'].pop()
            return self.candidate
        self.agent._reason_on_verified_material.side_effect = changed
        self.assertEqual(self.invoke(), self.proposal)
        self.agent._prepare_argument_review.assert_not_called()

    def test_failed_writer_is_not_retried_or_approved(self):
        def failed(task, selected):
            charge(task['ledger'], 'model_calls')
            raise ModelCompletionError('Offline synthetic failure')
        self.agent._reason_on_verified_material.side_effect = failed
        self.assertEqual(self.invoke(), self.proposal)
        self.invoke()
        self.agent._reason_on_verified_material.assert_called_once()
        self.agent._prepare_argument_review.assert_not_called()

    def test_partial_scope_cannot_expand_into_unselected_questions(self):
        question = copy.deepcopy(self.task['questions'][1]); question['id'] = 'q3'
        self.task['questions'].append(question)
        self.assertIs(self.invoke(), self.proposal)
        self.agent._reason_on_verified_material.assert_not_called()

    def test_active_supplement_keeps_its_exact_affected_scope(self):
        self.agent._active_supplement = Mock(return_value={'affected_questions': ['q1']})
        self.assertIs(self.invoke(), self.proposal)
        self.agent._reason_on_verified_material.assert_not_called()

    def test_supported_sentences_do_not_override_a_requires_revision_verdict(self):
        for item in self.record['result']['assessments']: item['supported'] = True
        self.invoke()
        self.agent._reason_on_verified_material.assert_called_once()

    def test_malformed_readiness_or_foreign_targets_do_not_authorize_writing(self):
        original = copy.deepcopy(self.record)
        variants = []
        item = copy.deepcopy(original); item['result']['question_assessments'][1]['missing_evidence'] = []; variants.append(item)
        item = copy.deepcopy(original); item['result']['question_assessments'][0]['missing_evidence'] = ['不应隐藏的缺口']; variants.append(item)
        item = copy.deepcopy(original); item['result']['question_assessments'].append(copy.deepcopy(item['result']['question_assessments'][0])); variants.append(item)
        item = copy.deepcopy(original); item['result']['assessments'][0]['target_id'] = 'foreign'; variants.append(item)
        for record in variants:
            self.record = record
            self.assertIs(self.invoke(), self.proposal)
        self.agent._reason_on_verified_material.assert_not_called()

    def test_feedback_exposes_mixed_readiness_without_claiming_all_material_is_sufficient(self):
        payloads = []
        def capture(model, task, phase, payload, fields, **kwargs):
            payloads.append(payload)
            raise ModelCompletionError('Offline payload capture')
        self.agent.model = object()
        self.agent._reason_on_verified_material = ResearchAgent._reason_on_verified_material.__get__(self.agent)
        # Isolate feedback projection from the old transcript's model-input
        # checkpoint. Full admission and host adoption are tested separately.
        self.agent._reasoning_evidence_payload = Mock(return_value={'evidence': []})
        with patch('researchhelper.engine.analysis_contract', return_value={}), \
                patch('researchhelper.engine.model_object', side_effect=capture): self.invoke()
        feedback = payloads[0]['completed_review_feedback']
        self.assertEqual(feedback['readiness_scope']['ready_question_ids'], ['q1'])
        self.assertEqual(feedback['readiness_scope']['evidence_limited_question_ids'], ['q2'])
        self.assertEqual(feedback['readiness_scope']['full_proposal_question_ids'], ['q1', 'q2'])
        self.assertEqual(feedback['original_proposal'], {key: self.proposal[key] for key in ('answers', 'theses')})
        self.assertFalse(feedback['automatic_repair'])

    def test_new_policy_is_input_bound_and_frozen_not_retroactively_added(self):
        from researchhelper.archive import ResearchArchive
        from researchhelper.reports import compose
        folder = tempfile.TemporaryDirectory(prefix='mixed-revision-policy-', dir=ROOT/'result/acceptance')
        self.addCleanup(folder.cleanup)
        resources = Path(os.environ.get('RESEARCHHELPER_MIXED_REVISION_RESOURCES', ROOT))
        agent = ResearchAgent(ResearchArchive(folder.name), resources, offline=True, require_argument_support=True)
        self.addCleanup(agent.close_runtime)
        task = agent.create('解释研究缺资料与改稿的区别', research_mode='current')
        self.assertEqual(task[POLICY], 1)
        agent._adopt_method_knowledge(task); task['status'] = '部分完成'
        agent._gap(task, '离线修稿契约检查，不是一次研究', category='question_coverage')
        self.assertEqual(compose(task)[POLICY], 1)
        old = dict(task); old.pop(POLICY)
        self.assertNotEqual(input_fingerprint(old), input_fingerprint(task))
        self.assertNotIn(POLICY, self.fixture['task'])

    def test_legacy_all_ready_proposal_still_gets_its_original_bounded_route(self):
        self.task.pop(POLICY)
        self.record['result']['question_assessments'][1].update(status='answered', missing_evidence=[])
        self.task['ledger']['used']['model_calls'] = 5
        self.task.pop('review_delivery_call_version')
        self.invoke()
        self.assertEqual(self.task['ledger']['used']['model_calls'], 7)
        journal = self.task['reasoning_state']['reviewed_writing_revision']
        self.assertNotIn('readiness_scope', journal)
        self.assertEqual(journal['status'], '复核未完成')
        self.assertEqual(self.task['ledger']['reserve']['model_calls'], 1)

    def test_legacy_reserved_review_slot_cannot_be_spent_on_a_new_draft(self):
        self.task.pop(POLICY)
        self.task.pop('review_delivery_call_version')
        self.record['result']['question_assessments'][1].update(status='answered', missing_evidence=[])
        self.assertIs(self.invoke(), self.proposal)
        self.agent._reason_on_verified_material.assert_not_called()


class MixedRevisionAdoptionTests(unittest.TestCase):
    """Fresh private host checkpoints and real review codecs; no language model."""
    def setUp(self):
        from researchhelper.archive import ResearchArchive
        network = patch('socket.socket.connect', side_effect=AssertionError('Offline host protocol only'))
        network.start(); self.addCleanup(network.stop)
        if os.environ.get('RESEARCHHELPER_FROZEN_TEST_EXECUTABLE'):
            from frozen_archive_test_loader import frozen_pdf_worker
            from researchhelper.material_parser import MaterialParser
            worker = patch.object(MaterialParser, 'worker_target', staticmethod(frozen_pdf_worker))
            worker.start(); self.addCleanup(worker.stop)
        folder = tempfile.TemporaryDirectory(prefix='mixed-revision-host-', dir=ROOT/'result/acceptance')
        self.addCleanup(folder.cleanup)
        resources = Path(os.environ.get('RESEARCHHELPER_MIXED_REVISION_RESOURCES', ROOT))
        self.agent = ResearchAgent(ResearchArchive(folder.name), resources, offline=True, require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        task = self.agent.create('核对政策生效日期，并在资料允许时补充适用范围', objects=['示例政策'],
            cutoff='2026-07-20T18:00:00+08:00', questions=[
                dict(id='q1', kind='fact', question='政策何时生效？', evidence_needs=['生效日期']),
                dict(id='q2', kind='fact', required=False, question='政策的适用范围是什么？', evidence_needs=['适用范围'])])
        task = self.agent.add_material(task['id'], 'policy.txt',
            '示例政策\n\n发布日期：2026年7月19日。\n\n政策自2026年7月21日起生效。'.encode())
        source = task['materials'][0]
        evidence = dict(id='effective_date', artifact_id=source['id'], object='示例政策',
            record_locator=dict(part='段落3'), text='政策自2026年7月21日起生效。', period='政策条款',
            nature='合同政策条款', unit='不适用', question_ids=['q1'], published_at='2026-07-19',
            publication=dict(at='2026-07-19', precision='日', timezone='Asia/Shanghai', certainty='已确认',
                basis='测试原件公布日', basis_ref=dict(kind='material', artifact_id=source['id'],
                    snapshot_hash=source['snapshot_hash'], part='段落2', quote='发布日期：2026年7月19日。')),
            review=dict(question_ids=['q1'], allowed_natures=['合同政策条款'], conditions=['仅核对文件条款'],
                reason='机械协议夹具，不是投资语义批准', evidence_need_texts=dict(q1=['生效日期'])))
        self.payload = dict(evidence=[], evidence_specs=[evidence], calculations=[], data_requests=[], theses=[],
            answers=dict(q1=dict(answer='政策自2026年7月21日起生效。', refs=['effective_date']),
                         q2=dict(answer='适用范围仍待独立材料核对，本次不作判断。', refs=[])))
        self.task = self.agent.start(task['id'], self.payload, defer_delivery=True)

    def wire(self, task=None, *, ready='requires_revision'):
        task = task or self.task
        request = review.review_request(task, task['host_result'])
        return dict(version=review.VERSION, review_hash=request['review_hash'],
            questions=[dict(question_id='q1', status=ready, missing_evidence=[],
                reason='机械夹具：已有日期条款足够，当前稿是否可采用由本条复核状态表示'),
                dict(question_id='q2', status='requires_evidence', missing_evidence=['适用范围'],
                     reason='缺少范围材料，不把它改成文字问题')],
            reviews=[dict(target_id=target['id'], segments=[dict(text=target['text'],
                verdict='supported' if target['question_ids'] == ['q1'] else 'insufficient',
                basis_refs=['evidence:effective_date'] if target['question_ids'] == ['q1'] else [],
                explanation='机械协议夹具，不代表真实模型质量')]) for target in request['targets']])

    def rewritten(self):
        payload = copy.deepcopy(self.payload)
        payload['answers']['q1']['answer'] = '按原件条款，政策自2026年7月21日起生效。'
        return self.agent.start(self.task['id'], payload, defer_delivery=True)

    def test_fresh_bound_review_has_two_distinct_readiness_states(self):
        wire = self.wire()
        self.agent.submit_argument_review(self.task['id'], wire)
        task = self.agent.archive.get(self.task['id'])
        record = review.find_record(task, task['host_result'])
        request = review.review_request(task, task['host_result'])
        scope = review.mixed_revision_scope(task, record, request)
        self.assertTrue(scope['revision_eligible'])
        self.assertEqual(scope['ready_question_ids'], ['q1'])
        self.assertEqual(scope['evidence_limited_question_ids'], ['q2'])
        self.assertFalse(record['result']['answer_complete'])

    def test_requires_revision_and_missing_data_cannot_be_delivered_as_completed(self):
        self.agent.submit_argument_review(self.task['id'], self.wire())
        task = self.agent.start(self.task['id'])
        self.assertEqual(task['status'], '部分完成')
        self.assertTrue(all(question['status'] == '未回答' for question in task['questions']))
        self.assertEqual(task['ledger']['used']['model_calls'], 0)

    def test_changed_text_cannot_borrow_the_old_completed_review(self):
        self.agent.submit_argument_review(self.task['id'], self.wire())
        task = self.rewritten()
        self.assertIsNone(review.find_record(task, task['host_result']))
        task = self.agent.start(task['id'])
        self.assertEqual(task['status'], '部分完成')
        self.assertEqual(task['questions'][0]['status'], '未回答')

    def test_new_independent_review_can_accept_core_without_inventing_optional_material(self):
        self.agent.submit_argument_review(self.task['id'], self.wire())
        task = self.rewritten()
        self.agent.submit_argument_review(task['id'], self.wire(task, ready='answered'))
        task = self.agent.start(task['id'])
        self.assertEqual(task['questions'][0]['status'], '已回答')
        self.assertEqual(task['questions'][1]['status'], '未回答')
        self.assertFalse(task['questions'][1]['required'])
        record = review.find_record(task, task['host_result'])
        limited = next(item for item in record['result']['question_assessments'] if item['question_id'] == 'q2')
        self.assertEqual(limited['status'], 'requires_evidence')
        self.assertEqual(limited['missing_evidence'], ['适用范围'])
        self.assertEqual(task['questions'][1]['evidence_needs'], ['适用范围'])
        self.assertEqual(task['ledger']['used']['model_calls'], task['ledger']['used']['data_requests'])
        self.assertEqual(task['ledger']['used']['model_calls'], 0)
        self.assertEqual(len(task['evidence']), 1)
        report = self.agent.archive.report(task['id'], task['version'])
        self.assertIn('政策自2026年7月21日起生效', report['html'])

    def test_old_review_hash_cannot_be_submitted_for_new_text(self):
        wire = self.wire()
        self.agent.submit_argument_review(self.task['id'], wire)
        task = self.rewritten()
        with self.assertRaises(ContractError): self.agent.submit_argument_review(task['id'], wire)

    def test_missing_data_cannot_be_erased_by_changing_only_the_readiness_label(self):
        wire = self.wire(); wire['questions'][1]['missing_evidence'] = []
        with self.assertRaises(ContractError): self.agent.submit_argument_review(self.task['id'], wire)
        wire = self.wire(); wire['questions'][0]['missing_evidence'] = ['未取得的新资料']
        with self.assertRaises(ContractError): self.agent.submit_argument_review(self.task['id'], wire)


if __name__ == '__main__': unittest.main()
