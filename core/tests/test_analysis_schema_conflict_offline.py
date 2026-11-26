"""The shipped analysis example must be valid and grant no factual approval."""

import json
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError
from tests import test_document_content_offline as documents


class AnalysisSchemaConflictTests(unittest.TestCase):
    def setUp(self):
        self.fixture = documents.DocumentContentTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.task = self.fixture.material_task()
        self.calls = []
        network = patch("socket.socket.connect", side_effect=AssertionError("External network forbidden"))
        network.start()
        self.addCleanup(network.stop)

    def model(self, response=None):
        calls = self.calls

        class Model:
            def reason_phase(self, task, messages, phase):
                calls.append((phase, messages))
                system = messages[0]["content"]
                sample = system.split("JSON格式样例，仅说明结构，不代表本任务没有证据：", 1)[1].split("。请输出合法json：", 1)[0]
                return sample if response is None else json.dumps(response)

        return Model()

    def test_following_shipped_sample_is_valid_but_does_not_invent_answers(self):
        self.fixture.agent.model = self.model()
        result = self.fixture.agent._reason_on_verified_material(self.task)
        self.assertEqual(result, {"answers": {}, "theses": []})
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][0], "analyze")
        self.assertEqual(self.task["evidence"], [])
        self.assertTrue(all(q["status"] != "已回答" for q in self.task["questions"]))
        self.assertIn("receipt", self.task["reasoning_state"]["analysis"])

    def test_consumer_accepts_mapping_instead_without_fabricating_answers(self):
        self.fixture.agent.model = self.model({"answers": {}, "theses": []})
        result = self.fixture.agent._reason_on_verified_material(self.task)
        self.assertEqual(result, {"answers": {}, "theses": []})
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.task["evidence"], [])
        self.assertTrue(all(q["status"] != "已回答" for q in self.task["questions"]))
        self.assertIn("receipt", self.task["reasoning_state"]["analysis"])
