"""Offline negative fixtures for the independent live acquisition diagnostic."""
import unittest

from tests.probe_multitype_acquisition_current import inspect_series


class MatrixSeriesInspectionTests(unittest.TestCase):
    def setUp(self):
        self.params=dict(codes='600519.SH',indicators='close',startdate='2026-10-12',enddate='2026-10-16')

    def check_table(self,*,code='600519.SH',dates=None,values=None):
        return inspect_series(dict(snapshot=dict(tables=[dict(thscode=code,
            time=dates if dates is not None else ['2026-10-12'],
            table=dict(close=values if values is not None else [1500.0]))])),self.params)

    def test_observation_is_not_unit_or_calendar_approval(self):
        result=self.check_table()
        self.assertTrue(result['usable'])
        self.assertFalse(result['unit_approved'])
        self.assertFalse(result['continuous_trading_calendar_approved'])

    def test_wrong_security_is_not_success(self):
        self.assertFalse(self.check_table(code='600000.SH')['usable'])

    def test_missing_numeric_or_infinite_values_are_not_success(self):
        for value in (None,'1500',float('inf'),True):
            self.assertFalse(self.check_table(values=[value])['usable'])

    def test_duplicate_future_and_misaligned_dates_are_not_success(self):
        for dates,values in ((['2026-10-12','2026-10-12'],[1,2]),(['2026-10-19'],[1]),(['2026-10-12'],[1,2])):
            self.assertFalse(self.check_table(dates=dates,values=values)['usable'])

    def test_extra_or_missing_code_and_field_are_not_success(self):
        self.params['codes']='600519.SH,300750.SZ'
        self.assertFalse(self.check_table()['usable'])
        self.params['codes']='600519.SH'
        self.params['indicators']='close,open'
        self.assertFalse(self.check_table()['usable'])

    def test_zero_and_negative_are_not_silently_repaired(self):
        result=self.check_table(values=[0])
        self.assertTrue(result['usable'])
        self.assertEqual(result['tables'][0]['values']['close'],[0])
        self.assertTrue(self.check_table(values=[-1])['usable'])
