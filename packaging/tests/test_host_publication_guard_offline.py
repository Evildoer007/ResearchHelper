"""Host and preassembled evidence must share original-bound publication checks.

All records are constructed in memory; no archive, provider or model is used.
"""

import copy
import unittest

from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.cases import get_case
from researchhelper.contracts import ContractError, DOCUMENT_CONTENT_INTENT, validate_evidence
from researchhelper.evidence import (
    candidate_evidence, extract_material, filing_record, material_record,
    snapshot_record, verification_receipt,
)


class HostPublicationGuardTests(unittest.TestCase):
    def setUp(self):
        self.task = {
            'id': 'publication_guard', 'version': 1, 'input_hash': 'guard_input',
            'intent': '综合研究', 'objects': ['600000.SH'],
            'cutoff': '2026-07-20T18:00:00+08:00', 'fixture': None, 'synthetic': False,
            'questions': [{'id': 'q1', 'question': '核查原件事实', 'kind': 'fact'}],
            'source_records': [], 'materials': [],
        }

    def material(self, publication_text=''):
        payload = ('营业状态正常。' + publication_text).encode()
        record = material_record(self.task, extract_material('source.txt', payload), payload)
        self.task['materials'].append(record)
        return record

    def spec(self, record, anchor=False):
        value = {
            'id': 'fact1', 'artifact_id': record['id'], 'object': '600000.SH',
            'record_locator': {'part': '段落1'}, 'text': '营业状态正常。',
            'published_at': '2026-07-19', 'period': '2026-07-19',
            'nature': '已实现事实', 'unit': '不适用', 'question_ids': ['q1'],
            'publication': {'at': '2026-07-19', 'basis': '另一份未归档资料的日期',
                'precision': '日', 'timezone': 'Asia/Shanghai', 'certainty': '已确认'},
            'review': {'question_ids': ['q1'], 'allowed_natures': ['已实现事实'],
                'conditions': ['仅核对原件事实'], 'reason': '摘录对应当前问题'},
        }
        if anchor:
            value['publication']['basis_ref'] = {
                'kind': 'material', 'artifact_id': record['id'],
                'snapshot_hash': record['snapshot_hash'], 'part': '段落1',
                'quote': '发布日期2026年7月19日。',
            }
        return value

    def accept(self, record, spec):
        item = candidate_evidence(self.task, record, spec)
        ResearchAnalyzer().assess(self.task, item, spec['review'],
                                  self.task['source_records'] + self.task['materials'])
        validate_evidence(item, self.task)
        return item

    def test_unknown_material_date_cannot_be_host_confirmed(self):
        record = self.material()
        with self.assertRaises(ContractError):
            self.accept(record, self.spec(record))

    def test_future_original_cannot_use_host_backdated_declaration(self):
        record = self.material('发布日期2026年7月25日。')
        with self.assertRaises(ContractError):
            self.accept(record, self.spec(record))

    def test_trade_date_api_is_not_publication_proof(self):
        record = snapshot_record(self.task, 'tinyshare', 'daily',
            {'ts_code': '600000.SH', 'start_date': '20260719', 'end_date': '20260719',
             'fields': 'ts_code,trade_date,close'},
            {'code': 0, 'data': [{'ts_code': '600000.SH', 'trade_date': '20260719', 'close': 10}]})
        self.task['source_records'].append(record)
        spec = self.spec(record)
        spec.update(record_locator={'row': 0, 'field': 'close'}, raw_value=10,
            raw_unit='元', value=10, unit='元', conversion_factor=1,
            identity={'provider_code': '600000.SH', 'object': '600000.SH',
                'basis': '映射已登记', 'valid_from': '2026-01-01', 'valid_to': '2026-12-31'})
        with self.assertRaises(ContractError):
            self.accept(record, spec)

    def test_preassembled_receipts_cannot_remove_publication_anchor(self):
        record = self.material('发布日期2026年7月19日。')
        item = self.accept(record, self.spec(record, anchor=True))
        item['publication'].pop('basis_ref')
        for component, key in [('EvidenceCollector', 'source_receipt'),
                               ('ResearchAnalyzer', 'applicability_receipt')]:
            item[key] = verification_receipt(item, self.task, component, '通过', '宿主重新签发')
        with self.assertRaises(ContractError):
            validate_evidence(item, self.task)

    def test_same_original_publication_anchor_passes(self):
        record = self.material('发布日期2026年7月19日。')
        self.assertEqual(self.accept(record, self.spec(record, anchor=True))['source_check'], '通过')

    def test_explicit_document_content_keeps_unknown_publication(self):
        self.task['intent'] = DOCUMENT_CONTENT_INTENT
        record = self.material()
        spec = self.spec(record)
        spec.update(usage_scope='document_content', published_at=None,
            publication={'at': None, 'basis': '公开时间未核验', 'basis_ref': None,
                'precision': '未知', 'timezone': '未知', 'certainty': '未知'})
        self.assertEqual(self.accept(record, spec)['applicability_check'], '通过')

    def test_filing_same_original_directory_anchor_passes(self):
        record = filing_record(self.task, {
            'issuer': '600000.SH', 'source': '本地验收公告', 'source_id': 'guard1',
            'title': '营业公告', 'published_at': '2026-07-19',
            'original_url': 'https://example.org/guard.txt',
            'query_coverage': {'offline_fixture': True},
        }, 'guard.txt', '营业状态正常。'.encode())
        self.task['source_records'].append(record)
        spec = self.spec(record)
        spec['publication']['basis_ref'] = {'kind': 'directory', 'artifact_id': record['id'],
            'snapshot_hash': record['hash'], 'field': 'published_at'}
        self.assertEqual(self.accept(record, spec)['source_check'], '通过')
        spec['publication'].pop('basis_ref')
        with self.assertRaises(ContractError):
            self.accept(record, spec)

    def test_wrong_hash_and_foreign_original_are_rejected(self):
        record = self.material('发布日期2026年7月19日。')
        for field, value in [('snapshot_hash', 'wrong_hash'), ('artifact_id', 'other_original')]:
            with self.subTest(field=field):
                spec = self.spec(record, anchor=True)
                spec['publication']['basis_ref'][field] = value
                with self.assertRaises(ContractError):
                    self.accept(record, spec)

    def test_synthetic_labels_cannot_bypass_ordinary_task(self):
        item = get_case('mechanism')['evidence'][0]
        for flag in (False, True):
            with self.subTest(synthetic=flag):
                self.task['synthetic'] = flag
                with self.assertRaises(ContractError):
                    validate_evidence(item, self.task)

    def test_unknown_fixture_and_mismatched_case_cannot_grant_exemption(self):
        item = get_case('mechanism')['evidence'][0]
        for key in ('not_registered', 'etf'):
            with self.subTest(fixture=key):
                self.task.update(fixture=key, synthetic=True)
                with self.assertRaises(ContractError):
                    validate_evidence(item, self.task)

    def test_registered_fixture_retains_only_its_registered_evidence(self):
        case = get_case('mechanism')
        self.task.update(fixture='mechanism', synthetic=True, objects=case['objects'],
                         intent=case['intent'], question=case['question'])
        item = case['evidence'][0]
        validate_evidence(item, self.task)
        changed = copy.deepcopy(item)
        changed['published_at'] = '2026-07-01'
        with self.assertRaises(ContractError):
            validate_evidence(changed, self.task)
