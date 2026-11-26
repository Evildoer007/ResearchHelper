"""Source anchors stay exact and frozen; the reader sees human labels."""
import copy
from pathlib import Path
import unittest

from researchhelper.reports import render, source_locator_label
from tests import test_document_content_offline as documents

ROOT = Path(__file__).resolve().parents[2]


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

    def test_archived_report_keeps_frozen_content_and_readable_location(self):
        from lxml import html

        fixture = documents.DocumentContentTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        literal = '费用上限为100百万元。'
        material = '\n\n'.join(('合成政策', '适用范围另行发布。', '以批复为准。', literal))
        task = fixture.material_task(text=material)
        spec = fixture.spec(task, record_locator={'part':'段落4'}, text=literal, raw_token='100',
                            raw_value=100, value=100, unit='百万元', raw_unit='百万元', conversion_factor=1)
        done = fixture.run_spec(task, spec)
        self.assertEqual(done['status'], '已完成', done.get('error'))
        saved = fixture.agent.export(done['id'])
        frozen = copy.deepcopy(saved)
        evidence, = saved['content']['evidence']
        self.assertEqual(evidence['record_locator'], {'part':'段落4'})
        for profile in ('standard','detailed'):
            page = html.document_fromstring(render(saved['content'], ROOT, report_profile=profile))
            article = page.get_element_by_id('ev-' + evidence['id'])
            source, = article.xpath('.//a')
            archived = page.get_element_by_id(source.get('href')[1:]).text_content()
            self.assertIn('用户提供材料，段落4', archived)
            self.assertIn(literal, archived)
            self.assertNotIn("'part':", archived)
        self.assertEqual(saved, frozen)
        self.assertEqual(fixture.agent.export(done['id']), frozen)
