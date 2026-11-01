"""Retained workflow behavior with current inputs and the one-page reader.

Older source tests are not overwritten. Real local archives and service calls
exercise the current engine; no model, provider or production store is used.
"""
import base64
import copy
import html
import unittest

from researchhelper.contracts import ContractError, DOCUMENT_CONTENT_LIMITATION, budget, fingerprint, validate_evidence
from researchhelper.reasoning import evidence_contract, validate_supplement
from tests import test_autonomous_supplement_contracts_offline as supplement
from tests import test_document_content_offline as documents
from tests import test_question_plan_interfaces_offline as interfaces
from tests import test_publication_guard_workflow_compat_offline as workflow
from tests import test_version_review_offline as versions


class RetainedHarnessWorkflowTests(unittest.TestCase):
    def fixture(self, case):
        fixture = case()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        return fixture

    def test_independent_supplement_preserves_permission_boundary_and_full_budget(self):
        fixture = self.fixture(supplement.SupplementContractsTests)
        task = copy.deepcopy(fixture.task)
        task['ledger'] = budget()
        task['questions'] += [dict(task['questions'][0], id=ident, question=ident)
                              for ident in ('q2', 'q3')]
        failed = copy.deepcopy(fixture.need)
        failed['question_ids'] = ['q2']
        task['data_need_specs'] = [failed]
        digest = fingerprint({key: failed[key] for key in ('source', 'operation', 'parameters')})
        task['ledger']['dispatches'] = [{'kind': 'data_requests', 'fingerprint': digest,
            'failure_code': 'permission_denied', 'status': '权限未通过'}]
        proposal = copy.deepcopy(fixture.proposal)
        proposal.update(question_ids=['q3'], reason='独立q3待核验', expected_change='核实q3独立字段')
        proposal['data_requests'][0].update(id='q3_independent', question_ids=['q3'],
            source='tinyshare', operation='daily', selection_reason='原问题独立q3使用已授权Tinyshare',
            parameters={'ts_code': '600000.SH', 'fields': 'ts_code,trade_date,open',
                        'start_date': '20260929', 'end_date': '20260930'})
        before = fingerprint(task)
        self.assertEqual(validate_supplement(task, proposal), proposal)
        same_question = copy.deepcopy(proposal)
        same_question['question_ids'] = ['q2']
        same_question['data_requests'][0]['question_ids'] = ['q2']
        with self.assertRaises(ContractError):
            validate_supplement(task, same_question)
        same_source = copy.deepcopy(fixture.proposal)
        same_source['question_ids'] = ['q3']
        same_source['data_requests'][0].update(id='new_q3', question_ids=['q3'])
        same_source['data_requests'][0]['parameters']['indicators'] = 'open'
        with self.assertRaises(ContractError):
            validate_supplement(task, same_source)
        self.assertEqual(fingerprint(task), before)

    def test_unknown_publication_document_keeps_exact_quote_and_scoped_delivery(self):
        fixture = self.fixture(documents.DocumentContentTests)
        task = fixture.material_task()
        done = fixture.run_spec(task, fixture.spec(task))
        self.assertEqual(done['status'], '已完成', done.get('error'))
        self.assertEqual(done['questions'][0]['kind'], 'fact')
        self.assertEqual((done['theses'], done['calculations'], done['tracking']), ([], [], []))
        self.assertEqual(done['ledger']['used']['model_calls'], 0)
        self.assertEqual(done['ledger']['used']['data_requests'], 0)
        self.assertIsNone(done['evidence'][0]['published_at'])
        validate_evidence(done['evidence'][0], done)
        report = fixture.agent.export(done['id'])
        for text in (DOCUMENT_CONTENT_LIMITATION, '原件摘录', '未核验', done['evidence'][0]['text']):
            self.assertIn(html.escape(text, quote=True), report['html'])
        self.assertNotIn('<nav', report['html'])
        self.assertEqual(fixture.agent.export(done['id']), report)

    def test_version_review_keeps_both_conditions_without_a_directory(self):
        fixture = self.fixture(versions.VersionReviewTests)
        before = fixture.complete(dict(versions.RULE, benchmark='<原条件>'))
        frozen = copy.deepcopy(fixture.agent.archive.report(before['id'], before['version']))
        after = fixture.update(before)
        report = fixture.agent.export(after['id'])
        for text in ('版本1判断', '版本2判断', '原版适用资格解释', '本版适用资格解释',
                     '下一次主动更新', '&lt;原条件&gt;'):
            self.assertIn(text, report['html'])
        self.assertNotIn('<nav', report['html'])
        self.assertEqual(fingerprint(report['content']), after['delivery']['digest'])
        self.assertEqual(fixture.agent.archive.report(before['id'], before['version']), frozen)

    def test_host_calculation_uses_retained_originals_not_seeded_case_evidence(self):
        fixture = self.fixture(workflow.PublicationGuardWorkflowCompatibilityTests)
        done = fixture.nonfixture_host(True)
        self.assertEqual(done['status'], '已完成', done.get('error'))
        self.assertFalse(done['synthetic'])
        self.assertIsNone(done['fixture'])
        self.assertEqual(len(done['materials']), 2)
        self.assertAlmostEqual(done['calculations'][0]['output']['value'], -.4)
        self.assertIn('费用差为-0.4', fixture.agent.export(done['id'])['html'])

    def test_app_plan_revision_then_retained_original_reaches_same_engine(self):
        fixture = self.fixture(interfaces.QuestionPlanInterfaces)
        task = fixture.task()
        route = '/api/tasks/'+task['id']
        revised = fixture.post(route+'/plan', {
            'questions': [dict(fixture.questions[0], evidence_needs=['原文条款'])],
            'reason': '补充定位需求', 'expected_input_hash': task['input_hash']})
        self.assertEqual(revised['plan_version'], 2)
        quote = '原文于2026年7月19日生效。'
        publication = '发布日期：2026年7月19日。'
        retained = fixture.post(route+'/material', {'name': 'policy.txt',
            'base64': base64.b64encode((quote+'\n\n'+publication).encode()).decode()})
        record = retained['materials'][0]
        needs = [item['id'] for item in evidence_contract(retained)['question_requirements']['q1']]
        spec = {'id': 'local', 'artifact_id': record['id'], 'object': '原文',
            'text': quote, 'record_locator': {'part': '段落1'}, 'period': '2026年7月',
            'nature': '合同政策条款', 'unit': '不适用', 'question_ids': ['q1'],
            'published_at': '2026-07-19', 'publication': {
                'at': '2026-07-19', 'precision': '日', 'timezone': 'Asia/Shanghai',
                'certainty': '已确认', 'basis': '同原件明确发布时间',
                'basis_ref': {'kind': 'material', 'artifact_id': record['id'],
                    'snapshot_hash': record['snapshot_hash'], 'part': '段落2', 'quote': publication}},
            'review': {'question_ids': ['q1'], 'allowed_natures': ['合同政策条款'],
                'conditions': ['只核对本原件条款日期'], 'reason': '原件与研究问题相符',
                'evidence_need_ids': {'q1': needs}}}
        fixture.post(route+'/start', {'host_result': {'evidence_specs': [spec],
            'answers': {'q1': {'answer': '2026年7月19日生效。', 'refs': ['local']}}}})
        worker = fixture.server.agent.workers[task['id']]
        worker.join(10)
        self.assertFalse(worker.is_alive())
        done = fixture.server.agent.archive.get(task['id'])
        self.assertEqual(done['status'], '已完成', done.get('error'))
        self.assertEqual(done['plan_version'], 2)
        self.assertEqual(done['questions'][0]['answer'], '2026年7月19日生效。')
        self.assertEqual(done['ledger']['used']['model_calls'], 0)
        self.assertEqual(done['ledger']['used']['data_requests'], 0)
        self.assertEqual(done['evidence'][0]['source_receipt']['component'], 'EvidenceCollector')
        self.assertEqual(done['evidence'][0]['applicability_receipt']['component'], 'ResearchAnalyzer')
        self.assertEqual(fixture.server.agent.export(done['id'])['content']['questions'], done['questions'])
