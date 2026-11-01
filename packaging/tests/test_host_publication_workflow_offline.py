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
from researchhelper.evidence import verification_receipt
from researchhelper.financial_sources import FINANCIAL_RECORD_KEYS
from researchhelper.reports import compose

ROOT = Path(__file__).resolve().parents[2]


class HostPublicationWorkflowTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='rh-publication-workflow-')
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.agent = ResearchAgent(ResearchArchive(self.root / 'native'), ROOT, offline=True)
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
        outputs = {'answers': {} if document else {'q1': {'answer': '营业状态正常。', 'refs': ['fact1']}}}
        outputs['evidence_specs' if preassembled is None else 'evidence'] = [spec if preassembled is None else preassembled]
        result = self.session.run(task['id'], **outputs) if route == 'skill' else self.agent.start(task['id'], outputs)
        self.assertEqual(result['run_mode'], 'host')
        self.assertEqual(result['ledger']['used']['model_calls'], 0)
        self.assertEqual(result['ledger']['used']['data_requests'], 0)
        return result

    def assert_rejected(self, task, reason):
        self.assertEqual(task['status'], '部分完成', task.get('error'))
        self.assertEqual(task['evidence'], [])
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
                agent, task = self.create(route)
                task = agent.add_snapshot(task['id'], 'tinyshare', 'daily',
                    {'ts_code': '600000.SH', 'start_date': '20260719', 'end_date': '20260719',
                     'fields': 'ts_code,trade_date,close'},
                    {'code': 0, 'data': [{'ts_code': '600000.SH', 'trade_date': '20260719', 'close': 10}]})
                record = task['source_records'][0]
                spec = self.spec(record)
                spec.update(record_locator={'row': 0, 'field': 'close'}, value=10, raw_value=10,
                    raw_unit='元', unit='元', conversion_factor=1,
                    identity={'provider_code': '600000.SH', 'object': '600000.SH', 'basis': '明确代码',
                        'valid_from': '2026-01-01', 'valid_to': '2026-12-31'})
                self.assert_rejected(self.run_host(route, task, spec), '首次公开时间缺少原始来源定位')

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
