"""Static originals, intent-specific publication and isolated parsing. No network."""

import base64
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import candidate_evidence, extract_material, validate_publication_basis
from researchhelper.html_material import LIMITATION, extract_html
from researchhelper.material_parser import MaterialParser, verified_material
from researchhelper.reasoning import evidence_contract, source_views

ROOT = Path(__file__).resolve().parents[2]
PRIOR = ROOT / "result/acceptance/live-policy-reasoning/isolated-policy-sfxccmcm"
TITLE = "2025年5月20日全国银行间同业拆借中心受权公布贷款市场报价利率（LPR）公告"
BODY = "中国人民银行授权全国银行间同业拆借中心公布，2025年5月20日贷款市场报价利率（LPR）为：1年期LPR为3.0%，5年期以上LPR为3.5%。以上LPR在下一次发布LPR之前有效。"
SAMPLE = ('<html><head><title>示例公告</title><script>fetch("https://example.invalid/")</script></head>'
          '<body><h1>2025年5月20日发布示例公告</h1><p style="display:none">1年期LPR为<span>3.0</span>%。</p>'
          '<script>执行伪造正文</script><style>样式伪造正文</style><iframe src="https://example.invalid/"></iframe>'
          '<p>&lt;img src=x onerror=alert(1)&gt;</p></body></html>').encode()


def retained_original():
    receipt = json.loads((PRIOR / "receipt-resumed.json").read_text())
    database = PRIOR / "data/researchhelper/researchhelper.sqlite3"
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        task = json.loads(db.execute("SELECT body FROM tasks WHERE id=?", (receipt["task_id"],)).fetchone()[0])
    payload = base64.b64decode(task["materials"][0]["snapshot"]["original_base64"], validate=True)
    assert hashlib.sha256(payload).hexdigest() == receipt["original_sha256"]
    return payload


class HTMLMaterialTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="html-original-", dir=ROOT / "result/acceptance")
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True, isolate_materials=True)

    def task(self, intent="综合研究", payload=SAMPLE, cutoff="2025-05-21T00:00:00+08:00"):
        task = self.agent.create("核对公告原文", intent=intent, objects=["LPR"], cutoff=cutoff)
        return self.agent.add_material(task["id"], "announcement.html", payload)

    def proposal(self, task):
        artifact = task["materials"][0]
        title = next(p for p in artifact["parts"] if "发布示例公告" in p["text"])
        body = next(p for p in artifact["parts"] if "1年期LPR" in p["text"])
        return {"id": "statement", "artifact_id": artifact["id"], "object": "LPR",
            "record_locator": {"part": body["locator"]}, "text": body["text"], "unit": "不适用",
            "period": "2025-05-20", "nature": "合同政策条款", "question_ids": ["q1"],
            "published_at": "2025-05-20", "publication": {"at": "2025-05-20", "basis": "原件明确发布日期",
                "precision": "日", "timezone": "Asia/Shanghai", "certainty": "已确认",
                "basis_ref": {"kind": "material", "artifact_id": artifact["id"],
                    "snapshot_hash": artifact["snapshot_hash"], "part": title["locator"], "quote": title["text"]}},
            "review": {"question_ids": ["q1"], "allowed_natures": ["合同政策条款"],
                "conditions": ["静态原文核对，不推断显示状态"], "reason": "原件逐字条款"}}

    def test_retained_official_original_has_contiguous_body_and_title(self):
        payload = retained_original()
        parsed = MaterialParser().parse("original.html", payload)
        self.assertIn(TITLE, [p["text"] for p in parsed["parts"]])
        self.assertIn(BODY, [p["text"] for p in parsed["parts"]])
        self.assertEqual(parsed["extract_version"], "html-1")
        self.assertEqual(parsed["limitation"], LIMITATION)
        self.assertTrue(parsed["parse_runtime"]["parser_worker_reaped"])
        self.assertFalse(parsed["parse_runtime"]["network_request_started"])
        self.assertEqual(parsed["source_check"], "待核验")

    def test_no_scripts_resources_or_display_inference(self):
        with patch("socket.socket.connect", side_effect=AssertionError("No network")):
            parts = extract_html(SAMPLE)
        text = str(parts)
        self.assertIn("1年期LPR为3.0%。", text)
        self.assertIn("<img src=x onerror=alert(1)>", text)
        self.assertNotIn("伪造正文", text)
        self.assertNotIn("fetch(", text)

    def test_table_rows_and_entities_keep_stable_locators(self):
        payload = '<h1>标题</h1><table><tr><th>值</th><th>单位</th></tr><tr><td>100</td><td>百万元</td></tr></table><p>甲&amp;乙<br>丙</p>'.encode()
        parts = extract_html(payload)
        self.assertEqual([p["locator"] for p in parts], ["标题1", "表1", "段落1"])
        self.assertIn("100\t百万元", parts[1]["text"])
        self.assertEqual(parts[2]["text"], "甲&乙\n丙")

    def test_unknown_publication_document_only_runs_without_financial_output(self):
        task = self.task("原件内容核对")
        spec = self.proposal(task)
        spec.update(usage_scope="document_content", published_at=None,
                    publication=evidence_contract(task)["document_publication_template"])
        done = self.agent.start(task["id"], {"evidence_specs": [spec]})
        self.assertEqual(done["status"], "已完成", done.get("error"))
        self.assertIsNone(done["evidence"][0]["published_at"])
        self.assertEqual((done["theses"], done["calculations"], done["tracking"]), ([], [], []))
        self.assertEqual(done["ledger"]["used"]["model_calls"], 0)
        self.assertEqual(done["ledger"]["used"]["data_requests"], 0)
        self.assertIn("1年期LPR为3.0%。", self.agent.export(task["id"])["html"])

    def test_same_unknown_scope_cannot_enter_ordinary_research(self):
        task = self.task()
        spec = self.proposal(task)
        spec.update(usage_scope="document_content", published_at=None,
            publication={"at": None, "basis": "首次公开时间未核验", "certainty": "未知",
                         "precision": "未知", "timezone": "未知", "basis_ref": None})
        done = self.agent.start(task["id"], {"evidence_specs": [spec]})
        self.assertEqual(done["evidence"], [])
        self.assertEqual(done["status"], "部分完成")

    def test_date_only_basis_retains_conservative_day_end(self):
        task = self.task()
        spec = self.proposal(task)
        item = candidate_evidence(task, task["materials"][0], spec)
        validate_publication_basis(item, task["materials"])
        assessed = ResearchAnalyzer().assess(task, item, spec["review"])
        self.assertEqual(assessed["publication"]["precision"], "日")
        early = dict(task, cutoff="2025-05-20T10:00:00+08:00")
        with self.assertRaises(ContractError):
            ResearchAnalyzer().assess(early, copy.deepcopy(item), spec["review"])

    def test_minute_time_cannot_be_invented_as_seconds(self):
        task = self.task(payload=SAMPLE+b'<p>2025-05-20 09:00</p>')
        spec = self.proposal(task)
        spec.update(published_at="2025-05-20T09:00:00+08:00")
        spec["publication"].update(at=spec["published_at"], precision="秒")
        item = candidate_evidence(task, task["materials"][0], spec)
        with self.assertRaises(ContractError):
            validate_publication_basis(item, task["materials"])

    def test_tampered_parts_and_stale_version_are_rejected(self):
        parsed = MaterialParser().parse("original.html", SAMPLE)
        self.assertEqual(verified_material(parsed, SAMPLE)["parts"], parsed["parts"])
        for field, value in (("extract_version", "1"), ("parts", [{"locator": "段落1", "text": "假造"}])):
            changed = copy.deepcopy(parsed)
            changed[field] = value
            with self.assertRaises(ContractError):
                verified_material(changed, SAMPLE)

    def test_same_bytes_text_format_does_not_share_html_cache(self):
        html = MaterialParser().parse("original.html", SAMPLE)
        text = MaterialParser().parse("original.txt", SAMPLE)
        self.assertEqual(text["extract_version"], "1")
        self.assertNotEqual(text["parts"], html["parts"])
        self.assertEqual(verified_material(text, SAMPLE)["parts"], text["parts"])

    def test_invalid_encoding_empty_dynamic_and_caps_do_not_create_text(self):
        with self.assertRaises(UnicodeDecodeError):
            extract_html(b'\xff')
        with patch("researchhelper.html_material.MAX_DEPTH", 3), self.assertRaises(ContractError):
            extract_html(b'<div><div><div><div>deep</div>')
        with patch("researchhelper.html_material.MAX_NODES", 1), self.assertRaises(ContractError):
            extract_html(b'<p>a</p><p>b</p>')
        with patch("researchhelper.html_material.MAX_CHARACTERS", 2), self.assertRaises(ContractError):
            extract_html(b'<p>abcd</p>')
        dynamic = extract_material("dynamic.htm", b'<script>document.write("invented")</script>')
        self.assertEqual(dynamic["parts"], [])
        self.assertEqual(dynamic["status"], "解析受限")

    def test_parser_failure_retains_exact_original_and_zero_budget(self):
        task = self.task(payload=b'\xff')
        raw = task["materials"][0]
        self.assertEqual(base64.b64decode(raw["original_base64"]), b'\xff')
        self.assertEqual(raw["parts"], [])
        self.assertEqual(raw["status"], "解析受限")
        self.assertEqual(task["ledger"]["used"]["model_calls"], 0)

    def test_mode_specific_schema_and_static_limitation_reach_host(self):
        normal = self.task()
        document = self.task("原件内容核对")
        self.assertEqual(evidence_contract(normal)["publication_types"]["certainty"], ["已确认"])
        self.assertEqual(evidence_contract(document)["publication_types"]["certainty"], ["未知"])
        self.assertEqual(source_views(normal)[0]["extraction_limitation"], LIMITATION)
        self.assertEqual(source_views(normal)[0]["extract_version"], "html-1")
        self.assertEqual(extract_material("empty.txt", b'')["limitation"],
                         "未提取到正文，可能为扫描件；不生成伪造摘录")


if __name__ == "__main__":
    unittest.main()
