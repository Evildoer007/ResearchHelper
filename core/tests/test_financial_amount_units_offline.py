"""Money-unit identity must not be inferred from substring matches."""
import copy
import unittest

from researchhelper.contracts import ContractError
from researchhelper.evidence import candidate_evidence
from tests import test_financial_metric_binding_offline as binding_fixture


class FinancialAmountUnitsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = binding_fixture.FinancialMetricBindingTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_inline_million_unit_cannot_be_relabelled_as_yuan_or_ten_thousand(self):
        task, original = self.fixture.original()
        valid = self.fixture.fixture.specs(original, "gross_margin_v1")[0]
        checked = candidate_evidence(task, original, valid)
        self.assertEqual(checked["raw_unit"], "百万元")
        for unit in ("元", "万元", "千元", "亿元"):
            with self.subTest(unit=unit), self.assertRaises(ContractError):
                proposal = copy.deepcopy(valid)
                proposal.update(raw_unit=unit, unit=unit)
                candidate_evidence(task, original, proposal)

    def test_table_numeric_cell_requires_the_exact_money_unit_header(self):
        task, original, valid, _ = self.fixture.table()
        self.assertEqual(candidate_evidence(task, original, valid)["unit"], "百万元")
        for unit in ("元", "万元"):
            with self.subTest(unit=unit), self.assertRaises(ContractError):
                proposal = copy.deepcopy(valid)
                proposal.update(raw_unit=unit, unit=unit)
                candidate_evidence(task, original, proposal)

    def test_unlabelled_amount_cannot_borrow_a_money_unit_from_an_unrelated_sentence(self):
        text = "600000.SH，2026年上半年，人民币，企业会计准则，合并利润表。营业收入100。其他资金支出1百万元。发布于2026-10-01。"
        task, original = self.fixture.original(text)
        proposal = self.fixture.material_spec(task, original)
        with self.assertRaises(ContractError):
            candidate_evidence(task, original, proposal)

    def test_inline_amount_can_use_a_single_explicit_unit_header(self):
        text = "600000.SH，2026年上半年，人民币，企业会计准则，合并利润表，单位：百万元。营业收入100。发布于2026-10-01。"
        task, original = self.fixture.original(text)
        proposal = self.fixture.material_spec(task, original)
        self.assertEqual(candidate_evidence(task, original, proposal)["value"], 100)

    def test_per_share_or_per_year_unit_is_not_a_statement_amount(self):
        for body in ("营业收入100元/股。", "单位：元/股。营业收入100。", "营业收入100元每年。"):
            with self.subTest(body=body), self.assertRaises(ContractError):
                text = "600000.SH，2026年上半年，人民币，企业会计准则，合并利润表。" + body + "发布于2026-10-01。"
                task, original = self.fixture.original(text)
                proposal = self.fixture.material_spec(task, original)
                proposal.update(raw_unit="元", unit="元")
                candidate_evidence(task, original, proposal)


if __name__ == "__main__":
    unittest.main()
