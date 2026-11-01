"""Equivalent cutoff instants must bind the same Shanghai market calendar."""
import copy
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError
from researchhelper.evidence import data_request_contract, validate_market_request
from researchhelper.financial_sources import validate_financial_request, validate_financial_snapshot
from tests.test_market_dispatch_scope_offline import IFIND
from tests import test_market_dispatch_scope_offline as dispatch_fixtures

# Same instant: Shanghai 2026-10-02 18:30. The +14 representation has a different date.
CUTOFFS = ('2026-10-02T18:30:00+08:00', '2026-10-02T10:30:00Z',
           '2026-10-03T00:30:00+14:00', '2026-10-02T00:30:00-10:00')


class MarketCalendarTests(unittest.TestCase):
    def task(self, cutoff=CUTOFFS[0]):
        return {'id': 'calendar-fixture', 'version': 1, 'input_hash': 'synthetic',
                'cutoff': cutoff, 'objects': ['600000.SH'], 'question': '核对指定原值',
                'questions': [{'id': 'q1', 'question': '核对指定原值'}], 'source_records': []}

    def financial(self, end='20261002'):
        return {'ts_code': '600000.SH', 'fields': 'ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,update_flag,revenue',
                'start_date': '20261001', 'end_date': end, 'period': '20260630', 'report_type': '1'}

    def test_equivalent_instants_allow_same_ifind_day(self):
        params = dict(IFIND, startdate='2026-10-02', enddate='2026-10-02')
        for cutoff in CUTOFFS:
            with self.subTest(cutoff=cutoff):
                self.assertEqual(validate_market_request(self.task(cutoff), 'ifind', 'cmd_history_quotation', params), ['2026-10-02'] * 2)

    def test_equivalent_instants_reject_future_ifind_day(self):
        params = dict(IFIND, startdate='2026-10-03', enddate='2026-10-03')
        for cutoff in CUTOFFS:
            with self.subTest(cutoff=cutoff), self.assertRaises(ContractError):
                validate_market_request(self.task(cutoff), 'ifind', 'cmd_history_quotation', params)

    def test_equivalent_instants_reject_future_tinyshare_day(self):
        for cutoff in CUTOFFS:
            for operation in ('daily', 'fund_daily', 'daily_basic'):
                params = {'ts_code': '600000.SH', 'start_date': '20261003', 'end_date': '20261003',
                          'fields': 'ts_code,trade_date,' + ('pe_ttm' if operation == 'daily_basic' else 'close')}
                with self.subTest(cutoff=cutoff, operation=operation), self.assertRaises(ContractError):
                    validate_market_request(self.task(cutoff), 'tinyshare', operation, params)

    def test_utc_previous_day_allows_shanghai_current_day(self):
        task = self.task('2026-10-01T16:30:00Z')  # Shanghai October 2, 00:30.
        params = {'ts_code': '600000.SH', 'start_date': '20261002', 'end_date': '20261002', 'fields': 'ts_code,trade_date,close'}
        self.assertEqual(validate_market_request(task, 'tinyshare', 'daily', params), ['2026-10-02'] * 2)
        self.assertEqual(task['cutoff'], '2026-10-01T16:30:00Z')

    def test_planner_examples_use_same_market_day_without_changing_task(self):
        for cutoff in CUTOFFS:
            task = self.task(cutoff); original = copy.deepcopy(task)
            contract = data_request_contract(task)
            examples = [tool['example'] for tool in contract['tools'] if tool.get('example')]
            self.assertTrue(examples)
            for example in examples:
                params = example['parameters']
                for key in ('startdate', 'enddate', 'beginrDate', 'endrDate'):
                    if key in params:
                        self.assertEqual(params[key], '2026-10-02')
                for key in ('start_date', 'end_date'):
                    if key in params:
                        self.assertEqual(params[key], '20261002')
            self.assertEqual(task, original)

    def test_financial_request_uses_market_day_for_all_vintages(self):
        for cutoff in CUTOFFS:
            self.assertEqual(validate_financial_request(self.task(cutoff), 'income', self.financial()), ['2026-10-01', '2026-10-02'])
            with self.subTest(cutoff=cutoff), self.assertRaises(ContractError):
                validate_financial_request(self.task(cutoff), 'income', self.financial('20261003'))
        self.assertEqual(validate_financial_request(self.task('2026-10-01T16:30:00Z'), 'income', self.financial()), ['2026-10-01', '2026-10-02'])

    def test_financial_response_rejects_next_market_day_publication(self):
        row = {'ts_code': '600000.SH', 'ann_date': '20261002', 'f_ann_date': '20261003',
               'end_date': '20260630', 'report_type': '1', 'comp_type': '1', 'update_flag': '0', 'revenue': 123}
        for cutoff in CUTOFFS:
            with self.subTest(cutoff=cutoff), self.assertRaises(ContractError):
                validate_financial_snapshot(self.task(cutoff), 'income', self.financial(), {'code': 0, 'data': [row]})

    def test_calendar_future_rejected_before_credentials_or_dispatch(self):
        # Use the existing fixture helper without inheriting and duplicating its test suite.
        case = dispatch_fixtures.MarketDispatchScopeTests();case.setUp()
        try:
            task = copy.deepcopy(case.task);task['cutoff'] = CUTOFFS[2]
            params = dict(IFIND, startdate='2026-10-03', enddate='2026-10-03')
            with patch.object(case.collector, '_http_json', side_effect=AssertionError('Must not dispatch')) as wire:
                with self.assertRaises(ContractError):
                    case.collector.external(task, 'ifind', 'cmd_history_quotation', params, {'refresh_token': 'SYNTHETIC_ONLY'})
                self.assertFalse(wire.called)
                self.assertEqual(task['ledger']['used']['data_requests'], 0)
        finally:
            case.tearDown()
            case.doCleanups()


if __name__ == '__main__':
    unittest.main()
