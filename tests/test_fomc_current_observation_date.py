"""Replay retained public bytes on copies; never resume the live source check."""
import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.contracts import ContractError, validate_evidence
from researchhelper.evidence import candidate_evidence, current_original_reference

ORIGINAL = ROOT / 'result/acceptance/fomc-source-live-ldktj1_x/task.json'


class FOMCCurrentObservationDateTests(unittest.TestCase):
    def setUp(self):
        self.digest = hashlib.sha256(ORIGINAL.read_bytes()).hexdigest()
        self.task = json.loads(ORIGINAL.read_text())
        self.record = self.task['source_records'][-1]
        self.parts = self.record['snapshot']['parts']
        self.header = next(part for part in self.parts if part['text'] == 'September 16, 2026')
        self.body = next(part for part in self.parts if part['text'].startswith('The Committee decided'))
        guard = patch('socket.socket.connect', side_effect=AssertionError('No network'))
        guard.start(); self.addCleanup(guard.stop)

    def tearDown(self):
        self.assertEqual(hashlib.sha256(ORIGINAL.read_bytes()).hexdigest(), self.digest)

    def spec(self):
        return dict(id='statement_direction', artifact_id=self.record['id'], object='FOMC',
            record_locator=dict(part=self.body['locator'], period_part=self.header['locator'],
                                period_quote=self.header['text']), text=self.body['text'],
            period='2026-09-16', nature='合同政策条款', unit='不适用', published_at=None,
            availability=current_original_reference(self.task, self.record),
            publication=dict(at=None, basis_ref=None, precision='未知', timezone='未知', certainty='未知',
                             basis='首次公开时间未知，仅核验当前下载声明'), question_ids=['q1'],
            review=dict(question_ids=['q1'], allowed_natures=['合同政策条款'],
                conditions=['仅核对该日期声明的当前版本，不推断实施时间或事件因果'],
                reason='同一声明原文及独立日期标题', temporal_use='current_version'))

    def test_exact_english_header_can_anchor_current_statement_not_publication(self):
        spec = self.spec()
        item = candidate_evidence(self.task, self.record, spec)
        self.assertEqual(item['source_check'], '通过')
        self.assertEqual(item['applicability_check'], '待核验')
        item = ResearchAnalyzer().assess(self.task, item, spec['review'], self.task['source_records'])
        validate_evidence(item, self.task)
        self.assertIsNone(item['published_at'])
        self.assertFalse(item['availability']['historical_availability_approved'])
        self.assertFalse(item['availability']['event_causality_approved'])

    def test_last_update_or_implementation_date_cannot_replace_the_header(self):
        for prefix in ('Last Update:', 'Implementation Note issued'):
            spec = self.spec()
            part = next(part for part in self.parts if part['text'].startswith(prefix))
            spec['record_locator']['period_part'] = part['locator']
            with self.assertRaises(ContractError):
                candidate_evidence(self.task, self.record, spec)

    def test_wrong_day_or_free_form_date_still_fails(self):
        for changes in ({'period': '2026-09-17'}, {'period': '2026-09'}, {'published_at': '2026-09-16'}):
            spec = dict(self.spec(), **changes)
            with self.assertRaises(ContractError):
                candidate_evidence(self.task, self.record, spec)

    def test_legacy_task_does_not_gain_fomc_or_english_date_permission(self):
        spec = self.spec()
        self.task.pop('official_policy_channels_version')
        self.assertIsNone(current_original_reference(self.task, self.record))
        with self.assertRaises(ContractError):
            candidate_evidence(self.task, self.record, spec)

    def test_date_format_support_does_not_enable_causal_review(self):
        spec = self.spec()
        item = candidate_evidence(self.task, self.record, spec)
        spec['review']['temporal_use'] = 'event_causality'
        with self.assertRaises(ContractError):
            ResearchAnalyzer().assess(self.task, item, spec['review'], self.task['source_records'])
