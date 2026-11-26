"""Publication gates through real host/Skill execution and isolated archives.

Source Skill uses repository report resources because packaging is out of scope.
All providers are disabled; business responses below are explicit test records.
"""

import copy
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, DOCUMENT_CONTENT_INTENT, fingerprint
from researchhelper.engine import ResearchAgent
from tests.identity_fixture_support import synthetic_identity_basis
from researchhelper.evidence import verification_receipt, validate_publication_basis
from researchhelper.financial_sources import FINANCIAL_RECORD_KEYS
from researchhelper.reports import compose

ROOT = Path(__file__).resolve().parents[2]


class HostPublicationWorkflowTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='rh-publication-workflow-')
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.agent = ResearchAgent(ResearchArchive(self.root / 'native'), ROOT, offline=True, require_argument_support=True)
        source = importlib.util.spec_from_file_location('publication_workflow_session',
            ROOT / 'core/session.py')
        module = importlib.util.module_from_spec(source)
        original_path = list(sys.path)
        try:
            source.loader.exec_module(module)
        finally:
            sys.path[:] = original_path
        self.session = module.Session(self.root / 'skill-project')
        self.session.agent.resources = ROOT
        self.addCleanup(self.agent.close_runtime)
        self.addCleanup(self.session.agent.close_runtime)

    def create(self, route, intent='综合研究'):
        agent = self.session.agent if route == 'skill' else self.agent
        task = agent.create('核对原件所述营业状态。', objects=['600000.SH'], intent=intent,
            cutoff='2026-07-20T18:00:00+08:00',
            questions=[{'id': 'q1', 'question': '原件所述营业状态是什么？', 'kind': 'fact'}])
        return agent, task

    def material(self, agent, task, date=''):
        task = agent.add_material(task['id'], 'original.txt', ('营业状态正常。' + date).encode())
        return task, task['materials'][0]

    def spec(self, record, anchored=False):
        value = {'id': 'fact1', 'artifact_id': record['id'], 'object': '600000.SH',
            'text': '营业状态正常。', 'record_locator': {'part': '段落1'},
            'published_at': '2026-07-19', 'period': '2026-07-19', 'nature': '已实现事实',
            'unit': '不适用', 'question_ids': ['q1'],
            'publication': {'at': '2026-07-19', 'basis': '宿主借用其他资料日期',
                'precision': '日', 'timezone': 'Asia/Shanghai', 'certainty': '已确认'},
            'review': {'question_ids': ['q1'], 'allowed_natures': ['已实现事实'],
                'conditions': ['仅核查本份原件'], 'reason': '原件文字直接回答当前事实问题'}}
        if anchored:
            value['publication']['basis_ref'] = {'kind': 'material', 'artifact_id': record['id'],
                'snapshot_hash': record['snapshot_hash'], 'part': '段落1',
                'quote': '发布日期2026年7月19日。'}
        return value

    def run_host(self, route, task, spec, document=False, preassembled=None):
        agent = self.session.agent if route == 'skill' else self.agent
        outputs = {'answers': {} if document else {'q1': {'answer': '营业状态正常。', 'refs': ['fact1']}}}
        if preassembled is None:
            outputs['evidence_specs'] = [spec]
            outputs['evidence'] = copy.deepcopy(task.get('evidence', []))
        else:
            outputs['evidence'] = [preassembled]
        result = (self.session.run(task['id'], **outputs, defer_delivery=not document) if route == 'skill'
                  else agent.start(task['id'], outputs, defer_delivery=not document))
        if not document:
            if any(item['id'] == 'fact1' for item in result['evidence']):
                request = agent.argument_review_inputs(task['id'])['request']
                self.assertEqual(len(request['targets']), 1)
                target = request['targets'][0]
                self.assertEqual(target['id'], 'answer:q1.answer')
                self.assertEqual(target['text'], '营业状态正常。')
                self.assertEqual(target['evidence_refs'], ['fact1'])
                self.assertEqual(target['calculation_refs'], [])
                fact = next(item for item in result['evidence'] if item['id'] == 'fact1')
                self.assertEqual(fact['text'], target['text'])
                submission = dict(version=request['version'], review_hash=request['review_hash'],
                    questions=[dict(question_id='q1', status='answered', missing_evidence=[],
                                    reason='仅核对合成原件的这一句，不是投资判断。')],
                    reviews=[dict(target_id=target['id'], segments=[dict(text=target['text'],
                        verdict='supported', basis_refs=['evidence:fact1'],
                        explanation='回答逐字等于已定位的合成原文，只验证宿主执行流程。')])])
                agent.submit_argument_review(task['id'], submission)
            result = self.session.deliver(task['id']) if route == 'skill' else agent.start(task['id'])
        self.assertEqual(result['run_mode'], 'host')
        self.assertEqual(result['ledger']['used']['model_calls'], 0)
        self.assertEqual(result['ledger']['used']['data_requests'], 0)
        return result

    def assert_rejected(self, task, reason, retained_evidence=()):
        self.assertEqual(task['status'], '部分完成', task.get('error'))
        self.assertEqual(sorted(item['id'] for item in task['evidence']), sorted(retained_evidence))
        self.assertEqual(task['questions'][0]['status'], '未回答')
        self.assertTrue(task['delivery'])
        self.assertTrue(any(reason in item.get('rejection', '') for item in task['rejected_evidence']))

    def test_unknown_and_future_material_dates_fail_both_host_entries(self):
        for route in ('native', 'skill'):
            for original_date in ('', '发布日期2026年7月25日。'):
                with self.subTest(route=route, original_date=original_date):
                    agent, task = self.create(route)
                    task, record = self.material(agent, task, original_date)
                    final = self.run_host(route, task, self.spec(record))
                    self.assert_rejected(final, '首次公开时间缺少原始来源定位')
                    reopened = ResearchArchive(agent.archive.root).get(task['id'])
                    self.assertEqual(reopened['materials'][0]['snapshot'], record['snapshot'])
                    self.assertEqual(reopened['evidence'], [])

    def test_trade_date_api_fails_both_host_entries(self):
        for route in ('native', 'skill'):
            with self.subTest(route=route):
                agent = self.session.agent if route == 'skill' else self.agent
                task = agent.create('核对原始收盘价，不把交易日当作首次公开时间。', objects=['600000.SH'],
                    cutoff='2026-10-02T18:00:00+08:00', questions=[dict(id='q1', kind='fact', question='原始收盘价是多少？')])
                identity = synthetic_identity_basis(task, 'tinyshare', '600000.SH', '2026-09-01', '2026-09-30')
                retained = [item['id'] for item in task['evidence']]
                agent.archive.save(task)
                task = agent.add_snapshot(task['id'], 'tinyshare', 'daily',
                    {'ts_code': '600000.SH', 'start_date': '20260930', 'end_date': '20260930',
                     'fields': 'ts_code,trade_date,close'},
                    {'code': 0, 'data': [{'ts_code': '600000.SH', 'trade_date': '20260930', 'close': 10}]})
                spec = self.spec(task['source_records'][0])
                spec.update(record_locator={'row': 0, 'field': 'close'}, value=10, raw_value=10,
                    raw_unit='元', unit='元', conversion_factor=1, identity=identity,
                    period='2026-09-30', published_at='2026-09-30')
                spec['publication']['at'] = '2026-09-30'
                self.assert_rejected(self.run_host(route, task, spec),
                    '首次公开时间缺少原始来源定位', retained_evidence=retained)

    def test_anchored_material_completes_and_survives_reopen(self):
        for route in ('native', 'skill'):
            with self.subTest(route=route):
                agent, task = self.create(route)
                task, record = self.material(agent, task, '发布日期2026年7月19日。')
                final = self.run_host(route, task, self.spec(record, anchored=True))
                self.assertEqual(final['status'], '已完成', final.get('error'))
                reopened = ResearchArchive(agent.archive.root).get(task['id'])
                self.assertEqual(reopened['evidence'], final['evidence'])
                compose(reopened)

    def test_explicit_document_review_completes_without_publication(self):
        for route in ('native', 'skill'):
            with self.subTest(route=route):
                agent, task = self.create(route, DOCUMENT_CONTENT_INTENT)
                task, record = self.material(agent, task)
                spec = self.spec(record)
                spec.update(usage_scope='document_content', published_at=None,
                    publication={'at': None, 'basis': '公开时间未核验', 'basis_ref': None,
                        'precision': '未知', 'timezone': '未知', 'certainty': '未知'})
                final = self.run_host(route, task, spec, document=True)
                self.assertEqual(final['status'], '已完成', final.get('error'))
                self.assertIsNone(final['evidence'][0]['published_at'])
                self.assertEqual(final['evidence'][0]['usage_scope'], 'document_content')

    def test_reopened_receipts_cannot_be_resigned_without_anchor(self):
        agent, task = self.create('native')
        task, record = self.material(agent, task, '发布日期2026年7月19日。')
        final = self.run_host('native', task, self.spec(record, anchored=True))
        self.assertEqual(final['status'], '已完成')
        archive = ResearchArchive(agent.archive.root)
        reopened = archive.get(task['id'])
        before = fingerprint(reopened)
        item = copy.deepcopy(reopened['evidence'][0])
        item['publication'].pop('basis_ref')
        for component, key in [('EvidenceCollector', 'source_receipt'), ('ResearchAnalyzer', 'applicability_receipt')]:
            item[key] = verification_receipt(item, reopened, component, '通过', '宿主自行重签')
        changed = copy.deepcopy(reopened)
        changed['evidence'] = [item]
        with self.assertRaisesRegex(ContractError, '首次公开时间缺少原始来源定位'):
            validate_publication_basis(item, reopened['materials'] + reopened['source_records'])
        # Changed evidence also invalidates the independent argument review;
        # source rejection must not depend on which guard runs first.
        with self.assertRaises(ContractError):
            compose(changed)
        self.assertEqual(fingerprint(archive.get(task['id'])), before)
        # A fresh task tests direct host submission, not just report recomposition.
        task2 = agent.create('核对原件所述营业状态。', objects=['600000.SH'],
            cutoff=task['cutoff'], questions=[{'id': 'q1', 'question': '核对营业状态', 'kind': 'fact'}])
        task2, record2 = self.material(agent, task2, '发布日期2026年7月19日。')
        item.update(task_id=task2['id'], research_version=task2['version'], artifact=record2,
            artifact_id=record2['id'], snapshot=record2['snapshot'], snapshot_hash=record2['snapshot_hash'])
        for component, key in [('EvidenceCollector', 'source_receipt'), ('ResearchAnalyzer', 'applicability_receipt')]:
            item[key] = verification_receipt(item, task2, component, '通过', '宿主自行重签')
        self.assert_rejected(self.run_host('native', task2, None, preassembled=item),
                             '首次公开时间缺少原始来源定位')

    def test_financial_raw_only_snapshot_survives_without_approved_evidence(self):
        agent, task = self.create('skill')
        params = {'ts_code': '600000.SH', 'fields': ','.join(FINANCIAL_RECORD_KEYS) + ',revenue',
            'start_date': '20260701', 'end_date': '20260720', 'period': '20260630', 'report_type': '1'}
        response = {'code': 0, 'data': [{'ts_code': '600000.SH', 'ann_date': '20260719',
            'f_ann_date': '20260719', 'end_date': '20260630', 'report_type': '1',
            'comp_type': '2', 'update_flag': '1', 'revenue': 123.5}]}
        task = agent.add_snapshot(task['id'], 'tinyshare', 'income', params, response)
        record = task['source_records'][0]
        spec = self.spec(record)
        spec.update(record_locator={'row': 0, 'field': 'revenue'}, value=123.5, raw_value=123.5,
                    raw_unit='元', unit='元', conversion_factor=1, period='2026-06-30')
        final = self.run_host('skill', task, spec)
        self.assert_rejected(final, '财报原始记录')
        restored = ResearchArchive(agent.archive.root).get(task['id'])
        retained = restored['source_records'][0]
        self.assertEqual(retained['snapshot'], response)
        self.assertEqual(retained['hash'], fingerprint(response))
        self.assertTrue(retained['financial_source']['raw_only'])
        self.assertEqual(restored['evidence'], [])

    def test_unreviewed_anchored_material_still_delivers_initial_report(self):
        for route in ('native', 'skill'):
            with self.subTest(route=route):
                agent, task = self.create(route)
                task, record = self.material(agent, task, '发布日期2026年7月19日。')
                outputs = dict(evidence_specs=[self.spec(record, anchored=True)],
                    answers={'q1': dict(answer='营业状态正常。', refs=['fact1'])})
                final = self.session.run(task['id'], **outputs) if route == 'skill' else agent.start(task['id'], outputs)
                self.assertTrue(final['delivery'])
                self.assertEqual([item['id'] for item in final['evidence']], ['fact1'])
                self.assertEqual(final['status'], '部分完成')
                self.assertNotEqual(final['questions'][0]['status'], '已回答')
                self.assertEqual(final['ledger']['used']['model_calls'], 0)
                self.assertEqual(final['ledger']['used']['data_requests'], 0)
                self.assertEqual(agent.export(task['id'])['content']['evidence'], final['evidence'])
