"""Actual extraction grammar, without coercion, network or model calls."""

import copy
import tempfile
import unittest
from pathlib import Path

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import DOCUMENT_CONTENT_INTENT, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.reasoning import evidence_contract

ROOT = Path(__file__).resolve().parents[2]
TEXT = "示例条款金额为人民币300亿元，期限为3年，利率为1.56%。"


class MaterialExtractionGrammarTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rh-extraction-grammar-", dir=ROOT / "result/acceptance")
        self.addCleanup(temporary.cleanup)
        self.agent = ResearchAgent(ResearchArchive(temporary.name), ROOT, offline=True)
        task = self.agent.create("仅核对材料中的金额、期限和利率", intent=DOCUMENT_CONTENT_INTENT, objects=["示例条款"])
        self.task = self.agent.add_material(task["id"], "terms.txt", TEXT.encode())

    def spec(self, token="1.56", number=1.56, unit="%"):
        contract = evidence_contract(self.task)
        return {"id": "terms", "artifact_id": self.task["materials"][0]["id"], "object": "示例条款",
            "record_locator": {"part": "段落1"}, "text": TEXT, "period": "原件条款", "nature": "合同政策条款",
            "unit": unit, "raw_unit": unit, "raw_token": token, "raw_value": number, "value": number,
            "conversion_factor": 1, "question_ids": ["q1"], "usage_scope": "document_content", "published_at": None,
            "publication": contract["document_publication_template"],
            "review": {"question_ids": ["q1"], "allowed_natures": ["合同政策条款"], "conditions": ["仅核对原件陈述"],
                       "reason": "条款逐字对应当前问题"}}

    def run_spec(self, spec):
        return self.agent.start(self.task["id"], {"evidence_specs": [spec]})

    def test_numeric_and_review_types_are_exposed_without_changing_task(self):
        before = fingerprint(self.task)
        contract = evidence_contract(self.task)
        self.assertEqual(contract["version"], 2)
        self.assertEqual(contract["material_numeric_contract"]["conversion_factor"], 1)
        self.assertFalse(contract["material_numeric_contract"]["example_is_source_fact"])
        self.assertIn("不接受单个字符串", contract["review_types"]["conditions"])
        self.assertEqual(fingerprint(self.task), before)

    def test_percentage_raw_number_remains_in_original_units(self):
        done = self.run_spec(self.spec())
        self.assertEqual(done["status"], "已完成")
        self.assertEqual(done["evidence"][0]["value"], 1.56)
        self.assertEqual(done["ledger"]["used"]["model_calls"], 0)
        self.assertEqual(done["ledger"]["used"]["data_requests"], 0)

    def test_currency_and_unit_do_not_belong_in_numeric_token(self):
        done = self.run_spec(self.spec("人民币300亿元", 300, "亿元"))
        self.assertEqual(done["status"], "部分完成")
        self.assertEqual(done["evidence"], [])
        self.assertIn("无法确定性解析", done["rejected_evidence"][0]["rejection"])
        self.assertEqual(done["host_result"]["evidence_specs"][0]["raw_token"], "人民币300亿元")

    def test_numeric_token_with_percent_is_rejected_not_stripped(self):
        done = self.run_spec(self.spec("1.56%"))
        self.assertEqual(done["evidence"], [])
        self.assertEqual(done["host_result"]["evidence_specs"][0]["raw_token"], "1.56%")

    def test_numeric_token_with_year_unit_is_rejected(self):
        done = self.run_spec(self.spec("3年", 3, "年"))
        self.assertEqual(done["evidence"], [])

    def test_plain_number_with_explicit_currency_unit_is_accepted(self):
        done = self.run_spec(self.spec("300", 300, "亿元"))
        self.assertEqual(done["status"], "已完成")
        self.assertEqual(done["evidence"][0]["unit"], "亿元")

    def test_single_string_condition_is_not_silently_wrapped(self):
        spec = self.spec()
        spec["review"]["conditions"] = "仅核对原件陈述"
        done = self.run_spec(spec)
        self.assertEqual(done["evidence"], [])
        self.assertIn("须记录", done["rejected_evidence"][0]["rejection"])
        self.assertIsInstance(done["host_result"]["evidence_specs"][0]["review"]["conditions"], str)

    def test_percentage_is_not_automatically_converted_to_decimal(self):
        spec = self.spec()
        spec["value"] = 0.0156
        spec["conversion_factor"] = 0.01
        done = self.run_spec(spec)
        self.assertEqual(done["evidence"], [])

    def test_shape_example_cannot_substitute_for_task_original(self):
        spec = self.spec()
        spec.update(copy.deepcopy(evidence_contract(self.task)["material_numeric_contract"]["shape_example_only"]))
        done = self.run_spec(spec)
        self.assertEqual(done["evidence"], [])
        self.assertIn("无法定位", done["rejected_evidence"][0]["rejection"])


if __name__ == "__main__":
    unittest.main()
