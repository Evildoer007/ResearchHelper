"""Source anchors stay exact and frozen; the reader sees human labels."""
import copy
import json
from pathlib import Path
import unittest

from researchhelper.reports import render, source_locator_label

ROOT = Path(__file__).resolve().parents[2]
REAL_CONTENT = ROOT / "result/host-research/lpr-20250520/study-kfq462ut/result/researchhelper/rh_b1a55ca1d11e45e29261fc5b2eeda858/research-v1.json"


class SourceLocatorPresentationTests(unittest.TestCase):
    def test_material_location_is_not_an_internal_dictionary(self):
        self.assertEqual(source_locator_label({"record_locator": {"part": "段落4"}}), "段落4")

    def test_api_indices_are_one_based_only_for_display(self):
        item = {"record_locator": {"table": 0, "row": 1, "field": "close"}}
        original = copy.deepcopy(item)
        self.assertEqual(source_locator_label(item), "表1，行2，字段close")
        self.assertEqual(item, original)

    def test_unit_anchor_remains_visible(self):
        self.assertEqual(source_locator_label({"record_locator": {"part": "第2页", "unit_part": "第1页"}}),
                         "第2页，单位依据第1页")

    def test_zero_is_not_missing_and_boolean_is_not_a_row(self):
        self.assertEqual(source_locator_label({"record_locator": {"row": 0, "table": False}}), "行1")

    def test_legacy_locations_keep_their_exact_text(self):
        for locator in (None, {}, "not a mapping", {"unknown": 8}):
            self.assertEqual(source_locator_label({"record_locator": locator, "locator": "历史定位原文"}), "历史定位原文")

    def test_actual_retained_report_preserves_frozen_content(self):
        content = json.loads(REAL_CONTENT.read_text())
        original = copy.deepcopy(content)
        paper = render(content, ROOT)
        self.assertIn("用户提供材料，段落4", paper)
        self.assertNotIn("&#x27;part&#x27;:", paper)
        self.assertIn("一年期LPR为3.0%", paper)
        self.assertIn("不能识别银行净息差变化方向", paper)
        self.assertEqual(content, original)
