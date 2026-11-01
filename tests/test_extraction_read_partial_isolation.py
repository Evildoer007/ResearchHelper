"""Mixed proposals isolate contract failures without adopting invalid facts."""

import copy
import importlib
import unittest
from unittest.mock import Mock, patch

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.evidence import (api_cell_choices, expand_api_cell_selection,
    candidate_evidence, validate_current_snapshot)

_prefix = __package__ + '.' if __package__ else ''
_isolation = importlib.import_module(_prefix + 'test_extraction_read_failure_isolation')
_api = importlib.import_module(_prefix + 'test_ifind_financial_evidence')


class ExtractionReadPartialIsolationTests(unittest.TestCase):
    def setUp(self):
        _isolation.ExtractionReadFailureIsolationTests.setUp(self)
        self.api_fixture = _api.BasicFinancialEntryTests()
        self.api_fixture.setUp()
        self.addCleanup(self.api_fixture.doCleanups)
        source = self.api_fixture.source
        choice = api_cell_choices(self.api_fixture.task, source)[0]
        self.good = expand_api_cell_selection(self.api_fixture.task, dict(
            api_cell_selection=choice['candidate_id'], question_ids=['q1'], review={}))
        self.task['extraction_read_isolation_version'] = 2
        self.proposed['evidence_specs'] = [self.good, self.spec]
        self.agent._complete_initial_fee_inputs = Mock(side_effect=lambda task, *args: task['host_result'])

    def validate_dates(self, task, specs, bundle):
        for spec in specs:
            if spec.get('artifact_id') == 'original':
                validate_current_snapshot(spec, task)
            else:
                # Real retained API ledger/identity/unit/source checks, without
                # model calls or silently blessing a synthetic specification.
                record = next((row for row in self.api_fixture.task['source_records']
                               if row['id'] == spec.get('artifact_id')), None)
                if record is None:
                    raise ContractError('API提案引用其他任务或未读取来源')
                candidate_evidence(self.api_fixture.task, record, spec)

    def test_valid_api_survives_bad_original_without_automatic_approval(self):
        before = copy.deepcopy((self.task['ledger'], self.task['evidence'], self.proposed))
        result = self.agent._extract_model_sources(self.task)
        self.assertEqual(result['evidence_specs'], [self.good])
        self.assertEqual(before, (self.task['ledger'], self.task['evidence'], self.proposed))
        self.assertNotIn('source_check', result['evidence_specs'][0])
        rejected = self.task['reasoning_state']['rejected_extraction']
        self.assertEqual(rejected['rejected_items'][0]['spec'], self.spec)
        self.assertEqual(rejected['rejected_items'][0]['spec_hash'], fingerprint(self.spec))
        self.assertEqual(rejected['retained_spec_hashes'], [fingerprint(self.good)])
        self.assertFalse(rejected['evidence_approved'])
        self.assertEqual(self.task['gap_records'][0]['category'], 'evidence_extraction')
        final = candidate_evidence(self.api_fixture.task, self.api_fixture.source, result['evidence_specs'][0])
        self.assertEqual(final['source_check'], '通过')
        self.assertEqual(final['applicability_check'], '待核验')

    def test_all_invalid_returns_empty_without_accepting_extraction(self):
        self.proposed['evidence_specs'] = [self.spec]
        result = self.agent._extract_model_sources(self.task)
        self.assertEqual(result['evidence_specs'], [])
        self.assertNotIn('extraction', self.task['reasoning_state'])
        self.assertTrue(self.task['gaps'])

    def test_duplicate_ids_reject_every_occurrence(self):
        duplicate = copy.deepcopy(self.good)
        self.proposed['evidence_specs'] = [self.good, duplicate]
        result = self.agent._extract_model_sources(self.task)
        self.assertEqual(result['evidence_specs'], [])
        rejected = self.task['reasoning_state']['rejected_extraction']['rejected_items']
        self.assertEqual([item['index'] for item in rejected], [0, 1])
        self.assertTrue(all('重复证据编号' in item['reason'] for item in rejected))
        self.reader.assert_not_called()

    def test_existing_verified_id_cannot_be_overwritten(self):
        self.good['id'] = self.task['evidence'][0]['id']
        result = self.agent._extract_model_sources(self.task)
        self.assertEqual(result['evidence_specs'], [])
        self.assertEqual(self.task['evidence'], [dict(id='verified', source_check='通过')])

    def test_cross_task_reference_rejected_without_discarding_valid_api(self):
        foreign = copy.deepcopy(self.good)
        foreign.update(id='foreign_spec', artifact_id='foreign_task_artifact')
        self.proposed['evidence_specs'] = [foreign, self.good]
        result = self.agent._extract_model_sources(self.task)
        self.assertEqual(result['evidence_specs'], [self.good])
        self.assertIn('其他任务', self.task['reasoning_state']['rejected_extraction']['rejected_items'][0]['reason'])

    def test_real_interval_and_future_date_guards_remain_strict(self):
        self.agent._extract_model_sources(self.task)
        with self.assertRaisesRegex(ContractError, '不能充当单日观测'):
            validate_current_snapshot(self.spec, self.task)
        wrong = copy.deepcopy(self.spec)
        wrong['observation_date'] = '2026-12-31'
        with self.assertRaisesRegex(ContractError, '观测窗口后的数据'):
            validate_current_snapshot(wrong, self.task)

    def test_v1_retains_whole_batch_rejection(self):
        self.task['extraction_read_isolation_version'] = 1
        result = self.agent._extract_model_sources(self.task)
        self.assertEqual(result['evidence_specs'], [])
        self.assertNotIn('isolation_version', self.task['reasoning_state']['rejected_extraction'])

    def test_system_and_checkpoint_failures_propagate(self):
        self.reader.side_effect = OSError('read failed')
        with self.assertRaisesRegex(OSError, 'read failed'):
            self.agent._extract_model_sources(self.task)
        self.reader.side_effect = self.validate_dates
        self.agent._save_model_checkpoint.side_effect = OSError('write failed')
        with self.assertRaisesRegex(OSError, 'write failed'):
            self.agent._extract_model_sources(self.task)
