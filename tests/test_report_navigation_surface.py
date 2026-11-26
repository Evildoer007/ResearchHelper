"""Report shells own one working keyboard entry and retain research links."""
import unittest
from lxml import html
from tests import ROOT
from researchhelper.publication_content import draft_logic_heading
from researchhelper.report_designer import compose_strategy_document


class ReportNavigationSurfaceTests(unittest.TestCase):
    def source(self, navigation, summary=True):
        body = '<section id="summary"><h2>核心结论</h2><p>报告的公开研究内容。</p></section>' if summary else ''
        return ('<html><head><title>研究报告</title></head><body>' + navigation +
            '<main id="source-main"><header><h1>合成报告</h1></header>' + body +
            '<article id="answer-one" data-question-ref="one"><h2>研究结论</h2>'
            '<p>保留数字12.5及其来源<a href="#original-record">原始来源</a>。</p></article>'
            '<footer>来源：公开材料。</footer></main>'
            '<div id="original-record" hidden>合成原件12.5。</div>'
            '<a class="source-link" href="#original-record">查看原件</a></body></html>')

    def assert_one_working_entry(self, tree):
        entries=tree.xpath('//a[contains(concat(" ",normalize-space(@class)," ")," skip ") or '
            'contains(concat(" ",normalize-space(@class)," ")," skip-link ")]')
        self.assertEqual(len(entries),1)
        target=entries[0].get('href','')
        self.assertTrue(target.startswith('#'))
        self.assertEqual(len(tree.xpath('//*[@id=$target]',target=target[1:])),1)
        self.assertIsNotNone(tree.xpath('//*[@id=$target]',target=target[1:])[0].get('tabindex'))

    def test_source_navigation_is_replaced_by_one_current_shell_entry(self):
        for classes in ('skip','skip old-reader','skip-link','legacy skip-link','skip-link skip'):
            for profile in ('standard','detailed'):
                with self.subTest(classes=classes,profile=profile):
                    source=self.source('<a class="'+classes+'" href="#source-main">旧正文入口</a>',summary=False)
                    tree=compose_strategy_document(source,ROOT,profile=profile)
                    self.assert_one_working_entry(tree)
                    self.assertNotIn('旧正文入口',tree.text_content())

    def test_main_entry_remains_unique_when_source_has_no_navigation(self):
        tree=compose_strategy_document(self.source('',summary=False),ROOT)
        self.assert_one_working_entry(tree)

    def test_research_references_and_non_navigation_content_are_preserved(self):
        source=self.source('<a class="skip" href="#source-main">旧正文入口</a>')
        tree=compose_strategy_document(source,ROOT)
        self.assertEqual(tree.xpath('//main//a[@href="#original-record"]/text()'),['原始来源'])
        self.assertEqual(tree.xpath('//a[@class="source-link"]/text()'),['查看原件'])
        self.assertEqual(tree.get_element_by_id('original-record').text_content(),'合成原件12.5。')
        self.assertIn('报告的公开研究内容。',tree.find('.//main').text_content())

    def test_mechanism_heading_stays_full_in_report_and_fits_the_contents(self):
        title = '传导逻辑一：政策利率上移，准备金安排延续'
        article = '<section id="logic-1"><h2>' + title + '</h2><p>原文及分析段落。</p></section>'
        source = self.source('').replace('<footer>', article + '<footer>', 1)
        tree = compose_strategy_document(source, ROOT)
        item, = tree.xpath('//aside[contains(@class,"report-toc")]//a[@href="#logic-1"]')
        self.assertEqual(item.find('span').tail, '传导逻辑一')
        self.assertEqual(item.get('title'), title)
        self.assertEqual(tree.get_element_by_id('logic-1').find('h2').text_content(), title)

    def test_generated_logic_number_is_printed_once(self):
        self.assertEqual(draft_logic_heading('mechanism', 2, '传导逻辑一：收益率与通胀'),
                         '传导逻辑二：收益率与通胀')
        self.assertEqual(draft_logic_heading('research', 1, '现金流分化'), '策略逻辑一：现金流分化')
