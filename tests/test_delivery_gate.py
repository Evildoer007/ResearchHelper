from __future__ import annotations

import unittest
from types import SimpleNamespace
from pathlib import Path
import tempfile

import main
from core.run_tracker import RunTracker
from render.gaps import delivery_check_markdown


class DeliveryGateTests(unittest.TestCase):
    @staticmethod
    def _content():
        logic = SimpleNamespace(逻辑id="T1", 论述="可复核正文" * 50, 图表规格列表=[{}, {}])
        return SimpleNamespace(plan=None), SimpleNamespace(核心结论="结论" * 30, logics=[logic])

    def test_pdf_page_count_is_recorded(self) -> None:
        ma, rc = self._content()
        result = delivery_check_markdown(ma, rc, pdf_pages=1)
        self.assertIn("状态：已导出", result)
        self.assertIn("真实 PDF 页数：**1 页**", result)

    def test_multi_page_is_allowed(self) -> None:
        ma, rc = self._content()
        result = delivery_check_markdown(
            ma, rc, pdf_pages=2,
            layout_audit={"overflow": [{"label": "策略逻辑二"}, {"label": "推荐结构·参考报价"}]},
        )
        self.assertIn("状态：已导出", result)
        self.assertIn("真实 PDF 页数：**2 页**", result)
        self.assertIn("不设单页限制", result)

    def test_missing_pdf_is_unverified_not_silently_accepted(self) -> None:
        ma, rc = self._content()
        result = delivery_check_markdown(ma, rc)
        self.assertIn("本次未导出 PDF", result)

    def test_tracker_does_not_block_multi_page_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tracker = RunTracker.create(root=Path(directory), request="测试")

            def work():
                tracker.add_metadata("一页通交付校验", "通过：PDF 已导出，实测 2 页（当前不限页数）")
                return "research.html"

            self.assertEqual(main._run_tracked(tracker, work), "research.html")
            self.assertNotEqual(tracker.status, "completed_not_deliverable")
            self.assertIn(tracker.status, {"completed", "completed_with_warnings"})


if __name__ == "__main__":
    unittest.main()
