"""Synthetic calendar replies on isolated copies, never resumed research.

Retained price bytes are read-only fixtures. Appended dispatches below are
explicit test doubles; they do not establish real provider permission.
"""
import copy
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests import ROOT
from researchhelper import market_calendar as calendar
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, charge, fingerprint, now
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import (EvidenceCollector, snapshot_record, current_query_reference,
    _record_current_acquisition, data_request_contract)
from researchhelper.reasoning import extractable_source_views, analysis_contract
from researchhelper.output_schema import planning_output_schema
from researchhelper.planning import input_fingerprint
from researchhelper.reports import compose
from researchhelper.argument_support import review_request


SOURCE = ROOT / 'result/acceptance/one-sentence-research-ckhviipf/task.json'


class MarketCalendarTests(unittest.TestCase):
    def setUp(self):
        self.original = SOURCE.read_bytes()
        self.task = json.loads(self.original)
        self.task[calendar.POLICY] = 1
        self.price = next(item for item in self.task['source_records'] if item['operation'] == 'cmd_history_quotation')
        self.days = list(self.price['snapshot']['tables'][0]['time'])
        self.params = dict(marketcode='212001', functionpara=copy.deepcopy(calendar.FUNCTION),
            startdate=self.price['parameters']['startdate'], enddate=self.price['parameters']['enddate'])

    def tearDown(self):
        self.assertEqual(SOURCE.read_bytes(), self.original)

    def append_record(self, operation, params, payload):
        record = snapshot_record(self.task, 'ifind', operation, params, payload)
        event = dict(kind='data_requests', source='ifind', operation=operation,
            fingerprint=record['request_hash'], batch=self.task['batch'], status='成功',
            transport_isolated=True, network_request_started=True, network_worker_reaped=True,
            time=now(), synthetic_test_double=True)
        charge(self.task['ledger'], 'data_requests')
        self.task['ledger']['dispatches'].append(event)
        _record_current_acquisition(self.task, record, event)
        record.update(question_ids=['q1'], data_need_ids=['synthetic_calendar_fixture'])
        self.task['source_records'].append(record)
        return record

    def append_calendar(self, days=None, params=None):
        return self.append_record('get_trade_dates', params or self.params,
            dict(errorcode=0, tables=dict(time=self.days if days is None else days)))

    def price_need(self):
        return dict(id='price_scope', source='ifind', operation='cmd_history_quotation',
            parameters=copy.deepcopy(self.price['parameters']), question_ids=['q1'])

    def test_separate_calendar_matches_price_dates_without_financial_approval(self):
        source = self.append_calendar()
        result = calendar.coverage(self.task)
        self.assertEqual(len(result['panels']), 1)
        panel = result['panels'][0]
        self.assertEqual(panel['status'], '日历覆盖一致')
        self.assertEqual(panel['calendar_dates'], self.days)
        self.assertFalse(panel['financial_approval'])
        self.assertFalse(panel['historical_availability_approved'])
        self.assertEqual(panel['calendar_ref']['snapshot_hash'], source['hash'])

    def test_prices_alone_never_become_the_calendar(self):
        result = calendar.coverage(self.task)['panels'][0]
        self.assertEqual(result['status'], '日历未核验')
        self.assertIsNone(result['calendar_ref'])
        with self.assertRaises(ContractError):
            calendar.sessions(self.params, self.price['snapshot'])

    def test_missing_price_is_reported_without_guessing_suspension(self):
        payload = copy.deepcopy(self.price['snapshot'])
        missing = payload['tables'][0]['time'].pop(5)
        payload['tables'][0]['table']['close'].pop(5)
        self.task['source_records'] = [item for item in self.task['source_records'] if item['id'] != self.price['id']]
        self.append_record('cmd_history_quotation', self.price['parameters'], payload)
        self.append_calendar()
        panel = calendar.coverage(self.task)['panels'][0]
        self.assertEqual(panel['missing_dates'], [missing])
        self.assertEqual(panel['status'], '存在未观测交易日')
        self.assertNotIn('停牌', panel['status'])

    def test_null_price_is_not_covered_or_filled(self):
        payload = copy.deepcopy(self.price['snapshot']); payload['tables'][0]['table']['close'][3] = None
        self.task['source_records'] = [item for item in self.task['source_records'] if item['id'] != self.price['id']]
        source = self.append_record('cmd_history_quotation', self.price['parameters'], payload)
        self.append_calendar()
        self.assertEqual(calendar.coverage(self.task)['panels'][0]['missing_dates'], [self.days[3]])
        self.assertIsNone(source['snapshot']['tables'][0]['table']['close'][3])

    def test_changed_cross_task_or_unreaped_calendar_is_not_reused(self):
        source = self.append_calendar()
        for change in ('hash', 'task', 'unreaped'):
            task = copy.deepcopy(self.task)
            record = next(item for item in task['source_records'] if item['id'] == source['id'])
            if change == 'hash': record['hash'] = 'changed'
            elif change == 'task': record['acquisition']['task_id'] = 'foreign_task'
            else: task['ledger']['dispatches'][record['acquisition']['dispatch_index']]['network_worker_reaped'] = False
            self.assertEqual(calendar.coverage(task)['panels'][0]['status'], '日历未核验')

    def test_empty_date_column_differs_from_absent_column(self):
        self.assertTrue(calendar.sessions(self.params, dict(errorcode=0, tables=dict(time=[])))['explicit_empty'])
        for tables in ([], {}, dict(table={})):
            with self.assertRaises(ContractError): calendar.sessions(self.params, dict(errorcode=0, tables=tables))

    def test_wrong_market_dates_order_duplicates_and_coercion_are_rejected(self):
        invalid = [list(reversed(self.days)), self.days + [self.days[-1]], ['2026-08-03T00:00:00'],
            ['2026/08/03'], [' 2026-08-03'], ['2026-09-01'], [None], [True]]
        for days in invalid:
            with self.subTest(days=days[:2]), self.assertRaises(ContractError):
                calendar.sessions(self.params, dict(errorcode=0, tables=dict(time=days)))
        with self.assertRaises(ContractError):
            calendar.sessions(self.params, dict(errorcode=0, tables=dict(time=self.days), inputParams=dict(marketcode='212100')))

    def test_conflicting_date_columns_are_not_silently_selected(self):
        with self.assertRaises(ContractError):
            calendar.sessions(self.params, dict(errorcode=0,
                tables=dict(time=self.days, table=dict(sequencedate=self.days[:-1]))))

    def test_imported_snapshot_is_not_an_independent_acquisition(self):
        source = snapshot_record(self.task, 'ifind', 'get_trade_dates', self.params,
            dict(errorcode=0, tables=dict(time=self.days)))
        self.task['source_records'].append(source)
        self.assertIsNone(current_query_reference(self.task, source))
        self.assertEqual(calendar.coverage(self.task)['panels'][0]['status'], '日历未核验')

    def test_dependency_only_for_acquired_multi_day_prices_and_same_task_market(self):
        need = self.price_need()
        prepared = [(need, 'ifind', need['operation'], need['parameters'])]
        requests = calendar.dependency_requests(self.task, prepared)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]['parameters'], self.params)
        self.assertEqual(requests[0]['question_ids'], ['q1'])
        self.assertEqual(calendar.dependency_requests(self.task, []), [])
        self.assertEqual(calendar.dependency_requests(self.task,
            [(dict(need), 'ifind', 'date_sequence', need['parameters'])]), [])
        parameters = dict(need['parameters'], enddate=need['parameters']['startdate'])
        self.assertEqual(calendar.dependency_requests(self.task, [(need, 'ifind', need['operation'], parameters)]), [])
        self.append_calendar()
        self.assertEqual(calendar.dependency_requests(self.task, prepared), [])

    def test_scope_is_checked_before_credentials_or_request_charge(self):
        before = copy.deepcopy(self.task['ledger'])
        collector = EvidenceCollector(offline=False)
        with self.assertRaises(ContractError):
            collector.external(self.task, 'ifind', 'get_trade_dates', dict(self.params, marketcode='212100'), {})
        self.assertEqual(self.task['ledger'], before)

    def test_calendar_is_public_tool_but_not_numeric_extraction(self):
        source = self.append_calendar()
        tool = next(tool for tool in data_request_contract(self.task)['tools'] if tool['operation'] == 'get_trade_dates')
        self.assertTrue(tool['raw_only']); self.assertFalse(tool['numeric_fields'])
        self.assertNotIn(source['id'], [view['id'] for view in extractable_source_views(self.task)])
        self.assertIn('get_trade_dates', json.dumps(planning_output_schema(self.task)))

    def test_coverage_is_bound_to_analysis_and_review_but_not_legacy(self):
        before = analysis_contract(self.task)
        self.append_calendar()
        after = analysis_contract(self.task)
        self.assertNotEqual(before['execution']['market_coverage_hash'], after['execution']['market_coverage_hash'])
        self.assertEqual(after['market_coverage']['panels'][0]['status'], '日历覆盖一致')
        request = review_request(self.task, self.task['host_result'])
        self.assertEqual(request['scope']['market_coverage'], after['market_coverage'])
        legacy = dict(self.task); legacy.pop(calendar.POLICY)
        self.assertIsNone(calendar.coverage(legacy))
        self.assertNotIn('market_coverage', analysis_contract(legacy))

    def test_optional_automatic_failure_does_not_invent_or_block_endpoint_evidence(self):
        agent = ResearchAgent.__new__(ResearchAgent)
        agent.source_credentials = {}; agent.collector = SimpleNamespace(external=Mock(side_effect=ContractError('日历权限未通过')))
        agent._active = Mock(); agent._save_model_checkpoint = Mock(); agent._journal = Mock()
        need = calendar.dependency_requests(self.task, [(self.price_need(), 'ifind',
            'cmd_history_quotation', self.price['parameters'])])[0]
        before = len(self.task['gaps'])
        agent._acquire_sources(self.task, [need], metadata_dependency=True)
        self.assertEqual(len(self.task['gaps']), before)
        self.assertTrue(self.task['calendar_dependency_failures'])
        self.assertEqual(calendar.coverage(self.task)['panels'][0]['status'], '日历未核验')
        agent._acquire_sources(self.task, [need], metadata_dependency=True)
        agent.collector.external.assert_called_once()  # Failed request is not silently retried.

    def test_new_policy_flags_are_frozen_without_upgrading_old_tasks(self):
        with tempfile.TemporaryDirectory(prefix='calendar-policy-') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            task = agent.create('解释价格变化', questions=[dict(id='q1', kind='fact', question='解释价格变化',
                answer_scope='general_definition', knowledge_needs=['rh_k_price_return_measures'])])
            self.assertEqual(task[calendar.POLICY], 1)
            self.assertEqual(task['economic_identifier_namespace_version'], 2)
            legacy = dict(task); legacy.pop(calendar.POLICY)
            self.assertNotEqual(task['input_hash'], input_fingerprint(legacy))
            done = compose(agent.start(task['id']))
            self.assertEqual(done['market_coverage']['panels'], [])
            self.assertEqual(done[calendar.POLICY], 1)
            self.assertNotIn(calendar.POLICY, agent.create_case('mechanism'))


if __name__ == '__main__':
    unittest.main()
