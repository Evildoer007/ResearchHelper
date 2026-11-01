"""Need-driven fixed routes and reuse of retained real catalogue aliases.

Offline replay only. The closed original task and downloaded files are read
without mutation; no model, supplier, production store or other product access.
"""
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import (acquisition_channels, data_request_contract,
    filing_selection_identity, snapshot_record)
from researchhelper.planning import question_specs
from researchhelper.reasoning import validate_plan

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / 'result/acceptance/selected-filing-originals-2cdlwnwg/task.json'


class NeedDrivenAcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.digest = hashlib.sha256(ORIGINAL.read_bytes()).hexdigest()
        self.task = json.loads(ORIGINAL.read_text())
        self.task['source_attempts'] = {}
        self.filing = next(r for r in self.task['source_records'] if r.get('kind') == 'filing')
        self.catalog = next(r for r in self.task['source_records'] if r['id'] == self.filing['parameters']['catalog_id'])
        params = dict(self.catalog['parameters'], beginrDate='2026-01-01')
        self.alias = snapshot_record(self.task, 'ifind', 'report_query', params, self.catalog['snapshot'])
        self.task['source_records'].append(self.alias)
        self.agent = ResearchAgent.__new__(ResearchAgent)
        self.agent.collector = Mock()
        self.agent._journal = Mock()
        self.agent._active = Mock()
        self.agent._save_model_checkpoint = Mock()
        self.agent._gap = Mock()
        self.agent.source_credentials = {}
        guard = patch('socket.socket.connect', side_effect=AssertionError('No network'))
        guard.start(); self.addCleanup(guard.stop)

    def tearDown(self):
        self.assertEqual(hashlib.sha256(ORIGINAL.read_bytes()).hexdigest(), self.digest)

    def need(self, *, ident='required_filing', catalog=None, source_id=None, reason='核对本问题所需持仓和资产配置，保留披露期间'):
        return dict(id=ident, source='ifind', operation='filing_original',
            parameters=dict(catalog_id=(catalog or self.catalog)['id'], source_id=source_id or self.filing['directory']['source_id']),
            question_ids=['q1'], selection_reason=reason)

    def test_fixed_routes_are_implemented_not_arbitrary_websites(self):
        channels = acquisition_channels()
        self.assertEqual(channels['default_source'], 'ifind')
        self.assertFalse(channels['automatic_fallback'])
        routes = {c['id']: c for c in channels['channels']}
        self.assertEqual(set(routes), {'ifind', 'disclosure_original', 'user_material', 'tinyshare'})
        self.assertFalse(routes['tinyshare']['enabled_by_default'])
        self.assertFalse(routes['user_material']['network'])
        self.assertTrue(routes['disclosure_original']['requires_retained_catalogue'])
        self.assertFalse(routes['disclosure_original']['arbitrary_url_allowed'])
        self.assertFalse(routes['disclosure_original']['redirect_allowed'])

    def test_tools_supply_same_policy_without_selecting_or_mutating(self):
        before = fingerprint(self.task)
        policy = data_request_contract(self.task)['acquisition_channels']
        self.assertEqual(policy, acquisition_channels())
        self.assertFalse(policy['download_policy']['whole_catalogue_download_by_default'])
        self.assertFalse(policy['download_policy']['download_is_evidence_approval'])
        self.assertEqual(fingerprint(self.task), before)

    def test_explicit_reason_is_required_even_for_default_ifind_original(self):
        for value in (None, '', ' ', [], 1):
            with self.subTest(value=value), self.assertRaises(ContractError):
                self.agent._validated_data_needs(self.task, [self.need(reason=value)])
        self.agent.collector.external.assert_not_called()

    def test_download_requires_actual_question_binding(self):
        need = self.need(); need['question_ids'] = ['foreign']
        with self.assertRaises(ContractError):
            self.agent._validated_data_needs(self.task, [need])

    def test_catalogue_aliases_identify_one_original(self):
        first, alias = self.need(), self.need(catalog=self.alias)
        self.assertNotEqual(first['parameters'], alias['parameters'])
        self.assertEqual(filing_selection_identity(self.task, first['parameters']),
            filing_selection_identity(self.task, alias['parameters']))

    def test_duplicate_originals_rejected_before_any_dispatch(self):
        before = copy.deepcopy(self.task['ledger'])
        with self.assertRaises(ContractError):
            self.agent._acquire_sources(self.task, [self.need(), self.need(ident='duplicate', catalog=self.alias)])
        self.agent.collector.external.assert_not_called()
        self.assertEqual(self.task['ledger'], before)

    def test_model_plan_rejects_same_alias_duplicates(self):
        plan = dict(questions=question_specs(self.task), calculations=[], data_requests=[self.need(),
            self.need(ident='alias', catalog=self.alias)])
        with self.assertRaises(ContractError):
            validate_plan(self.task, plan)

    def test_different_originals_are_allowed_no_fixed_document_count(self):
        originals = [r for r in self.task['source_records'] if r.get('kind') == 'filing']
        requests = [self.need(ident='doc_'+str(i), source_id=r['directory']['source_id']) for i, r in enumerate(originals)]
        self.assertEqual(len(self.agent._validated_data_needs(self.task, requests)), 3)

    def test_same_file_serves_multiple_questions_by_one_request(self):
        self.task['questions'].append(dict(self.task['questions'][0], id='q2'))
        need = self.need(); need['question_ids'] = ['q1', 'q2']
        self.assertEqual(len(self.agent._validated_data_needs(self.task, [need])), 1)

    def test_retained_original_is_reused_through_another_catalogue(self):
        before = copy.deepcopy(self.task['ledger'])
        self.agent._acquire_sources(self.task, [self.need(catalog=self.alias)])
        self.agent.collector.external.assert_not_called()
        self.assertEqual(self.task['ledger'], before)
        self.assertIn('required_filing', self.filing['data_need_ids'])

    def test_failure_cannot_be_retried_by_switching_catalogue_id(self):
        first = self.need()
        identity = filing_selection_identity(self.task, first['parameters'])
        self.task['source_records'] = [r for r in self.task['source_records'] if r.get('kind') != 'filing']
        self.task['source_attempts']['old_attempt'] = dict(original_identity=identity, status='失败或中断')
        self.agent._acquire_sources(self.task, [self.need(catalog=self.alias)])
        self.agent.collector.external.assert_not_called()
        self.agent._gap.assert_called_once()

    def test_arbitrary_url_rejected_without_credentials_or_network(self):
        need = self.need(); need['parameters']['url'] = 'https://example.com/file.pdf'
        with self.assertRaises(ContractError):
            self.agent._acquire_sources(self.task, [need])
        self.agent.collector.external.assert_not_called()

    def test_empty_plan_remains_valid_when_materials_are_sufficient(self):
        self.assertEqual(self.agent._validated_data_needs(self.task, []), [])
        validate_plan(self.task, dict(questions=question_specs(self.task), data_requests=[], calculations=[]))


if __name__ == '__main__': unittest.main()
