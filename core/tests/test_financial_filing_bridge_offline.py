"""Financial originals through the actual kernel, with fabricated transports.

No real supplier or model is contacted. PDFs are generated in memory, parsed
without overrides, and bound to a retained iFinD-style directory snapshot.
"""
import copy
import io
import tempfile
import unittest
from decimal import Decimal
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject,
    NameObject, NumberObject, TextStringObject)

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import (ContractError, FINANCIAL_PERIOD_METHODS,
    METHOD_REGISTRY, charge, fingerprint)
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import (candidate_evidence, data_request_contract,
    filing_record, snapshot_record, validate_filing_original_request,
    validate_ifind_report_request)
from researchhelper.model import ModelService, ModelSettings
from researchhelper.planning import question_specs
from tests.test_autonomous_reasoning_offline import FabricatedModel
from tests import test_filing_workflow_offline as filing_fixture


class FinancialFilingBridgeTests(unittest.TestCase):
    def setUp(self):
        fixture = filing_fixture.FilingWorkflowTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.agent = fixture.agent
        settings = ModelSettings(self.agent.archive.root)
        settings.save({"endpoint": "https://example.invalid/model", "model": "fabricated-fixture-model",
            "api_key": "FAKE_MODEL_KEY"})
        self.agent.model = ModelService(settings, offline=False, journal=self.agent._journal)
        network = patch("socket.socket.connect", side_effect=AssertionError("Real network forbidden"))
        network.start()
        self.addCleanup(network.stop)
        self.dispatches = []

    def create(self, method="gross_margin_v1"):
        question = "核算600000.SH披露的2026年上半年" + METHOD_REGISTRY[method]["title"] + "，不推断投资收益"
        return self.agent.create(question, entry="个股", objects=["600000.SH"],
            cutoff="2026-10-02T18:00:00+08:00", questions=[{"id": "q1", "question": question,
                "kind": "fact", "method_needs": [method], "evidence_needs": []}])

    @staticmethod
    def pdf(statement_rows=None):
        if statement_rows is None:
            statement_rows = (
                "合并利润表（2026年上半年）：营业收入100百万元。营业成本60百万元。净利润12百万元。",
                "合并现金流量表（2026年上半年）：经营活动产生的现金流量净额-8百万元。",
            )
        rows = ("合成财务原件，非真实公司数据。600000.SH。",
                "本财务报表以人民币列报。本财务报表按照企业会计准则编制。", *statement_rows)
        # Explicit Unicode mappings keep the test independent of host fonts,
        # report generators and optional dependencies in the frozen App.
        writer = PdfWriter()
        page = writer.add_blank_page(width=595, height=842)
        mapping = DecodedStreamObject()
        characters = sorted(set("".join(rows)))
        entries = "\n".join(f"<{ord(character):04X}> <{ord(character):04X}>" for character in characters)
        mapping.set_data(("/CIDInit /ProcSet findresource begin 12 dict begin begincmap "
            "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def "
            "/CMapName /FinancialFixture def /CMapType 2 def "
            "1 begincodespacerange <0000> <FFFF> endcodespacerange\n"
            f"{len(characters)} beginbfchar\n{entries}\nendbfchar\n"
            "endcmap CMapName currentdict /CMap defineresource pop end end").encode())
        descendant = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/CIDFontType0"), NameObject("/BaseFont"): NameObject("/STSong-Light"),
            NameObject("/CIDSystemInfo"): DictionaryObject({NameObject("/Registry"): TextStringObject("Adobe"),
                NameObject("/Ordering"): TextStringObject("GB1"), NameObject("/Supplement"): NumberObject(4)})})
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type0"),
            NameObject("/BaseFont"): NameObject("/STSong-Light"), NameObject("/Encoding"): NameObject("/UniGB-UCS2-H"),
            NameObject("/DescendantFonts"): ArrayObject([writer._add_object(descendant)]),
            NameObject("/ToUnicode"): writer._add_object(mapping)})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):
            DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        content = DecodedStreamObject()
        commands = [f"BT /F1 12 Tf 24 {780 - index * 30} Td <{text.encode('utf-16-be').hex()}> Tj ET"
            for index, text in enumerate(rows)]
        content.set_data("\n".join(commands).encode())
        page[NameObject("/Contents")] = writer._add_object(content)
        output = io.BytesIO()
        writer.write(output)
        return output.getvalue()

    @staticmethod
    def directory_request():
        return {"id": "financial_directory", "source": "ifind", "operation": "report_query",
            "parameters": copy.deepcopy(filing_fixture.PARAMETERS), "question_ids": ["q1"],
            "selection_reason": "优先iFinD取得财务披露目录，数字仍须定位原件"}

    def transport(self, task, source, operation, parameters, credentials):
        self.assertEqual(source, "ifind")
        if operation == "report_query":
            validate_ifind_report_request(task, parameters)
            payload = copy.deepcopy(filing_fixture.RESPONSE)
            payload["tables"][0]["reportTitle"] = "合成半年财务报告，不是真实数据"
            record = snapshot_record(task, source, operation, parameters, payload)
        elif operation == "filing_original":
            directory = validate_filing_original_request(task, parameters)
            record = filing_record(task, directory, "financial.pdf", self.pdf(), task["source_records"])
            record.update(operation=operation, parameters=copy.deepcopy(parameters), status="成功待核验",
                request_hash=fingerprint({"source": source, "operation": operation, "parameters": parameters}))
        else:
            raise AssertionError("Unexpected supplier operation: " + operation)
        charge(task["ledger"], "data_requests")
        task["ledger"]["dispatches"].append({"kind": "data_requests", "fingerprint": record["request_hash"],
            "batch": task["batch"], "attempt": 1, "status": "成功"})
        self.agent._journal(task)
        self.dispatches.append(operation)
        return record

    @staticmethod
    def calculation(method):
        return {"id": "financial", "method": method,
            "inputs": {key: "e_" + key for key in METHOD_REGISTRY[method]["input_units"]},
            "parameters": dict(METHOD_REGISTRY[method]["parameters"], period="2026年上半年",
                accounting_scope="consolidated", accounting_standard="CAS"),
            "assumptions": {}, "question_ids": ["q1"]}

    @staticmethod
    def specs(record, method):
        text = record["snapshot"]["material"]["parts"][0]["text"]
        values = {"revenue": (100, "revenue"), "cost": (60, "operating_cost"),
            "net_profit": (12, "net_profit"), "operating_cash_flow": (-8, "operating_cash_flow")}
        anchor = {"kind": "material", "artifact_id": record["id"], "snapshot_hash": record["hash"],
            "part": "第1页", "quote": text}
        results = []
        for field in METHOD_REGISTRY[method]["input_units"]:
            value, metric = values[field]
            context = {"entity_id": "600000.SH", "currency": "CNY", "period": "2026年上半年",
                "accounting_scope": "consolidated", "accounting_standard": "CAS", "metric": metric}
            results.append({"id": "e_" + field, "artifact_id": record["id"], "object": "600000.SH",
                "record_locator": {"part": "第1页"}, "text": text, "period": "2026年上半年",
                "nature": "已实现事实", "unit": "百万元", "raw_unit": "百万元", "raw_token": str(value),
                "raw_value": value, "value": value, "conversion_factor": 1, "question_ids": ["q1"],
                "published_at": record["directory"]["published_at"],
                "publication": {"at": record["directory"]["published_at"], "precision": "秒",
                    "timezone": "Asia/Shanghai", "certainty": "已确认", "basis": "合成目录的明确发布时间",
                    "basis_ref": {"kind": "directory", "artifact_id": record["id"],
                        "snapshot_hash": record["hash"], "field": "published_at"}},
                "calculation_context": context,
                "calculation_context_basis": {key: copy.deepcopy(anchor) for key in context},
                "review": {"question_ids": ["q1"], "allowed_natures": ["已实现事实"],
                    "conditions": ["只核算原件同主体、期间、币种和报表口径，不形成买卖判断"],
                    "reason": "逐页核对原值与口径，目录不代替正文"}})
        return results

    def test_registered_financial_method_alone_enables_late_source_collection(self):
        for method in FINANCIAL_PERIOD_METHODS:
            with self.subTest(method=method):
                task = self.create(method)
                before = copy.deepcopy(task)
                options = self.agent._supplement_options(task)
                self.assertEqual(options["sources"], ["ifind"])
                self.assertEqual(options["source_question_ids"], {"ifind": ["q1"]})
                self.assertEqual(task, before)

    def test_completed_unknown_and_optional_methods_do_not_create_source_actions(self):
        for variant in ("completed", "optional"):
            with self.subTest(variant=variant):
                task = self.create()
                if variant == "completed":
                    task["calculations"] = [{"method": "gross_margin_v1", "question_ids": ["q1"]}]
                else:
                    task["questions"][0]["required"] = False
                self.assertEqual(self.agent._supplement_options(task)["sources"], [])
        with self.assertRaises(ContractError):
            self.agent.create("核算财务指标", objects=["600000.SH"], questions=[{
                "question": "核算财务指标", "kind": "fact", "method_needs": ["made_up_method"]}])

    def test_offline_credentials_and_budget_guards_still_apply(self):
        task = self.create()
        for variant in ("offline", "credentials", "budget"):
            with self.subTest(variant=variant):
                agent = ResearchAgent(self.agent.archive, self.agent.resources,
                    offline=variant == "offline", source_credentials={} if variant == "credentials"
                    else {"ifind": {"access_token": "FAKE_IFIND_TOKEN"}})
                candidate = copy.deepcopy(task)
                if variant == "budget":
                    candidate["ledger"]["used"]["data_requests"] = candidate["ledger"]["limits"]["data_requests"]
                before = copy.deepcopy(candidate)
                self.assertEqual(agent._supplement_options(candidate)["sources"], [])
                self.assertEqual(candidate, before)

    def test_shared_contract_prefers_originals_and_does_not_register_unknown_fields(self):
        task = self.create()
        before = copy.deepcopy(task)
        route = data_request_contract(task)["financial_evidence_path"]
        self.assertEqual(route["method_ids"], list(FINANCIAL_PERIOD_METHODS))
        self.assertEqual(route["acquisition_priority"], ["ifind", "tinyshare"])
        self.assertEqual(route["original_operations"], ["report_query", "filing_original"])
        self.assertFalse(route["raw_api_numeric_evidence_registered"])
        self.assertEqual(task, before)
        self.assertNotIn("basic_data_service", [tool["operation"] for tool in data_request_contract(task)["tools"]])

    def test_host_skill_and_app_expose_identical_financial_route(self):
        from core.session import Session

        with tempfile.TemporaryDirectory(prefix="rh-independent-skill-") as project:
            session = Session(project)
            task = session.create("核算600000.SH毛利率", objects=["600000.SH"],
                cutoff="2026-10-02T18:00:00+08:00")
            before = session.status(task["id"])
            self.assertEqual(session.tools(task["id"])["data_requests"], data_request_contract(task))
            self.assertEqual(session.status(task["id"]), before)

    def retained_original(self, statement_rows=None):
        task = self.create()
        request = self.directory_request()
        task = self.agent.add_snapshot(task["id"], "ifind", "report_query", request["parameters"],
            copy.deepcopy(filing_fixture.RESPONSE))
        directory = validate_filing_original_request(task, {"catalog_id": task["source_records"][0]["id"],
            "source_id": "10001"})
        task = self.agent.add_filing(task["id"], directory, "financial.pdf", self.pdf(statement_rows))
        original = next(record for record in task["source_records"] if record["kind"] == "filing")
        return task, original, self.specs(original, "gross_margin_v1")[0]

    def test_wrong_context_unit_publication_and_page_never_create_financial_evidence(self):
        task, original, valid = self.retained_original()
        candidate = candidate_evidence(task, original, valid)
        self.assertEqual(candidate["source_check"], "通过")
        self.assertEqual(candidate["applicability_check"], "待核验")
        for variant in ("period", "currency", "scope", "unit", "publication", "page"):
            with self.subTest(variant=variant), self.assertRaises(ContractError):
                proposal = copy.deepcopy(valid)
                if variant == "period":
                    proposal["calculation_context"]["period"] = "2025年上半年"
                elif variant == "currency":
                    proposal["calculation_context"]["currency"] = "HKD"
                elif variant == "scope":
                    proposal["calculation_context"]["accounting_scope"] = "parent"
                elif variant == "unit":
                    proposal.update(raw_unit="万元", conversion_factor=.01, value=1)
                elif variant == "publication":
                    proposal["published_at"] = "2026-10-03T18:00:00+08:00"
                    proposal["publication"]["at"] = proposal["published_at"]
                else:
                    proposal["record_locator"]["part"] = "第2页"
                candidate_evidence(task, original, proposal)
        self.assertEqual(task["calculations"], [])
        self.assertEqual(task["ledger"]["used"]["data_requests"], 0)

    def test_cross_task_and_tampered_original_are_rejected(self):
        task, original, proposal = self.retained_original()
        for variant in ("cross_task", "original_bytes"):
            with self.subTest(variant=variant), self.assertRaises(ContractError):
                current = copy.deepcopy(task)
                record = copy.deepcopy(original)
                if variant == "cross_task":
                    current["id"] = "rh_foreign_financial_task"
                else:
                    record["snapshot"]["material"]["original_base64"] = "c3Bvb2ZlZA=="
                candidate_evidence(current, record, proposal)

    def test_future_directory_cannot_supply_an_original_selection(self):
        task = self.create()
        payload = copy.deepcopy(filing_fixture.RESPONSE)
        payload["tables"][0]["ctime"] = "2026-10-03T18:00:00+08:00"
        request = self.directory_request()
        task = self.agent.add_snapshot(task["id"], "ifind", "report_query", request["parameters"], payload)
        with self.assertRaises(ContractError):
            validate_filing_original_request(task, {"catalog_id": task["source_records"][0]["id"], "source_id": "10001"})
        tool = next(item for item in data_request_contract(task)["tools"]
                    if item["qualified_id_for_lookup_only"] == "ifind.filing_original")
        self.assertEqual(tool["available_selections"], [])
        self.assertEqual(task["evidence"], [])

    def test_three_methods_from_directory_and_pdf_to_frozen_report(self):
        expected = {"gross_margin_v1": Decimal(40), "net_profit_margin_v1": Decimal(12),
            "operating_cash_profit_ratio_v1": Decimal(-8) / Decimal(12) * 100}
        for method in FINANCIAL_PERIOD_METHODS:
            with self.subTest(method=method):
                task = self.create(method)
                self.dispatches = []
                def directory(_):
                    current = self.agent.archive.get(task["id"])
                    self.assertEqual(current["evidence"], [])
                    self.assertEqual(current["calculations"], [])
                    return {"data_requests": [self.directory_request()], "stop_reason": ""}
                def download(_):
                    current = self.agent.archive.get(task["id"])
                    self.assertFalse(current["evidence"] or current["calculations"])
                    catalog = next(row for row in current["source_records"] if row["operation"] == "report_query")
                    return {"data_requests": [{
                            "id": "financial_original", "source": "ifind", "operation": "filing_original",
                            "parameters": {"catalog_id": catalog["id"], "source_id": "10001"},
                            "question_ids": ["q1"], "selection_reason": "沿用本任务核验目录，不猜下载地址"}],
                        "stop_reason": ""}
                def extract(_):
                    current = self.agent.archive.get(task["id"])
                    original = next(row for row in current["source_records"] if row["kind"] == "filing")
                    return {"evidence_specs": self.specs(original, method)}
                model = FabricatedModel([
                    {"questions": question_specs(task), "data_requests": [], "calculations": [self.calculation(method)]},
                    directory, download, extract,
                    {"answers": {"q1": {"answer": "仅核算已披露同口径指标，不推断投资收益。",
                        "refs": ["e_" + key for key in METHOD_REGISTRY[method]["input_units"]],
                        "calculation_refs": ["financial"]}}, "theses": []},
                ])
                with patch.object(self.agent.collector, "external", side_effect=self.transport), \
                        patch("researchhelper.model.urllib.request.urlopen", side_effect=model.respond):
                    result = self.agent.start(task["id"])
                self.assertEqual(result["status"], "已完成", {"error": result.get("error"), "gaps": result["gaps"],
                    "rejected": result["rejected_evidence"], "remaining_model_outputs": len(model.outputs)})
                self.assertEqual(self.dispatches, ["report_query", "filing_original"])
                self.assertEqual(result["ledger"]["used"]["data_requests"], 2)
                self.assertEqual(result["ledger"]["used"]["model_calls"], 5)
                self.assertEqual(result["ledger"]["supplements"], 0)
                self.assertEqual(result["ledger"]["limits"]["model_calls"], 8)
                self.assertEqual(result["ledger"]["reserve"]["model_calls"], 1)
                self.assertEqual(len(result["evidence"]), 2)
                self.assertEqual(len(result["calculations"]), 1)
                for evidence in result["evidence"]:
                    self.assertEqual(evidence["source_check"], "通过")
                    self.assertEqual(evidence["applicability_check"], "通过")
                calculation = result["calculations"][0]
                self.assertAlmostEqual(calculation["output"]["value"], float(expected[method]),
                    delta=calculation["output"]["tolerance"])
                self.assertTrue(calculation["method_binding"] and calculation["dependency_hash"])
                saved = self.agent.archive.report(result["id"], result["version"])
                self.assertEqual(saved["content"]["calculations"], result["calculations"])
                reopened = ResearchAgent(ResearchArchive(self.agent.archive.root), self.agent.resources, offline=True)
                self.assertEqual(reopened.archive.get(result["id"])["ledger"], result["ledger"])
                self.assertEqual(reopened.export(result["id"]), self.agent.export(result["id"]))
                self.assertFalse(model.outputs)


if __name__ == "__main__":
    unittest.main()
