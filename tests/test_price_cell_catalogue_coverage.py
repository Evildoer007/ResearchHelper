"""Synthetic long-window cell catalogues, distinct from live market evidence."""
from datetime import date, timedelta
import json
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import api_cell_choices, candidate_evidence, expand_api_cell_selections, snapshot_record
from researchhelper.output_schema import api_extraction_output_schema, decode_api_extraction_output
from researchhelper.reasoning import evidence_contract, extractable_source_views


class PriceCellCatalogueCoverageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='price-catalogue-', dir=ROOT / 'result/acceptance')
        self.addCleanup(directory.cleanup)
        self.agent = ResearchAgent(ResearchArchive(directory.name), ROOT, offline=True, require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        guard = patch('socket.socket.connect', side_effect=AssertionError('Synthetic catalogue blocks networking'))
        guard.start(); self.addCleanup(guard.stop)

    def task(self, count, extra_fields=False, missing_row=None, objects=None):
        task = self.agent.create('合成窗口协议验收，不是真实行情。核对600519.SH收盘价。',
            objects=objects or ['600519.SH'], research_mode='current', questions=[dict(id='q1',
                question='只核对合成响应的收盘价，不形成实际投资结论', kind='fact',
                evidence_needs=['本次响应中的收盘价'], method_needs=['observed_price_return_v1'])])
        dates = []; day = date(2026, 6, 1)
        while len(dates) < count:
            if day.weekday() < 5: dates.append(day.isoformat())
            day += timedelta(days=1)
        params = dict(codes='600519.SH', indicators='open,close' if extra_fields else 'close',
            startdate=dates[0], enddate=dates[-1], functionpara=dict(Currency='RMB', CPS='1', Fill='Omit'))
        columns = dict(close=[100 + index / 10 for index in range(count)])
        if missing_row is not None: columns['close'][missing_row] = None
        if extra_fields: columns['open'] = [99 + index / 10 for index in range(count)]
        source = snapshot_record(task, 'ifind', 'cmd_history_quotation', params,
            dict(errorcode=0, tables=[dict(thscode='600519.SH', time=dates, table=columns)]))
        # Explicit synthetic transport receipt. No real request is made.
        task['source_records'] = [source]
        task['ledger']['dispatches'] = [dict(kind='data_requests', batch=task['batch'],
            fingerprint=source['request_hash'], status='成功', transport_isolated=True,
            network_request_started=True, network_worker_reaped=True, sdk_wire_requests_started=1,
            response_snapshot_hash=source['hash'], response_obtained_at=source['obtained_at'])]
        source['acquisition'] = dict(version=1, task_id=task['id'], research_version=task['version'],
            batch=task['batch'], dispatch_index=0, request_hash=source['request_hash'],
            snapshot_hash=source['hash'], obtained_at=source['obtained_at'])
        return task, source

    def second_source(self, task, count):
        first = task['source_records'][0]
        dates = first['snapshot']['tables'][0]['time'][:count]
        params = dict(first['parameters'], codes='601318.SH', enddate=dates[-1])
        source = snapshot_record(task, 'ifind', 'cmd_history_quotation', params,
            dict(errorcode=0, tables=[dict(thscode='601318.SH', time=dates,
                table=dict(close=[200 + index / 10 for index in range(count)]))]))
        index = len(task['ledger']['dispatches'])
        task['ledger']['dispatches'].append(dict(task['ledger']['dispatches'][0],
            fingerprint=source['request_hash'], response_snapshot_hash=source['hash'],
            response_obtained_at=source['obtained_at']))
        source['acquisition'] = dict(first['acquisition'], dispatch_index=index,
            request_hash=source['request_hash'], snapshot_hash=source['hash'], obtained_at=source['obtained_at'])
        task['source_records'].append(source)
        return source

    def test_month_window_uses_complete_selection_despite_short_location_preview(self):
        task, _ = self.task(22)
        contract = evidence_contract(task)
        self.assertEqual(len(contract['sources'][0]['api_cells']['cells']), 16)
        self.assertFalse(contract['sources'][0]['api_cells']['scan_complete'])
        self.assertEqual(len(contract['sources'][0]['cell_choices']), 22)
        schema = api_extraction_output_schema(task, extractable_source_views(task))
        self.assertIsNotNone(schema, 'A display hint limit must not disable a complete price catalogue')
        self.assertEqual(schema['properties']['version']['enum'], [2])

    def test_coverage_binds_all_offered_cells_and_original_snapshot(self):
        task, source = self.task(22)
        before = fingerprint(task)
        item = evidence_contract(task)['sources'][0]
        coverage = item['cell_choice_coverage']
        self.assertTrue(coverage['selection_complete'])
        self.assertEqual(coverage['eligible_positions'], 22)
        self.assertEqual(coverage['choices_hash'], fingerprint(item['cell_choices']))
        self.assertEqual(coverage['snapshot_hash'], source['hash'])
        self.assertFalse(coverage['candidate_is_approval'])
        self.assertEqual(fingerprint(task), before)

    def test_last_row_is_selectable_and_remains_independently_unapproved(self):
        task, source = self.task(22)
        cells = api_cell_choices(task, source)
        review = dict(question_ids=['q1'], allowed_natures=['已实现事实'], conditions=['仅合成协议验收'],
            reason='明确选择同一原始响应中的首尾单元，不推断总收益',
            evidence_need_texts=[dict(question_id='q1', texts=['本次响应中的收盘价'])])
        wire = dict(version=2, evidence_specs=[dict(api_cell_selections=[cells[0]['candidate_id'], cells[-1]['candidate_id']],
            question_ids=['q1'], review=review)])
        decoded = decode_api_extraction_output(wire, api_extraction_output_schema(task, extractable_source_views(task)))
        specs = expand_api_cell_selections(task, decoded['evidence_specs'])
        items = [candidate_evidence(task, source, spec) for spec in specs]
        self.assertEqual([item['record_locator']['row'] for item in items], [0, 21])
        self.assertEqual(items[-1]['value'], source['snapshot']['tables'][0]['table']['close'][-1])
        self.assertTrue(all(item['applicability_check'] == '待核验' for item in items))

    def test_sixty_four_rows_fit_but_sixty_five_do_not_pretend_complete(self):
        for count in (64, 65):
            task, _ = self.task(count)
            item = evidence_contract(task)['sources'][0]
            self.assertEqual(item['cell_choice_coverage']['selection_complete'], count == 64)
            schema = api_extraction_output_schema(task, extractable_source_views(task))
            if count == 64: self.assertEqual(schema['properties']['version']['enum'], [2])
            else: self.assertIsNone(schema)

    def test_removed_or_modified_offered_cells_cannot_reuse_complete_flag(self):
        task, _ = self.task(22)
        for change in ('remove', 'locator', 'identity'):
            contract = evidence_contract(task)
            if change == 'remove': contract['sources'][0]['cell_choices'].pop()
            elif change == 'locator': contract['sources'][0]['cell_choices'][-1]['record_locator']['row'] = 0
            else: contract['sources'][0]['cell_choices'][-1]['candidate_id'] = 'rh_cell_foreign'
            with self.subTest(change=change), patch('researchhelper.reasoning.evidence_contract', return_value=contract):
                self.assertIsNone(api_extraction_output_schema(task, extractable_source_views(task)))

    def test_other_requested_fields_are_not_silently_reduced_to_close_only(self):
        task, _ = self.task(22, extra_fields=True)
        item = evidence_contract(task)['sources'][0]
        self.assertFalse(item['cell_choice_coverage']['selection_complete'])
        self.assertIsNone(api_extraction_output_schema(task, extractable_source_views(task)))

    def test_legacy_tasks_still_use_their_old_container(self):
        task, _ = self.task(4)
        task.pop('api_cell_selection_encoding')
        self.assertEqual(api_extraction_output_schema(task, extractable_source_views(task))['properties']['version']['enum'], [1])

    def test_missing_values_are_disclosed_and_never_filled_with_zero(self):
        task, source = self.task(22, missing_row=21)
        item = evidence_contract(task)['sources'][0]
        self.assertEqual(item['cell_choice_coverage']['invalid_positions'], 1)
        self.assertEqual(len(item['cell_choices']), 21)
        self.assertFalse(item['cell_choice_coverage']['absence_is_proof'])
        self.assertIsNone(source['snapshot']['tables'][0]['table']['close'][21])
        self.assertNotIn(21, [choice['record_locator']['row'] for choice in item['cell_choices']])

    def test_catalogue_metadata_does_not_mutate_retained_live_study(self):
        path = ROOT / 'result/acceptance/one-sentence-research-ppid4ad2/task.json'
        before = path.read_bytes()
        task = json.loads(before)
        evidence_contract(task)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(task['status'], '已完成')

    def test_two_sources_keep_separate_complete_catalogues_within_shared_limit(self):
        task, _ = self.task(22, objects=['600519.SH', '601318.SH'])
        self.second_source(task, 22)
        sources = evidence_contract(task)['sources']
        self.assertEqual([len(item['cell_choices']) for item in sources], [22, 22])
        self.assertTrue(all(item['cell_choice_coverage']['selection_complete'] for item in sources))
        schema = api_extraction_output_schema(task, extractable_source_views(task))
        self.assertEqual(schema['properties']['version']['enum'], [2])
        positions = [set(choice['candidate_id'] for choice in item['cell_choices']) for item in sources]
        self.assertFalse(positions[0].intersection(positions[1]))

    def test_shared_selection_limit_is_not_reset_for_each_source(self):
        task, _ = self.task(40, objects=['600519.SH', '601318.SH'])
        self.second_source(task, 30)
        sources = evidence_contract(task)['sources']
        self.assertEqual([len(item['cell_choices']) for item in sources], [40, 24])
        self.assertFalse(sources[1]['cell_choice_coverage']['selection_complete'])
        self.assertIsNone(api_extraction_output_schema(task, extractable_source_views(task)))

