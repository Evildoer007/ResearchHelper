"""Observed-price calculations must not convert estimated values into realized returns."""

import unittest

from tests import test_method_dependency_workflow_offline as fixtures


class ObservedPriceNatureTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.MethodDependencyWorkflowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_estimated_prices_remain_evidence_but_cannot_compute_observed_return(self):
        task, host, _ = self.fixture.prepare("observed_price_return_v1")
        for item in host["evidence_specs"]:
            item["nature"] = "第三方估计"
            item["review"]["allowed_natures"] = ["第三方估计"]
        result = self.fixture.agent.start(task["id"], host)
        self.assertEqual(len(result["evidence"]), 2, result["rejected_evidence"])
        self.assertFalse(result["calculations"])
        self.assertNotEqual(result["questions"][0]["status"], "已回答")
        self.assertTrue(any("未来窗口或预测" in gap for gap in result["gaps"]), result["gaps"])


if __name__ == "__main__":
    unittest.main()
