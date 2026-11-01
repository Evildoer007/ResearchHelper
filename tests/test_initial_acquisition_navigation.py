"""Offline navigation scheduling regressions; no provider or model transport."""
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from researchhelper import acquisition
from researchhelper.contracts import ContractError, DOCUMENT_CONTENT_INTENT, budget


class InitialAcquisitionNavigationTests(unittest.TestCase):
    def setUp(self):
        self.task = dict(id='navigation_test', version=1, batch=1, cutoff='2026-10-15',
            input_hash='input', question='研究贵州茅台半年报盈利质量', objects=[],
            intent='综合研究', run_mode='model', questions=[dict(id='q1', required=True, evidence_needs=['半年报盈利数据'])],
            ledger=budget(dict(data_requests=100, model_calls=8, seconds=600)),
            source_records=[], materials=[], data_need_specs=[], calculation_specs=[], gaps=[])
        self.stage = 0
        self.engine = SimpleNamespace(offline=False, model=object(),
            source_credentials={'ifind': {'refresh_token': 'synthetic-no-network'}},
            _active_supplement=Mock(return_value=None), _reasoning_available=Mock(return_value=True),
            _save_model_checkpoint=Mock(), _active=Mock(), _validated_data_needs=Mock(),
            _gap=lambda task, text, questions: task['gaps'].append(text),
            _acquire_sources=Mock(side_effect=self.acquire))
        patches = {
            'extractable_source_views': lambda task: [{'id': 'original'}] if self.stage == 3 else [],
            'navigation_progress': lambda task: tuple({str(index)} if self.stage >= index else set() for index in (1, 2, 3)),
            'data_request_contract': lambda task: {'callable_codes': [], 'tools': [
                {'source': 'ifind', 'operation': operation} for operation in
                ('get_thscode', 'report_query', 'filing_original', 'cmd_history_quotation')]},
            'question_specs': lambda task: copy.deepcopy(task['questions']),
            'context_sources': lambda task: {'catalog': []},
            'supplement_request_blocked': lambda *args: False,
        }
        for name, replacement in patches.items():
            guard = patch.object(acquisition, name, side_effect=replacement)
            guard.start(); self.addCleanup(guard.stop)
        self.validate = patch.object(acquisition, 'validate_plan').start()
        self.addCleanup(patch.stopall)
        self.decisions = [self.proposal(op) for op in ('get_thscode', 'report_query', 'filing_original')]
        self.model = patch.object(acquisition, 'model_object', side_effect=self.respond).start()
        guard = patch('socket.socket.connect', side_effect=AssertionError('No network permitted'))
        guard.start(); self.addCleanup(guard.stop)

    @staticmethod
    def proposal(operation):
        return {'data_requests': [dict(id=operation, source='ifind', operation=operation,
            parameters={'synthetic_scheduler_fixture': True}, question_ids=['q1'])], 'stop_reason': ''}

    def respond(self, *args):
        self.task['ledger']['used']['model_calls'] += 1
        return self.decisions.pop(0), {'synthetic': True}

    def acquire(self, task, needs):
        self.stage += 1
        task['source_records'].append({'id': needs[0]['id'], 'kind': 'api'})
        task['ledger']['used']['data_requests'] += 1

    def run_navigation(self):
        acquisition.complete_initial_acquisition(self.engine, self.task)

    def test_name_mapping_directory_original_share_budget_without_supplements(self):
        before_hash = self.task['input_hash']
        self.run_navigation()
        self.assertEqual(self.model.call_count, 3)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 3)
        self.assertEqual(self.task['ledger']['used']['data_requests'], 3)
        self.assertEqual(self.task['ledger']['supplements'], 0)
        self.assertNotIn('supplement_history', self.task)
        self.assertEqual(self.task['data_need_specs'], [])
        self.assertEqual(self.task['input_hash'], before_hash)
        self.assertEqual(self.task['reasoning_state']['acquisition']['status'], 'complete')
        self.assertEqual(self.validate.call_count, 3)
        self.assertEqual(self.engine._validated_data_needs.call_count, 3)

    def test_unregistered_operation_never_dispatches(self):
        self.decisions = [self.proposal('invented_operation')]
        self.run_navigation()
        self.engine._acquire_sources.assert_not_called()
        self.assertEqual(self.task['reasoning_state']['acquisition']['status'], 'stopped')

    def test_preflight_rejection_never_dispatches(self):
        self.engine._validated_data_needs.side_effect = ContractError('invalid identity or catalog')
        self.run_navigation()
        self.engine._acquire_sources.assert_not_called()

    def test_no_progress_stops_without_repeating_decision(self):
        self.engine._acquire_sources.side_effect = None
        self.run_navigation(); self.run_navigation()
        self.assertEqual(self.model.call_count, 1)

    def test_repeated_cached_request_stops_when_it_adds_no_navigation_state(self):
        self.decisions = [self.proposal('get_thscode')] * 3
        self.engine._acquire_sources.side_effect = lambda task, needs: setattr(self, 'stage', 1)
        self.run_navigation()
        self.assertEqual(self.model.call_count, 2)
        self.assertEqual(self.task['reasoning_state']['acquisition']['decisions'][-1]['progress'], [0, 0, 0])

    def test_global_budget_reserve_stops_before_model(self):
        self.engine._reasoning_available.return_value = False
        self.run_navigation()
        self.model.assert_not_called()

    def test_data_budget_stops_before_model(self):
        self.task['ledger']['used']['data_requests'] = 100
        self.run_navigation()
        self.model.assert_not_called()

    def test_cancelled_decision_is_not_reissued_on_resume(self):
        self.model.side_effect = ContractError('cancelled')
        self.engine._active.side_effect = ContractError('cancelled')
        with self.assertRaises(ContractError):
            self.run_navigation()
        self.engine._active.side_effect = None
        self.task['batch'] += 1
        self.run_navigation()
        self.assertEqual(self.model.call_count, 1)
        self.assertEqual(len(self.task['reasoning_state']['acquisition']['decisions']), 1)

    def test_existing_original_skips_navigation(self):
        self.stage = 3
        self.run_navigation()
        self.model.assert_not_called()

    def test_document_content_and_active_supplement_skip_navigation(self):
        self.task['intent'] = DOCUMENT_CONTENT_INTENT
        self.run_navigation()
        self.task['intent'] = '综合研究'
        self.engine._active_supplement.return_value = {'round': 1}
        self.run_navigation()
        self.model.assert_not_called()

    def test_changed_input_does_not_reuse_or_reset_decision_count(self):
        self.engine._acquire_sources.side_effect = None
        self.run_navigation()
        self.task['input_hash'] = 'new_input'
        self.run_navigation()
        self.assertEqual(self.model.call_count, 1)
        self.assertEqual(len(self.task['reasoning_state']['acquisition']['decisions']), 1)

    def test_permission_failure_stops_before_model(self):
        with patch.object(acquisition, 'supplement_request_blocked', return_value=True):
            self.run_navigation()
        self.model.assert_not_called()

    def test_payload_excludes_original_text_and_keeps_stable_scope(self):
        self.decisions = [{'data_requests': [], 'stop_reason': '没有适用来源'}]
        self.run_navigation()
        payload = self.model.call_args.args[3]
        self.assertNotIn('untrusted_sources', payload)
        self.assertEqual(payload['question'], self.task['question'])
        self.assertEqual(self.model.call_args.args[2], 'acquire')

    def test_general_definition_never_requests_navigation(self):
        self.task['questions'][0]['answer_scope'] = 'general_definition'
        self.run_navigation()
        self.model.assert_not_called()

    def test_question_without_source_or_method_needs_never_navigates(self):
        self.task['questions'][0]['evidence_needs'] = []
        self.run_navigation()
        self.model.assert_not_called()

    def test_existing_request_id_cannot_be_rebound(self):
        self.task['data_need_specs'] = [dict(self.proposal('report_query')['data_requests'][0], id='get_thscode')]
        self.run_navigation()
        self.engine._acquire_sources.assert_not_called()
        self.assertIn('标识已绑定', self.task['gaps'][-1])

    def test_registered_price_request_does_not_require_pdf(self):
        self.decisions = [self.proposal('cmd_history_quotation')]
        self.engine._acquire_sources.side_effect = lambda task, needs: setattr(self, 'stage', 3)
        self.run_navigation()
        self.assertEqual(self.model.call_count, 1)
        self.assertEqual(self.engine._acquire_sources.call_args.args[1][0]['operation'], 'cmd_history_quotation')


if __name__ == '__main__':
    unittest.main()
