"""Offline contracts for raw daily iFinD volume and amount observations."""
import unittest

from tests import ROOT
from researchhelper.contracts import ContractError
from researchhelper.evidence import (
    api_calculation_context_contract,
    api_cell_candidates,
    snapshot_record,
    validate_market_request,
)


class DailyLiquidityTests(unittest.TestCase):
    def setUp(self):
        self.task = dict(id="daily-liquidity", version=1, objects=["600000.SH", "00700.HK"],
                         cutoff="2026-10-31T23:00:00+08:00", source_records=[])
        self.params = dict(codes="600000.SH,00700.HK", indicators="close,volume,amount",
                           startdate="2026-10-30", enddate="2026-10-30",
                           functionpara=dict(Currency="RMB", CPS="1", Fill="Omit"))

    def record(self, *columns, indicators=None):
        payload = {"errorcode": 0, "tables": [
            dict(thscode=code, time=["2026-10-30"], table=fields)
            for code, fields in zip(self.task["objects"], columns)]}
        params = dict(self.params, indicators=indicators or self.params["indicators"])
        record = snapshot_record(self.task, "ifind", "cmd_history_quotation", params, payload)
        self.assertEqual(record["snapshot"], payload)
        return record

    def test_request_accepts_registered_raw_fields_and_rejects_aliases(self):
        for indicators in ("volume", "amount", "close,volume,amount"):
            dates = validate_market_request(self.task, "ifind", "cmd_history_quotation",
                                            dict(self.params, indicators=indicators))
            self.assertEqual(dates, ["2026-10-30", "2026-10-30"])
        for indicators in ("vol", "turnover", "volume,volume", "amount,changeRatio"):
            with self.subTest(indicators=indicators), self.assertRaises(ContractError):
                validate_market_request(self.task, "ifind", "cmd_history_quotation",
                                        dict(self.params, indicators=indicators))

    def test_mixed_request_keeps_zero_null_and_missing_separate_per_table(self):
        record = self.record(dict(close=[10], volume=[0]), dict(close=[20], amount=[None]))
        self.assertEqual(record["missing_quote_columns"], [
            {"table": 0, "missing_fields": ["amount"]}, {"table": 1, "missing_fields": ["volume"]}])
        self.assertEqual(record["status"], "成功待核验")
        contract = api_calculation_context_contract(record)
        self.assertEqual(contract["registered_numeric_fields"], ["close"])
        self.assertEqual([cell["proposal_fields"]["record_locator"]["field"]
                          for cell in api_cell_candidates(record)["cells"]], ["close", "close"])

    def test_raw_only_columns_have_no_approved_unit_or_price_context(self):
        record = self.record(dict(volume=["123456789.0123"], amount=[0], close=[10]),
                             dict(volume=[0], amount=["9.00"]), indicators="volume,amount")
        self.assertNotIn("missing_quote_columns", record)
        self.assertEqual(api_calculation_context_contract(record)["registered_numeric_fields"], [])
        self.assertEqual(api_cell_candidates(record)["cells"], [])
