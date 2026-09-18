from __future__ import annotations

import unittest
from types import SimpleNamespace

from core import event_evidence, genres
from core.brief import Brief, TargetRef


def _evidence_with_three_branches() -> event_evidence.EventEvidence:
    return event_evidence.parse({
        "事件事实": [{"证据ID": "F1", "内容": "IPO发行窗口已经披露", "来源": "发行公告"}],
        "产业机制": [{"证据ID": "M1", "内容": "申购资金会短期冻结", "来源": "交易规则"}],
        "A股暴露": [{"证据ID": "E1", "内容": "宽基指数覆盖相关市值风格", "来源": "指数方案"}],
        "组合传导链": [
            {"事实证据ID": ["F1"], "机制证据ID": ["M1"], "暴露证据ID": ["E1"],
             "结论": "流动性形成短期扰动", "影响分支": "市场流动性", "分支用途": "主方向"},
            {"事实证据ID": ["F1"], "机制证据ID": ["M1"], "暴露证据ID": ["E1"],
             "结论": "可能产生风格切换", "影响分支": "市值风格", "分支用途": "备选方向"},
            {"事实证据ID": ["F1"], "机制证据ID": ["M1"], "暴露证据ID": ["E1"],
             "结论": "产业链可能受益", "影响分支": "产业链", "分支用途": "仅审计"},
        ],
    })


class EventPathTests(unittest.TestCase):
    def test_market_state_does_not_require_external_event_evidence(self) -> None:
        b = Brief(原始需求="科技回调期的消费轮动", 主导类型=genres.TYPE_EVENT,
                  事件路径=genres.EVENT_PATH_MARKET_STATE, ok=True)
        self.assertFalse(event_evidence.required_for(b))
        self.assertTrue(event_evidence.assess(b, event_evidence.parse(None)).ready)

    def test_external_event_requires_evidence_even_without_verified_entity(self) -> None:
        b = Brief(原始需求="长鑫科技IPO有什么影响", 主导类型=genres.TYPE_EVENT,
                  事件路径=genres.EVENT_PATH_EXTERNAL,
                  触发实体=TargetRef("长鑫科技", "", ""), ok=True)
        gate = event_evidence.assess(b, event_evidence.parse(None))
        self.assertTrue(gate.required)
        self.assertFalse(gate.ready)

    def test_explicit_non_event_is_not_overridden_by_entity_compatibility(self) -> None:
        b = Brief(原始需求="英伟达AI产业趋势", 主导类型=genres.TYPE_EVENT,
                  事件路径=genres.EVENT_PATH_NONE,
                  触发实体=TargetRef("英伟达", "NVDA.O", "ok"), ok=True)
        self.assertFalse(event_evidence.required_for(b))

    def test_only_primary_and_one_secondary_branch_become_claims(self) -> None:
        evidence = _evidence_with_three_branches()
        self.assertTrue(evidence.complete, evidence.errors)
        claims = event_evidence.claims(evidence, subject="长鑫科技IPO")
        self.assertEqual([item.branch_name for item in claims], ["市场流动性", "市值风格"])
        self.assertEqual([item.branch_role for item in claims], ["主方向", "备选方向"])
        self.assertNotIn("[F1]", claims[0].source_text)
        self.assertNotIn("M1", claims[0].source_text)
        self.assertNotIn("置信度", claims[0].source_text)
        self.assertIn("发行公告", claims[0].source_text)

    def test_internal_display_keeps_ids_and_confidence_for_workpaper(self) -> None:
        evidence = _evidence_with_three_branches()
        self.assertIn("[F1]", evidence.event_facts[0].display())
        self.assertIn("置信度", evidence.inference_chains[0].display())
        self.assertNotIn("[F1]", evidence.event_facts[0].public_display())
        self.assertNotIn("置信度", evidence.inference_chains[0].public_display())

    def test_writer_fields_only_include_selected_chains_and_keep_source_ledger(self) -> None:
        evidence = _evidence_with_three_branches()
        ma = SimpleNamespace(field_values={})
        event_evidence.attach_to_analysis(ma, Brief(原始需求="IPO影响"), evidence)
        chains = [name for name in ma.field_values if name.startswith("事件组合传导链")]
        self.assertEqual(len(chains), 2)
        self.assertEqual(len(ma.事件证据["组合传导链"]), 3)
        self.assertEqual(len(ma.事件证据["引用来源"]), 3)

    def test_legacy_chains_are_normalized_to_primary_secondary_and_audit(self) -> None:
        raw = event_evidence.to_dict(_evidence_with_three_branches())
        for item in raw["组合传导链"]:
            item.pop("分支用途", None)
        evidence = event_evidence.parse(raw)
        self.assertEqual(
            [item.branch_role for item in evidence.inference_chains],
            ["主方向", "备选方向", "仅审计"],
        )


if __name__ == "__main__":
    unittest.main()
