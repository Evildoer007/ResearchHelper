"""Financial metric/amount association, using actual retained source bytes."""
import copy
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.evidence import candidate_evidence
from researchhelper.financial_sources import FINANCIAL_METRIC_BINDING_VERSION
from researchhelper.reasoning import evidence_contract, extractable_source_views
from tests import test_financial_filing_bridge_offline as bridge


class FinancialMetricBindingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = bridge.FinancialFilingBridgeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.agent = self.fixture.agent

    def original(self, text=None):
        if text is None:
            task, original, _ = self.fixture.retained_original()
            return task, original
        task = self.fixture.create()
        task = self.agent.add_material(task["id"], "financial.txt", text.encode())
        material = task["materials"][-1]
        # Reuse the same evidence grammar, but explicitly bind the real TXT
        # original rather than inventing a PDF or changing parser output.
        return task, material

    def material_spec(self, task, material, token="100"):
        text = material["snapshot"]["parts"][0]["text"]
        anchor = {"kind": "material", "artifact_id": material["id"], "snapshot_hash": material["snapshot_hash"],
            "part": "段落1", "quote": text}
        context = {"entity_id": "600000.SH", "currency": "CNY", "period": "2026年上半年",
            "accounting_scope": "consolidated", "accounting_standard": "CAS", "metric": "revenue"}
        return {"id": "e_revenue", "artifact_id": material["id"], "object": "600000.SH",
            "record_locator": {"part": "段落1"}, "text": text, "period": context["period"],
            "nature": "已实现事实", "unit": "百万元", "raw_unit": "百万元", "raw_token": token,
            "raw_value": float(token), "value": float(token), "conversion_factor": 1, "question_ids": ["q1"],
            "published_at": "2026-10-01", "publication": {"at": "2026-10-01", "precision": "日",
                "timezone": "Asia/Shanghai", "certainty": "已确认", "basis": "原件明确发布日", "basis_ref": anchor},
            "calculation_context": context, "calculation_context_basis": {key: copy.deepcopy(anchor) for key in context}}

    def test_cost_cannot_borrow_the_revenue_number_on_the_same_page(self):
        task, original = self.original()
        proposal = self.fixture.specs(original, "gross_margin_v1")[1]
        proposal.update(raw_token="100", raw_value=100, value=100)
        with self.assertRaises(ContractError):
            candidate_evidence(task, original, proposal)

    def test_other_statement_metrics_cannot_borrow_each_others_numbers(self):
        task, original = self.original()
        for method, field, value in (("gross_margin_v1", "revenue", 60),
                ("net_profit_margin_v1", "net_profit", 100),
                ("operating_cash_profit_ratio_v1", "operating_cash_flow", 12)):
            with self.subTest(field=field):
                proposal = next(item for item in self.fixture.specs(original, method) if item["id"] == "e_" + field)
                proposal.update(raw_token=str(value), raw_value=value, value=value)
                with self.assertRaises(ContractError):
                    candidate_evidence(task, original, proposal)

    def test_all_four_correct_inline_amounts_remain_source_checked(self):
        task, original = self.original()
        seen = set()
        for method in bridge.FINANCIAL_PERIOD_METHODS:
            for proposal in self.fixture.specs(original, method):
                if proposal["id"] in seen:
                    continue
                seen.add(proposal["id"])
                checked = candidate_evidence(task, original, proposal)
                self.assertEqual(checked["source_check"], "通过")
                self.assertEqual(checked["applicability_check"], "待核验")
                self.assertEqual(checked["raw_value"], proposal["raw_value"])
        self.assertEqual(len(seen), 4)

    def test_reports_name_the_original_amounts_and_keep_their_exact_source_links(self):
        from lxml import html
        from researchhelper.report_designer import design

        task, original = self.original()
        specs = {item["id"]: item for method in bridge.FINANCIAL_PERIOD_METHODS
                 for item in self.fixture.specs(original, method)}
        self.agent.offline = True
        result = self.agent.start(task["id"], {"evidence_specs": list(specs.values())})
        frozen = self.agent.archive.report(task["id"], task["version"])
        self.assertEqual(result["status"], "部分完成")
        expected = {"营业收入（合并）": 100, "营业成本（合并）": 60,
                    "净利润（合并）": 12, "经营活动产生的现金流量净额（合并）": -8}
        for profile in ("standard", "detailed"):
            with self.subTest(profile=profile):
                report = design(frozen["content"], self.agent.resources, profile=profile)
                data = report["document"]["dataset"]["rows"]
                self.assertEqual({row[1]: row[3] for row in data}, expected)
                self.assertTrue(all(row[:1] == ["600000.SH"] and row[2] == "2026年上半年"
                                    and row[4] == "百万元" for row in data))
                tree = html.document_fromstring(report["html"])
                paper_table, = tree.xpath('//main//table[contains(@class,"adopted-data-table")]')
                self.assertEqual([cell.text_content() for cell in paper_table.xpath('thead/tr/th')],
                                 ["对象与指标", "所属期", "原值"])
                rows = paper_table.xpath("tbody/tr")
                self.assertEqual(len(rows), 4)
                for row, (name, value), ref in zip(rows, expected.items(), specs):
                    self.assertIn(name, row.find("th").text_content())
                    self.assertIn(str(value) + "百万元", row.text_content())
                    link, = row.xpath('./td/a')
                    self.assertEqual(link.get("href"), "#ev-" + ref)
                    self.assertEqual(link.text_content(), str(value) + "百万元")
                    record = tree.get_element_by_id("ev-" + ref).text_content()
                    self.assertIn("第1页", record)
                    self.assertIn(specs[ref]["text"], record)
                self.assertFalse(tree.xpath('//main//*[@data-logic-detail="research-basis"]'))
        self.assertEqual(self.agent.archive.report(task["id"], task["version"]), frozen)

    def test_inline_pdf_cannot_borrow_a_scope_or_period_from_its_own_page(self):
        task, original, valid = self.fixture.retained_original((
            "2026年上半年合并利润表：营业收入100百万元。",
            "母公司利润表（2026年上半年）：营业收入90百万元。",
            "合并利润表（2025年上半年）：营业收入80百万元。",
        ))
        self.assertEqual(candidate_evidence(task, original, valid)["source_check"], "通过")
        for field, value in (("accounting_scope", "parent"), ("period", "2025年上半年")):
            with self.subTest(field=field), self.assertRaisesRegex(ContractError, "同一原文行"):
                proposal = copy.deepcopy(valid)
                proposal["calculation_context"][field] = value
                if field == "period":
                    proposal["period"] = value
                candidate_evidence(task, original, proposal)

    def test_inline_pdf_keeps_the_first_amount_and_rejects_amounts_after_a_period_change(self):
        task, original, _ = self.fixture.retained_original((
            "合并利润表（2026年上半年）：营业收入100百万元。上年同期。营业成本60百万元。",
        ))
        revenue, cost = self.fixture.specs(original, "gross_margin_v1")
        self.assertEqual(candidate_evidence(task, original, revenue)["source_check"], "通过")
        with self.assertRaisesRegex(ContractError, "同一原文行"):
            candidate_evidence(task, original, cost)

    def test_inline_pdf_rejects_currency_from_a_second_original(self):
        task, original, valid = self.fixture.retained_original()
        task = self.agent.add_material(task["id"], "other-statement.txt", "币种：人民币".encode())
        other = task["materials"][-1]
        valid["calculation_context_basis"]["currency"] = {"kind": "material", "artifact_id": other["id"],
            "snapshot_hash": other["snapshot_hash"], "part": "段落1", "quote": "币种：人民币"}
        with self.assertRaisesRegex(ContractError, "须来自同一原件"):
            candidate_evidence(task, original, valid)

    def test_inline_pdf_cannot_use_global_cny_when_statement_declares_another_currency(self):
        task, original, proposal = self.fixture.retained_original((
            "合并利润表（2026年上半年）：营业收入100百万元。币种：港币。",
        ))
        with self.assertRaisesRegex(ContractError, "币种"):
            candidate_evidence(task, original, proposal)

    def test_negative_amount_cannot_lose_its_sign(self):
        task, original = self.original()
        proposal = self.fixture.specs(original, "operating_cash_profit_ratio_v1")[0]
        proposal.update(raw_token="8", raw_value=8, value=8)
        with self.assertRaises(ContractError):
            candidate_evidence(task, original, proposal)

    def test_multiple_unbound_table_columns_are_not_silently_selected(self):
        text = "600000.SH，2026年上半年，人民币，企业会计准则，合并利润表，单位百万元。营业收入 100 80。发布于2026-10-01。"
        task, original = self.original(text)
        for value in ("100", "80"):
            with self.subTest(value=value), self.assertRaises(ContractError):
                candidate_evidence(task, original, self.material_spec(task, original, value))

    def test_percentage_cannot_use_an_unrelated_amount_unit_header(self):
        text = "600000.SH，2026年上半年，人民币，企业会计准则，合并利润表，单位百万元。营业收入100%。发布于2026-10-01。"
        task, original = self.original(text)
        with self.assertRaises(ContractError):
            candidate_evidence(task, original, self.material_spec(task, original))

    def table(self):
        header = "项目\t2026年上半年\t2025年上半年"
        revenue = "营业收入\t100\t80"
        cost = "营业成本\t60\t40"
        text = "600000.SH，2026年上半年，2025年上半年，人民币，企业会计准则，合并利润表，单位百万元。\n" + \
            "\n".join((header, revenue, cost, "发布于2026-10-01。"))
        task, original = self.original(text)
        proposal = self.material_spec(task, original)
        proposal["record_locator"]["financial_column"] = {"version": 1, "header_part": "段落1",
            "header_quote": header, "row_quote": revenue, "column_index": 1}
        return task, original, proposal, cost

    def test_period_bound_table_columns_reach_the_correct_frozen_margin(self):
        task, original, revenue, cost_row = self.table()
        cost = copy.deepcopy(revenue)
        cost.update(id="e_cost", raw_token="60", raw_value=60, value=60)
        cost["calculation_context"]["metric"] = "operating_cost"
        cost["record_locator"]["financial_column"]["row_quote"] = cost_row
        for proposal in (revenue, cost):
            checked = candidate_evidence(task, original, proposal)
            self.assertEqual(checked["source_check"], "通过")
            proposal["review"] = {"question_ids": ["q1"], "allowed_natures": ["已实现事实"],
                "conditions": ["仅同一主体2026年上半年合并口径"], "reason": "原件表头与金额列逐项核对"}
        self.agent.offline = True
        result = self.agent.start(task["id"], {"evidence_specs": [revenue, cost],
            "calculations": [self.fixture.calculation("gross_margin_v1")],
            "answers": {"q1": {"answer": "只核算原件同期间指标，不推断投资收益。",
                "refs": ["e_revenue", "e_cost"], "calculation_refs": ["financial"]}}})
        self.assertEqual(result["status"], "已完成", result.get("error") or result["gaps"])
        self.assertEqual(result["calculations"][0]["output"]["value"], 40)
        self.assertEqual(self.agent.archive.report(result["id"], result["version"])["content"]["calculations"],
            result["calculations"])

    def test_table_column_cannot_change_period_value_or_original_structure(self):
        task, original, valid, _ = self.table()
        for mutation in ("old_period", "wrong_value", "boolean_index", "out_of_bounds", "forged_header",
                "missing_column", "rewritten_row", "wrong_label", "percent", "extra_field"):
            with self.subTest(mutation=mutation), self.assertRaises(ContractError):
                proposal = copy.deepcopy(valid)
                column = proposal["record_locator"]["financial_column"]
                if mutation == "old_period":
                    column["column_index"] = 2
                    proposal.update(raw_token="80", raw_value=80, value=80)
                elif mutation == "wrong_value":
                    proposal.update(raw_token="80", raw_value=80, value=80)
                elif mutation == "boolean_index":
                    column["column_index"] = True
                elif mutation == "out_of_bounds":
                    column["column_index"] = 3
                elif mutation == "forged_header":
                    column["header_quote"] = "项目\t2025年上半年\t2026年上半年"
                elif mutation == "missing_column":
                    column["row_quote"] = "营业收入\t100"
                elif mutation == "rewritten_row":
                    column["row_quote"] = "营业收入\t80\t100"
                elif mutation == "wrong_label":
                    column["row_quote"] = "营业成本\t60\t40"
                    proposal.update(raw_token="60", raw_value=60, value=60)
                elif mutation == "percent":
                    column["row_quote"] = "营业收入\t100%\t80%"
                else:
                    column["approved"] = True
                candidate_evidence(task, original, proposal)

    def test_unregistered_space_aligned_layout_cannot_forge_tabular_cells(self):
        task, original, valid, _ = self.table()
        proposal = copy.deepcopy(valid)
        column = proposal["record_locator"]["financial_column"]
        column["header_quote"] = column["header_quote"].replace("\t", " ")
        column["row_quote"] = column["row_quote"].replace("\t", " ")
        with self.assertRaises(ContractError):
            candidate_evidence(task, original, proposal)

    def test_metric_anchor_cannot_come_from_another_value_original(self):
        task, original = self.original()
        proposal = self.fixture.specs(original, "gross_margin_v1")[0]
        updated = self.agent.add_material(task["id"], "label.txt", "营业收入100百万元".encode())
        label = updated["materials"][-1]
        proposal["calculation_context_basis"]["metric"] = {"kind": "material", "artifact_id": label["id"],
            "snapshot_hash": label["snapshot_hash"], "part": "段落1", "quote": "营业收入100百万元"}
        with self.assertRaises(ContractError):
            candidate_evidence(updated, original, proposal)

    def test_wrong_cost_cannot_reach_a_saved_financial_calculation(self):
        task, original = self.original()
        proposals = self.fixture.specs(original, "gross_margin_v1")
        proposals[1].update(raw_token="100", raw_value=100, value=100)
        host = {"evidence_specs": proposals, "calculations": [self.fixture.calculation("gross_margin_v1")],
            "answers": {"q1": {"answer": "不能用收入数字代替成本。", "refs": ["e_revenue", "e_cost"],
                "calculation_refs": ["financial"]}}}
        self.agent.offline = True
        result = self.agent.start(task["id"], host)
        self.assertEqual(result["status"], "部分完成", result.get("error"))
        self.assertEqual(result["calculations"], [])
        self.assertTrue(result["rejected_evidence"])
        self.assertNotEqual(result["questions"][0]["status"], "已回答")
        saved = self.agent.archive.report(result["id"], result["version"])
        self.assertEqual(saved["content"]["calculations"], [])
        self.assertEqual(result["ledger"]["used"]["data_requests"], 0)
        self.assertEqual(result["ledger"]["used"]["model_calls"], 0)

    def test_app_and_skill_extraction_contract_names_the_actual_binding_policy(self):
        task, _ = self.original()
        contract = evidence_contract(task)["material_numeric_contract"]["financial_metric_binding"]
        self.assertEqual(contract["version"], FINANCIAL_METRIC_BINDING_VERSION)
        self.assertEqual(set(contract["metric_labels"]), {"revenue", "operating_cost", "net_profit", "operating_cash_flow"})
        self.assertTrue(contract["same_value_original_required"] and contract["signed_raw_token_required"])

    def test_unfinished_financial_checkpoint_is_invalidated_once_without_resetting_budget(self):
        task, _ = self.original()
        views = extractable_source_views(task)
        task["host_result"] = {}
        old_input = {"input_hash": task["input_hash"], "sources": views, "reader_version": 1}
        old_digest = fingerprint(old_input)
        task["reasoning_state"] = {"extraction": {"input_digest": old_digest}}
        before = copy.deepcopy(task["ledger"])
        with patch.object(self.agent, "_reasoning_available", return_value=True), \
                patch.object(self.agent, "_save_model_checkpoint"), \
                patch("researchhelper.engine.model_object", return_value=({"evidence_specs": []}, {"synthetic": True})) as extract:
            self.agent._extract_model_sources(task)
            current_digest = task["reasoning_state"]["extraction"]["input_digest"]
            self.agent._extract_model_sources(task)
            extract.assert_called_once()
            with patch("researchhelper.engine.FINANCIAL_METRIC_BINDING_VERSION", FINANCIAL_METRIC_BINDING_VERSION + 1):
                self.agent._extract_model_sources(task)
                upgraded_digest = task["reasoning_state"]["extraction"]["input_digest"]
                self.agent._extract_model_sources(task)
            self.assertEqual(extract.call_count, 2)
        self.assertEqual(task["ledger"], before)
        self.assertNotEqual(current_digest, old_digest)
        self.assertNotEqual(upgraded_digest, current_digest)
        self.assertEqual(task["reasoning_state"]["extraction"]["input_digest"], upgraded_digest)


if __name__ == "__main__":
    unittest.main()
