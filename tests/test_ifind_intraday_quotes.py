"""Offline, synthetic iFinD snapshots exercise request and cell boundaries."""
import unittest

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.intraday_quotes import (
    MINUTE, REALTIME, minute_parameters, quote_observations, realtime_parameters, validate_parameters,
)

CODE = '600030.SH'
START = '2026-11-24 09:30:00'
END = '2026-11-24 09:32:00'


def record(operation=REALTIME, *, times=None, prices=None, code=CODE, error=0):
    params = realtime_parameters(CODE) if operation == REALTIME else minute_parameters(CODE, START, END)
    table = dict(thscode=code, table={params['indicators']: [20.5] if prices is None else prices},
                 time=[START] if times is None else times)
    snapshot = dict(errorcode=error, tables=[table])
    request = dict(source='ifind', operation=operation, parameters=params)
    return dict(**request, snapshot=snapshot, hash=fingerprint(snapshot), request_hash=fingerprint(request),
                obtained_at='2026-11-24T10:00:00+08:00')


class IntradayQuotesTests(unittest.TestCase):
    def test_single_explicit_security(self):
        for code in ('300033.SZ', '00700.HK', CODE):
            self.assertEqual(realtime_parameters(code), dict(codes=code, indicators='latest'))
        for code in (None, '600030', '0700.HK', '600030.SH,300033.SZ', '*', '６０００３０.SH'):
            with self.subTest(code=code), self.assertRaises(ContractError):
                realtime_parameters(code)

    def test_minute_settings_and_window(self):
        params = minute_parameters(CODE, START, END)
        self.assertEqual(params['functionpara'], dict(Interval='1', Fill='Original', CPS='no', Timeformat='LocalTime'))
        self.assertEqual(validate_parameters(MINUTE, params), params)
        self.assertEqual(minute_parameters(CODE, START, '2026-11-24 15:00:00')['starttime'], START)
        for end in ('2026-11-25 09:30:00', '2026-11-24 09:29:59', '09:32', None):
            with self.subTest(end=end), self.assertRaises(ContractError):
                minute_parameters(CODE, START, end)
        for settings in ({}, dict(Fill='Previous'), dict(params['functionpara'], Interval='5')):
            with self.assertRaises(ContractError):
                validate_parameters(MINUTE, dict(params, functionpara=settings))
        with self.assertRaises(ContractError):
            validate_parameters(REALTIME, dict(realtime_parameters(CODE), extra='x'))

    def test_supplier_time_only_and_exact_cell(self):
        self.assertEqual(quote_observations(record()), [dict(code=CODE, field='latest', value=20.5,
            quote_time=START, record_locator=dict(table=0, row=0, field='latest'))])
        for times in ([], [None], ['09:30:00'], ['2026-11-24'], ['2026-02-30 09:30:00'],
                      ['9999-12-31T23:59:59Z'], [START, END]):
            with self.subTest(times=times):
                self.assertEqual(quote_observations(record(times=times)), [])

    def test_unusable_values_and_mismatched_response(self):
        for price in (False, None, 'null', 'NaN', 'Infinity', [], 0):
            self.assertEqual(quote_observations(record(prices=[price])), [])
        for item in (record(code='300033.SZ'), record(error=-4001), record(error=False),
                     dict(record(), hash='wrong'), dict(record(), request_hash='wrong')):
            self.assertEqual(quote_observations(item), [])

    def test_sparse_unfilled_minutes_retain_original_locator(self):
        rows = quote_observations(record(MINUTE, times=[START, END], prices=[None, '20.50']))
        self.assertEqual(rows, [dict(code=CODE, field='close', value='20.50', quote_time=END,
                                    record_locator=dict(table=0, row=1, field='close'))])
        rows = quote_observations(record(MINUTE, times=['2026-11-24T01:30Z'], prices=[20]))
        self.assertEqual(rows[0]['quote_time'], '2026-11-24T01:30Z')
        for times in ([START, START], [START, '2026-11-24 09:33:00'], [START, END+'-05:00'],
                      [START, '2026-11-24 09:31:10']):
            self.assertEqual(quote_observations(record(MINUTE, times=times, prices=[20, 21])), [])
