"""Detached real-response replay; old tasks, receipts and data are read-only."""
import copy
from decimal import Decimal
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.contracts import ContractError, fingerprint, validate_evidence
from researchhelper.economic_observations import contract, cell, validate
from researchhelper.evidence import (api_cell_choices, api_cell_choice_coverage,
    candidate_evidence, expand_api_cell_selection, current_snapshot_reference)
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import extractable_source_views, evidence_contract

ROOT = Path(__file__).resolve().parents[1]
RETAINED = ROOT/'result/acceptance/economic-discovery-live-20261023-v1/task.json'


class EconomicObservationTests(unittest.TestCase):
    def setUp(self):
        self.original = RETAINED.read_bytes()
        self.task = json.loads(self.original)
        self.task['economic_observation_version'] = 1
        self.task['input_hash'] = input_fingerprint(self.task)
        self.record = next(row for row in self.task['source_records'] if row['operation'] == 'edb_service')
        self.table = self.record['snapshot']['tables'][0]
        guard = patch('socket.socket.connect', side_effect=AssertionError('No network allowed'))
        guard.start()
        self.addCleanup(guard.stop)

    def tearDown(self):
        self.assertEqual(RETAINED.read_bytes(), self.original)

    def rebind_detached_response(self):
        # A deliberately synthetic altered response in this disposable task,
        # never a new supplier acquisition or a change to the retained study.
        self.record['hash'] = fingerprint(self.record['snapshot'])
        self.record['acquisition']['snapshot_hash'] = self.record['hash']
        event = self.task['ledger']['dispatches'][self.record['acquisition']['dispatch_index']]
        event.update(response_snapshot_hash=self.record['hash'], synthetic_test_transport=True)

    def proposal(self, choice=None):
        choice = choice or api_cell_choices(self.task, self.record)[0]
        review = dict(question_ids=['q1'], allowed_natures=['已实现事实'],
            reason='核对国家统计局月度CPI当月同比的实际序列，不当作年率、累计或环比。',
            conditions=['仅当前取得版本，不证明历史首发，rtime不是首次公开时间。'],
            evidence_need_texts={'q1': ['月度CPI当月同比']})
        return expand_api_cell_selection(self.task,
            dict(api_cell_selection=choice['candidate_id'], question_ids=['q1'], review=review))

    def adopted(self, choice=None):
        spec = self.proposal(choice)
        item = candidate_evidence(self.task, self.record, spec)
        self.assertEqual(item['applicability_check'], '待核验')
        ResearchAnalyzer().assess(self.task, item, spec['review'], self.task['source_records'])
        validate_evidence(item, self.task)
        return item

    def test_nine_actual_monthly_cells_preserve_original_strings_and_units(self):
        found = contract(self.task, self.record)
        self.assertEqual(found['status'], 'registered_observations')
        self.assertEqual(len(found['cells']), 9)
        self.assertFalse(found['numeric_approval'])
        self.assertEqual(found['gaps'], [])
        self.assertEqual([row['raw_value'] for row in found['cells']], self.table['value'])
        self.assertEqual(found['cells'][0]['period'], '2026-09')
        self.assertEqual(found['cells'][0]['economic_context']['frequency'], 'M')
        self.assertEqual(found['cells'][0]['unit'], '%')

    def test_each_real_point_passes_source_and_applicability_independently(self):
        for choice in api_cell_choices(self.task, self.record):
            item = self.adopted(choice)
            self.assertIsNone(item['published_at'])
            self.assertNotIn('calculation_context', item)
            self.assertEqual(item['identity']['mode'], 'economic_indicator_observation')
            self.assertEqual(Decimal(str(item['value'])), Decimal(item['raw_value']))

    def test_real_api_enters_reading_and_complete_selection_protocol(self):
        views = extractable_source_views(self.task)
        self.assertIn(self.record['id'], {view['id'] for view in views})
        view = next(row for row in evidence_contract(self.task, views)['sources'] if row['artifact_id'] == self.record['id'])
        self.assertEqual(len(view['cell_choices']), 9)
        self.assertTrue(view['cell_choice_coverage']['selection_complete'])
        self.assertTrue(view['numeric_evidence_registered'])
        self.assertFalse(view['economic_raw_only'])

    def test_legacy_boolean_historical_and_missing_search_do_not_gain_evidence(self):
        for flag in (None, True, 2):
            self.task['economic_observation_version'] = flag
            self.assertEqual(api_cell_choices(self.task, self.record), [])
            self.assertIsNone(current_snapshot_reference(self.task, self.record))
        self.task['economic_observation_version'] = 1
        self.task['research_mode'] = 'historical'
        self.assertEqual(contract(self.task, self.record)['cells'], [])
        self.task['research_mode'] = 'current'
        self.task['source_records'] = [row for row in self.task['source_records'] if row['operation'] != 'search_edb']
        self.assertEqual(contract(self.task, self.record)['cells'], [])

    def test_modified_snapshot_without_transport_binding_is_rejected(self):
        self.table['value'][0] = '999'
        self.assertEqual(contract(self.task, self.record)['cells'], [])

    def test_null_preserves_other_points_and_is_never_zero(self):
        self.table['value'][0] = None
        self.rebind_detached_response()
        found = contract(self.task, self.record)
        self.assertEqual(len(found['cells']), 8)
        self.assertEqual(len(found['gaps']), 1)
        self.assertFalse(found['response_coverage_complete'])
        self.assertNotIn('2026-09', {row['period'] for row in found['cells']})

    def test_invalid_precision_does_not_round_an_original_value(self):
        self.table['value'][0] = '0.123456789012345678901'
        self.rebind_detached_response()
        self.assertEqual(len(contract(self.task, self.record)['cells']), 8)

    def test_duplicate_period_is_not_selected_or_averaged(self):
        self.table['time'][1] = self.table['time'][0]
        self.rebind_detached_response()
        self.assertEqual(contract(self.task, self.record)['cells'], [])

    def test_wrong_name_does_not_borrow_search_semantics(self):
        self.table['index_name'][0] = 'CPI:累计同比'
        self.rebind_detached_response()
        found = contract(self.task, self.record)
        self.assertEqual(len(found['cells']), 8)
        self.assertNotIn('2026-09', {row['period'] for row in found['cells']})

    def test_request_echo_cannot_change_the_actual_query_window(self):
        self.record['snapshot']['inputParams']['edate'] = '20261231'
        self.rebind_detached_response()
        self.assertEqual(contract(self.task, self.record)['cells'], [])

    def test_future_revision_does_not_enter_current_observations(self):
        self.table['rtime'][0] = '2030-01-01 09:30:00'
        self.rebind_detached_response()
        self.assertEqual(len(contract(self.task, self.record)['cells']), 8)

    def test_value_unit_frequency_and_metadata_tampering_are_rejected(self):
        spec = self.proposal()
        for name, value in (('value', 99), ('unit', '亿元'), ('period', '2026-08'), ('raw_value', 0.9)):
            with self.assertRaises(ContractError):
                candidate_evidence(self.task, self.record, dict(spec, **{name: value}))
        wrong = copy.deepcopy(spec)
        wrong['economic_context']['frequency'] = 'Y'
        with self.assertRaises(ContractError):
            candidate_evidence(self.task, self.record, wrong)

    def test_unverified_calculation_context_is_not_minted_from_statistics(self):
        with self.assertRaises(ContractError):
            candidate_evidence(self.task, self.record, dict(self.proposal(), calculation_context={'currency': 'CNY'}))

    def test_selection_limit_is_not_a_completeness_claim(self):
        choices = api_cell_choices(self.task, self.record, 3)
        coverage = api_cell_choice_coverage(self.record, choices, 3, task=self.task)
        self.assertEqual(len(choices), 3)
        self.assertFalse(coverage['selection_complete'])
        self.assertFalse(coverage['absence_is_proof'])

    def test_receipt_rejects_changed_semantics_even_when_numeric_value_is_same(self):
        item = self.adopted()
        item['economic_context']['data_source'] = 'other publisher'
        with self.assertRaises(ContractError):
            validate_evidence(item, self.task)
