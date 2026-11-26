"""Compact header dates preserve the exact frozen research boundary."""
import copy
import html
import unittest
from pathlib import Path
from lxml import html as dom

from researchhelper.reports import compose, render, research_date_label
from tests import test_document_content_offline as documents

ROOT = Path(__file__).resolve().parents[2]


class CompactReportCutoffTests(unittest.TestCase):
    def test_header_omits_subminute_precision_but_preserves_zone(self):
        self.assertEqual(research_date_label('2026-10-10T03:01:12.123456+08:00', compact=True),
                         '北京时间2026年10月10日03:01')
        self.assertEqual(research_date_label('2026-10-09T19:01:12.123456Z', compact=True),
                         '北京时间2026年10月10日03:01')
        self.assertEqual(research_date_label('2026-10-10T03:01:12+05:45',compact=True),
                         '北京时间2026年10月10日05:16')

    def test_full_detail_date_keeps_exact_precision(self):
        self.assertEqual(research_date_label('2026-10-10T03:01:12.123456+08:00'),
                         '2026年10月10日03:01:12.123456UTC+08:00')

    def test_missing_zone_is_not_invented(self):
        self.assertEqual(research_date_label('2026-10-10T03:01:12.123456', compact=True),
                         '2026年10月10日03:01')

    def test_date_only_and_unverified_value_remain_unchanged(self):
        self.assertEqual(research_date_label('2026-10-10', compact=True), '2026年10月10日')
        self.assertEqual(research_date_label('未核验', compact=True), '未核验')

    def test_rendered_header_preserves_frozen_content_and_original_cutoff(self):
        fixture = documents.DocumentContentTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        task = fixture.agent.create('解释铜价上涨对下游利润的影响机制', entry='事件', objects=['铜'],
                                    cutoff='2026-10-10T03:01:12.123456+08:00')
        done = fixture.agent.start(task['id'])
        content = compose(done)
        before = copy.deepcopy(content)
        frozen = fixture.agent.export(done['id'])
        paper = render(content, ROOT)
        tree=dom.document_fromstring(paper);header=tree.find('.//header')
        self.assertIn('北京时间2026年10月10日03:01',header.text_content())
        self.assertEqual(header.find('.//time').get('datetime'),content['cutoff'])
        self.assertNotIn('03:01:12.123456',header.text_content())
        self.assertIn(content['cutoff'],tree.get_element_by_id('scope').text_content())
        self.assertEqual(content, before)
        self.assertEqual(fixture.agent.export(done['id']), frozen)
