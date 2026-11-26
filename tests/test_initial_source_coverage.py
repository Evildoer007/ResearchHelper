"""Required original-source dependencies cannot be hidden by another ready API."""
import copy
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests.legacy_replay_fixtures import ETF_ORIGINAL, etf_task
from researchhelper import acquisition
from researchhelper.contracts import budget, fingerprint
from researchhelper.reasoning import extractable_source_views


class InitialSourceCoverageTests(unittest.TestCase):
    def setUp(self):
        self.task = dict(id='source_scope', version=1, batch=1, cutoff='2026-10-17', input_hash='input',
            initial_acquisition_scope_version=1, run_mode='model', intent='综合研究', question='比较ETF价格与官方净值',
            objects=['510500.SH'], questions=[dict(id='price', kind='fact', required=True,
                evidence_needs=['收盘价']), dict(id='nav', kind='fact', required=True, evidence_needs=['官方净值'])],
            ledger=budget(dict(data_requests=100, model_calls=8, seconds=600)),
            source_records=[dict(id='quote', kind='api', source='ifind', operation='cmd_history_quotation',
                task_id='source_scope', research_version=1, question_ids=['price']),
                dict(id='directory', kind='api', source='ifind', operation='report_query',
                    task_id='source_scope', research_version=1, question_ids=['nav'])],
            materials=[], data_need_specs=[dict(id='find_nav', source='ifind', operation='report_query',
                parameters={}, question_ids=['nav'])], calculation_specs=[], gaps=[])
        self.original = dict(id='fund_original', kind='filing', source='ifind', operation='filing_original',
            task_id='source_scope', research_version=1, question_ids=['nav'])
        self.engine = SimpleNamespace(offline=False, model=object(),
            source_credentials={'ifind': {'refresh_token': 'SYNTHETIC_NOT_USED'}},
            _active_supplement=Mock(return_value=None), _reasoning_available=Mock(return_value=True),
            _save_model_checkpoint=Mock(), _active=Mock(), _validated_data_needs=Mock(),
            _gap=lambda task, text, selected: task['gaps'].append(text), _acquire_sources=Mock(side_effect=self.acquire))
        self.views = patch.object(acquisition, 'extractable_source_views', side_effect=self.extractable).start()
        self.contract = patch.object(acquisition, 'data_request_contract', return_value=dict(tools=[
            dict(source='ifind', operation=operation) for operation in
            ('get_thscode', 'report_query', 'filing_original', 'cmd_history_quotation')])).start()
        self.validate = patch.object(acquisition, 'validate_plan').start()
        patch.object(acquisition, 'context_sources', return_value={'catalog': []}).start()
        patch.object(acquisition, 'question_specs', side_effect=lambda task: copy.deepcopy(task['questions'])).start()
        patch.object(acquisition, 'supplement_request_blocked', return_value=False).start()
        patch.object(acquisition, 'navigation_progress', side_effect=lambda task: (set(), {'directory'},
            {record['id'] for record in task['source_records'] if record['kind'] == 'filing'})).start()
        self.model = patch.object(acquisition, 'model_object', side_effect=self.author).start()
        patch.object(socket.socket, 'connect', side_effect=AssertionError('No network')).start()
        self.addCleanup(patch.stopall)

    def extractable(self, task):
        return [dict(id=record['id'], kind=record['kind']) for record in task['source_records']
                if record['kind'] in ('filing', 'material') or record['id'] == 'quote']

    def author(self, model, task, phase, payload, fields):
        self.assertEqual(phase, 'acquire')
        task['ledger']['used']['model_calls'] += 1
        return dict(data_requests=[dict(id='original', source='ifind', operation='filing_original',
            parameters={'synthetic_selector': True}, question_ids=['nav'])], stop_reason=''), {'synthetic': True}

    def acquire(self, task, needs):
        task['source_records'].append(copy.deepcopy(self.original))
        task['ledger']['used']['data_requests'] += 1

    def coverage(self):
        return acquisition.initial_acquisition_coverage(self.task)

    def test_ready_price_does_not_cover_another_questions_original(self):
        coverage = self.coverage()
        self.assertFalse(coverage['ready'])
        self.assertEqual(coverage['pending_original_question_ids'], ['nav'])
        self.assertFalse(coverage['financial_approval'])

    def test_initial_navigation_finishes_declared_original_before_returning_ready(self):
        before = fingerprint(self.task['data_need_specs'])
        acquisition.complete_initial_acquisition(self.engine, self.task)
        self.model.assert_called_once()
        self.engine._acquire_sources.assert_called_once()
        self.assertTrue(self.coverage()['ready'])
        self.assertEqual(self.task['reasoning_state']['acquisition']['status'], 'complete')
        self.assertEqual(self.task['ledger']['supplements'], 0)
        self.assertEqual(fingerprint(self.task['data_need_specs']), before)
        payload = self.model.call_args.args[3]
        self.assertEqual(payload['source_readiness']['pending_original_question_ids'], ['nav'])
        self.assertNotIn('cmd_history_quotation', [tool['operation'] for tool in payload['data_request_contract']['tools']])

    def test_legacy_keeps_its_existing_ready_source_behavior(self):
        self.task.pop('initial_acquisition_scope_version')
        acquisition.complete_initial_acquisition(self.engine, self.task)
        self.model.assert_not_called()

    def test_price_only_does_not_force_original_or_extra_call(self):
        self.task['data_need_specs'] = []
        self.task['source_records'] = self.task['source_records'][:1]
        acquisition.complete_initial_acquisition(self.engine, self.task)
        self.model.assert_not_called()
        self.assertTrue(self.coverage()['ready'])

    def test_optional_or_query_provenance_directory_does_not_block_price(self):
        self.task['questions'][1]['required'] = False
        self.assertTrue(self.coverage()['ready'])
        self.task['questions'][1]['required'] = True
        self.task['questions'][1]['evidence_need_scopes'] = {'官方净值': 'query_provenance'}
        self.assertTrue(self.coverage()['ready'])

    def test_unrelated_or_foreign_original_never_covers_required_question(self):
        for change in (dict(question_ids=['price']), dict(task_id='foreign'), dict(research_version=99)):
            self.task['source_records'] = self.task['source_records'][:2] + [dict(self.original, **change)]
            self.assertFalse(self.coverage()['ready'])

    def test_correct_original_satisfies_source_readiness_not_evidence_approval(self):
        self.task['source_records'].append(copy.deepcopy(self.original))
        self.assertTrue(self.coverage()['ready'])
        self.assertFalse(self.coverage()['financial_approval'])
        acquisition.complete_initial_acquisition(self.engine, self.task)
        self.model.assert_not_called()

    def test_exhausted_budget_retains_existing_data_and_does_not_dispatch(self):
        self.engine._reasoning_available.return_value = False
        before = copy.deepcopy(self.task['source_records'])
        acquisition.complete_initial_acquisition(self.engine, self.task)
        self.model.assert_not_called()
        self.assertEqual(self.task['source_records'], before)
        self.assertEqual(self.task['reasoning_state']['acquisition']['status'], 'stopped')

    def test_original_dependency_replay_does_not_mutate_the_task_or_pdf(self):
        original = ETF_ORIGINAL.read_bytes()
        task = etf_task()
        task['source_records'] = [record for record in task['source_records'] if record['kind'] != 'filing']
        before = fingerprint(task)
        with patch.object(acquisition, 'extractable_source_views', side_effect=extractable_source_views):
            coverage = acquisition.initial_acquisition_coverage(task)
        self.assertFalse(coverage['ready'])
        self.assertEqual(coverage['pending_original_question_ids'], ['q1'])
        self.assertEqual(fingerprint(task), before)
        self.assertEqual(ETF_ORIGINAL.read_bytes(), original)

    def test_one_question_with_two_funds_requires_both_scoped_originals(self):
        self.task['data_need_specs'] = [dict(id='discover_' + code, source='ifind', operation='report_query',
            parameters={'codes': code}, question_ids=['nav']) for code in ('510300.SH', '510500.SH')]
        directories = [dict(id='dir_' + str(index), kind='api', source='ifind', operation='report_query',
            task_id=self.task['id'], research_version=1, parameters={'codes': code}, question_ids=['nav'])
            for index, code in enumerate(('510300.SH', '510500.SH'))]
        self.task['source_records'] = self.task['source_records'][:1] + directories + [dict(self.original,
            parameters={'catalog_id': 'dir_0', 'source_id': 'fund_a_report'})]
        missing = self.coverage()
        self.assertFalse(missing['ready'])
        self.assertEqual(missing['pending_original_scopes'], [dict(question_id='nav', source='ifind',
            kind='catalog', target='510500.SH')])
        self.task['source_records'].append(dict(self.original, id='fund_b_original',
            parameters={'catalog_id': 'dir_1', 'source_id': 'fund_b_report'}))
        self.assertTrue(self.coverage()['ready'])

    def test_explicit_original_selection_is_not_replaced_by_another_file_for_same_fund(self):
        self.task['data_need_specs'].append(dict(id='specific_original', source='ifind', operation='filing_original',
            parameters={'catalog_id': 'directory', 'source_id': 'selected_report'}, question_ids=['nav']))
        self.task['source_records'].append(dict(self.original,
            parameters={'catalog_id': 'directory', 'source_id': 'different_report'}))
        self.assertFalse(self.coverage()['ready'])
        self.task['source_records'].append(dict(self.original, id='selected_original',
            parameters={'catalog_id': 'directory', 'source_id': 'selected_report'}))
        self.assertTrue(self.coverage()['ready'])
