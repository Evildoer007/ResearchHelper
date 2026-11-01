"""Unregistered Wind requests must not read credentials or start a connection."""

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import CAPABILITIES, EvidenceCollector, data_request_contract, validate_source_request


ROOT = Path(__file__).resolve().parents[2]


class WindSourceBoundaryTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / "result/acceptance/wind-adapter-reference"
        base.mkdir(parents=True, exist_ok=True)
        folder = tempfile.TemporaryDirectory(prefix="isolated-", dir=base)
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True)
        self.task = self.agent.create("核对600000.SH的收盘原值", entry="个股", objects=["600000.SH"],
            cutoff="2026-10-02T18:00:00+08:00", questions=[{"id": "q1", "question": "核对收盘原值", "kind": "fact"}])

    def test_sdk_and_sql_operation_names_do_not_grant_authority(self):
        before = copy.deepcopy(self.task)
        for operation in ("wsd", "daily", "select", "query"):
            with self.subTest(operation=operation), self.assertRaisesRegex(ContractError, "Wind查询与字段口径未登记"):
                validate_source_request(self.task, "wind", operation, {})
        self.assertEqual(self.task, before)

    def test_rejection_precedes_credential_access_connection_and_budget(self):
        class UnreadableCredentials(dict):
            def get(self, *args, **kwargs):
                raise AssertionError("Unregistered Wind must not read credentials")

        collector = EvidenceCollector(offline=False)
        before = copy.deepcopy(self.task)
        with patch("socket.socket.connect") as connect, patch("urllib.request.urlopen") as http:
            with self.assertRaisesRegex(ContractError, "Wind查询与字段口径未登记"):
                collector.external(self.task, "wind", "query", {}, UnreadableCredentials())
        connect.assert_not_called()
        http.assert_not_called()
        self.assertEqual(self.task, before)

    def test_catalog_and_model_tools_do_not_offer_an_unimplemented_adapter(self):
        before = copy.deepcopy(self.task)
        self.assertEqual(CAPABILITIES["wind"]["registered_operations"], [])
        self.assertFalse(any(item["source"] == "wind" for item in data_request_contract(self.task)["tools"]))
        self.assertEqual(self.task, before)
