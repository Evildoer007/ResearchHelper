"""Exact implemented tool names and parameter examples; no supplier or model."""
import copy
import io
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import data_request_contract, snapshot_record
from researchhelper.model import ModelService, ModelSettings
from researchhelper.planning import question_specs
from researchhelper.reasoning import PHASE_RULES, validate_plan

ROOT = Path(__file__).resolve().parents[2]


class DataRequestContractTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="rh-tool-contract-")
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=False)
        settings = ModelSettings(folder.name)
        settings.save({"endpoint": "https://example.invalid/chat", "model": "offline-fixture", "api_key": "FAKE_TEST_KEY"})
        self.agent.model = ModelService(settings, offline=False, journal=self.agent._journal)
        self.task = self.agent.create("核查600000.SH原件条款", entry="事件", objects=["600000.SH"],
            cutoff="2026-10-01T12:00:00+08:00", questions=[{"id": "q1", "question": "发行条款是什么", "kind": "fact"}])
        for name in ("socket.socket.connect", "socket.getaddrinfo"):
            blocker = patch(name, side_effect=AssertionError("No network or DNS in offline acceptance"))
            blocker.start()
            self.addCleanup(blocker.stop)

    def proposed(self, requests):
        return {"questions": question_specs(self.task), "data_requests": requests, "calculations": []}

    def test_tools_equal_implemented_registries_not_supplier_marketing(self):
        contract = data_request_contract(self.task)
        self.assertEqual({tool["qualified_id_for_lookup_only"] for tool in contract["tools"]}, {
            'ifind.cmd_history_quotation', 'ifind.date_sequence', 'ifind.edb_service', 'ifind.filing_original',
            'ifind.get_thscode', 'ifind.report_query', 'ifind.smart_stock_picking',
            'official.release_catalog', 'official.release_original',
            'tinyshare.cashflow', 'tinyshare.daily', 'tinyshare.daily_basic', 'tinyshare.fund_daily', 'tinyshare.income',
        })
        self.assertNotIn("basic_data_service", [tool["operation"] for tool in contract["tools"]])

    def test_every_concrete_example_passes_actual_plan_validator(self):
        for tool in data_request_contract(self.task)["tools"]:
            if tool["example"]:
                with self.subTest(tool=tool["operation"]):
                    validate_plan(self.task, self.proposed([tool["example"]]))
                    self.assertEqual(set(tool["example"]["parameters"]), set(tool["parameter_keys"]))

    def test_lookup_identifier_is_not_an_operation_alias(self):
        example = copy.deepcopy(data_request_contract(self.task)["tools"][0]["example"])
        example["operation"] = "ifind." + example["operation"]
        with self.assertRaises(ContractError):
            validate_plan(self.task, self.proposed([example]))
        self.assertIn("operation只填report_query等短名", PHASE_RULES["plan"])
        self.assertIn("qualified_id_for_lookup_only", PHASE_RULES["supplement"])

    def test_no_declared_code_does_not_infer_from_company_name(self):
        task = copy.deepcopy(self.task)
        task["question"], task["objects"] = "研究某银行事件", ["某银行"]
        contract = data_request_contract(task)
        self.assertEqual(contract["declared_codes"], [])
        self.assertTrue(all(tool["example"] is None for tool in contract["tools"]))

    def test_hk_code_uses_hk_filing_and_never_generates_mainland_tinyshare_example(self):
        task = copy.deepcopy(self.task)
        task["question"], task["objects"] = "研究00700.HK", ["00700.HK"]
        contract = data_request_contract(task)
        self.assertTrue(next(tool for tool in contract["tools"] if tool["operation"] == "cmd_history_quotation")["example"])
        self.assertTrue(all(tool["example"] is None for tool in contract["tools"] if tool["source"] == "tinyshare"))
        filing = next(tool["example"] for tool in contract["tools"] if tool["operation"] == "report_query")
        self.assertEqual(filing['parameters']['codes'], '00700.HK')
        validate_plan(task, {'questions': question_specs(task), 'data_requests': [filing], 'calculations': []})

    def test_descriptor_is_read_only_and_does_not_dispatch(self):
        before = copy.deepcopy(self.task)
        with patch("urllib.request.urlopen") as transport:
            contract = data_request_contract(self.task)
        transport.assert_not_called()
        self.assertEqual(self.task, before)
        self.assertEqual(contract["binding"]["input_hash"], self.task["input_hash"])
        self.assertTrue(contract["empty_requests_allowed"])
        self.assertNotIn("api_key", json.dumps(contract))

    def test_no_future_catalogue_identifier_is_fabricated(self):
        tool = next(tool for tool in data_request_contract(self.task)["tools"] if tool["operation"] == "filing_original")
        self.assertIsNone(tool["example"])
        self.assertEqual(tool["available_selections"], [])

    def test_retained_filing_selection_matches_real_binding_and_rejects_tampering(self):
        parameters = next(tool for tool in data_request_contract(self.task)["tools"] if tool["operation"] == "report_query")["example"]["parameters"]
        response = {"errorcode": 0, "tables": [{"reportDate": "2026-10-01", "thscode": "600000.SH", "secName": "合成测试主体",
            "ctime": "2026-10-01T09:00:00+08:00", "reportTitle": "合成公告", "pdfURL": "https://static.cninfo.com.cn/fixture.pdf", "seq": "fixture_1"}]}
        record = snapshot_record(self.task, "ifind", "report_query", parameters, response)
        self.task["source_records"].append(record)
        tool = next(tool for tool in data_request_contract(self.task)["tools"] if tool["operation"] == "filing_original")
        self.assertEqual(tool["available_selections"], [{"catalog_id": record["id"], "source_id": "fixture_1"}])
        record["catalog_entries"][0]["report_title"] = "篡改标题"
        tool = next(tool for tool in data_request_contract(self.task)["tools"] if tool["operation"] == "filing_original")
        self.assertEqual(tool["available_selections"], [])

    def test_actual_model_planner_receives_exact_contract_and_can_plan_no_request(self):
        requests = []
        proposed = self.proposed([])

        def transport(request, timeout):
            payload = json.loads(request.data)
            requests.append(json.loads(payload["messages"][1]["content"]))
            return io.BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(proposed)}}]}).encode())

        with patch("researchhelper.model.urllib.request.urlopen", side_effect=transport):
            final = self.agent.start(self.task["id"])
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["data_request_contract"], data_request_contract(self.task))
        self.assertEqual(final["status"], "部分完成")
        self.assertEqual(final["ledger"]["used"]["data_requests"], 0)

    def test_host_skill_queries_same_contract_without_model_or_archive_mutation(self):
        spec = importlib.util.spec_from_file_location("tool_contract_skill", ROOT / "core/session.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix="rh-host-tools-") as directory:
            session = module.Session(directory)
            task = session.create("核查600000.SH公告条款", entry="事件", objects=["600000.SH"],
                cutoff="2026-10-01T12:00:00+08:00", questions=[{"id": "q1", "question": "条款是什么", "kind": "fact"}])
            original = copy.deepcopy(session.archive.get(task["id"]))
            with patch("urllib.request.urlopen") as transport:
                tools = session.tools(task["id"])
            transport.assert_not_called()
            self.assertIsNone(session.agent.model)
            self.assertEqual(tools["data_requests"], data_request_contract(task))
            self.assertEqual(session.archive.get(task["id"]), original)


if __name__ == "__main__":
    unittest.main()
