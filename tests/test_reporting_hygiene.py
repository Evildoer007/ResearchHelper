from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.writer import (_normalise_chart_specs, _public_text, _parse, write,
                         LogicContent, ReportContent)
from render.layout import _footer_block


class ReportingHygieneTests(unittest.TestCase):
    def test_customer_text_removes_internal_ids_and_confidence_labels(self) -> None:
        text = "[F1+M1+E1] 事件形成正向传导，证据链置信度为中；仍需观察需求。"
        cleaned = _public_text(text)
        self.assertNotIn("F1", cleaned)
        self.assertNotIn("置信度", cleaned)
        self.assertIn("仍需观察需求", cleaned)

    def test_customer_text_removes_repeated_query_date_but_keeps_actual_period(self) -> None:
        self.assertEqual(_public_text("数据查询日：2026-09-15。2026年中报盈利改善。"), "2026年中报盈利改善。")
        self.assertEqual(_public_text("截至本次数据查询日2026-09-15，盈利改善。"), "盈利改善。")
        self.assertEqual(_public_text("产业机制层面：价格上涨。"), "价格上涨。")

    def test_dense_summary_requires_rewrite_and_audit_notes_are_separate(self) -> None:
        ma = SimpleNamespace(plan=SimpleNamespace(logics=[]))
        rc = ReportContent(主题="测试", 类型="事件")
        gaps = _parse({"核心结论": "PB3倍、ROE20%、涨幅10%。", "内部审核备注": ["平台映射仍有待补充。"]}, ma, rc)
        self.assertIn("核心结论", gaps)
        self.assertEqual(rc.内部审核备注, ["平台映射仍有待补充。"])
        self.assertNotIn("平台映射", rc.核心结论)

    def test_writer_rewrites_dense_summary_without_adding_date_prefix(self) -> None:
        class Client:
            total_tokens = 0
            calls = 0
            def available(self):
                return True
            def chat_json(self, system, prompt, **kwargs):
                self.calls += 1
                summary = "PB3倍、ROE20%、涨幅10%。" if self.calls == 1 else "需求改善有望带动产业链，关注订单兑现。"
                return SimpleNamespace(ok=True, data={"核心结论": summary}, error="")
        ma = SimpleNamespace(plan=SimpleNamespace(主题="测试", 类型="事件", logics=[]),
                             数据查询日="2026-09-15", field_values={}, 研究篮子口径="主题篮子", 研究篮子=[])
        client = Client()
        with patch("core.writer._build_user_prompt", return_value="{}"):
            rc = write(ma, client)
        self.assertEqual(client.calls, 2)
        self.assertEqual(rc.空缺逻辑, [])
        self.assertNotIn("数据查询日", rc.核心结论)

    def test_chart_title_is_not_overwritten_by_conclusion(self) -> None:
        ma = SimpleNamespace(数据查询日="2026-09-15", field_values={}, 研究篮子口径="主题篮子", 研究篮子=[])
        rc = ReportContent(主题="测试", 类型="事件")
        rc.logics = [LogicContent(逻辑id="x", 论述="", 结论="", 图表规格列表=[{
            "类型": "bar", "标题": "主题篮子近20日涨跌幅", "图表结论": "涨幅10%支撑估值", "数据点": []}])]
        _normalise_chart_specs(ma, rc)
        self.assertEqual(rc.logics[0].图表规格列表[0]["标题"], "主题篮子近20日涨跌幅")

    def test_footer_includes_actual_event_sources_with_links(self) -> None:
        ma = SimpleNamespace(数据查询日="2026-09-15", field_values={}, 事件证据={"引用来源": [
            {"来源": "公司产品发布公告", "链接": "https://example.com/ir"},
            {"来源": "行业机制报告", "链接": "https://example.com/report"}]})
        html = _footer_block(ma)
        self.assertIn("公司产品发布公告", html)
        self.assertIn('href="https://example.com/ir"', html)
        self.assertIn("行业机制报告", html)

    def test_chart_placeholder_is_replaced_by_explicit_date(self) -> None:
        ma = SimpleNamespace(
            数据查询日="2026-09-15", field_values={}, 研究篮子口径="主题篮子（5只）", 研究篮子=[])
        rc = ReportContent(主题="测试", 类型="板块")
        rc.logics = [LogicContent(
            逻辑id="x", 论述="", 结论="",
            图表规格列表=[{"类型": "bar", "标题": "测试", "数据截至": "见底稿", "数据点": []}],
        )]
        _normalise_chart_specs(ma, rc)
        self.assertEqual(rc.logics[0].图表规格列表[0]["数据截至"], "2026-09-15")


if __name__ == "__main__":
    unittest.main()
