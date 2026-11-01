"""Chat is a readable thread of independently frozen research tasks."""
import copy
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, calculate
from researchhelper.engine import ResearchAgent
from researchhelper.service import app_store, make_server

ROOT = Path(__file__).resolve().parents[2]


class ChatContracts(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=ROOT / "result" / "acceptance", prefix="chat-contract-")
        self.addCleanup(directory.cleanup)
        self.agent = ResearchAgent(ResearchArchive(directory.name), ROOT, offline=True)

    def test_follow_up_keeps_frozen_parent_and_does_not_adopt_old_evidence(self):
        old = self.agent.start(self.agent.create_case("etf")["id"])
        frozen = copy.deepcopy(self.agent.export(old["id"]))
        old_record = copy.deepcopy(self.agent.archive.get(old["id"]))
        new = self.agent.follow_up(old["id"], "再解释跟踪误差与费用的区别")
        self.assertNotEqual(old["id"], new["id"])
        self.assertEqual(new["parent"], old["id"])
        self.assertEqual(new["thread_id"], old["thread_id"])
        self.assertEqual([m["role"] for m in new["messages"]], ["user", "assistant", "user"])
        self.assertEqual(new["messages"][1]["task_id"], old["id"])
        self.assertEqual(new["messages"][-1]["task_id"], new["id"])
        self.assertIsNone(new["fixture"])
        self.assertFalse(new["synthetic"])
        self.assertEqual(new["evidence"], [])
        self.assertEqual(new["status"], "部分完成")
        self.assertEqual(self.agent.archive.get(old["id"]), old_record)
        self.assertEqual(self.agent.export(old["id"]), frozen)

    def test_follow_up_rejects_empty_long_and_running_questions(self):
        old = self.agent.create("研究指数估值")
        for invalid in (None, "", " ", "问" * 20001):
            with self.assertRaises(ContractError):
                self.agent.follow_up(old["id"], invalid)
        old["status"] = "运行中"
        self.agent.archive.save(old)
        with self.assertRaises(ContractError):
            self.agent.follow_up(old["id"], "继续")

    def test_limits_are_per_new_question_not_refunded_into_old_task(self):
        old = self.agent.create("个股利润率", limits={"model_calls": 3, "data_requests": 12, "seconds": 120})
        old["ledger"]["used"]["data_requests"] = 5
        self.agent.archive.save(old)
        new = self.agent.follow_up(old["id"], "海外收入如何影响利润率")
        self.assertEqual(new["ledger"]["limits"], old["ledger"]["limits"])
        self.assertEqual(self.agent.archive.get(old["id"])["ledger"]["used"]["data_requests"], 5)
        self.assertEqual(new["ledger"]["used"]["data_requests"], 0)

    def test_store_default_never_reuses_legacy_database(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(app_store(), Path.home() / "Library/Application Support/ResearchHelper/research-v1")
        with patch.dict(os.environ, {"RESEARCHHELPER_STORE": str(self.agent.archive.root)}):
            self.assertEqual(app_store(), self.agent.archive.root)

    def test_message_route_uses_a_related_task(self):
        server = make_server(self.agent.archive.root, ROOT)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            address = f"http://127.0.0.1:{server.server_port}"
            bootstrap = json.load(urlopen(address + "/api/bootstrap"))
            old = server.agent.start(server.agent.create_case("mechanism")["id"])
            payload = json.dumps({"message": "还有哪些反证？"}).encode()
            request = Request(address + "/api/tasks/" + old["id"] + "/messages", data=payload,
                              headers={"X-ResearchHelper-Token": bootstrap["token"], "Content-Type": "application/json"})
            new = json.load(urlopen(request))
            self.assertEqual(new["parent"], old["id"])
            server.agent.workers[new["id"]].join(10)
            self.assertFalse(server.agent.workers[new["id"]].is_alive())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)

    def test_overflow_and_underflow_fail_as_contract_errors(self):
        parameters = {"amount_unit": "百万元", "shares_unit": "百万股"}
        inputs = dict(receipt=100, debt=10, cost=10, shares=10, rate=10, years=1e308, price=1)
        with self.assertRaises(ContractError):
            calculate("residual_present_value_v1", inputs, parameters)
        inputs.update(rate=-.9, years=1e308)
        with self.assertRaises(ContractError):
            calculate("residual_present_value_v1", inputs, parameters)


if __name__ == "__main__":
    unittest.main()
