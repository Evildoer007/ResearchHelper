"""Report navigation follows real arguments, without empty moved-from groups."""
import socket
import tempfile
import unittest
from unittest.mock import patch

from lxml import html
from tests import ROOT
from core.tests.test_report_measurement_accessibility_offline import chart_data
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import canonical
from researchhelper.engine import ResearchAgent
from researchhelper.publication_content import publication_dataset
from researchhelper.report_designer import compose_strategy_document, design
from researchhelper.visual import research_figures


class FormalReportStructureTests(unittest.TestCase):
    def setUp(self):
        denied = patch.object(socket.socket, 'connect', side_effect=AssertionError('No API calls'))
        denied.start()
        self.addCleanup(denied.stop)

    def source(self, *, before='', after='', arguments=None):
        arguments = arguments or [
            '<article data-thesis-ref="fee"><h3>年度费用比较</h3><p>年度费用相差0.40个百分点。</p></article>']
        return ('<html><head><title>比较报告</title></head><body><main>'
                '<header><h1>比较报告</h1></header><section id="theses"><h2>投资逻辑</h2>'
                + before + ''.join(arguments) + after + '</section></main></body></html>')

    def render(self, source=None, profile='standard'):
        return compose_strategy_document(source or self.source(), ROOT, profile=profile)

    def test_moved_argument_does_not_leave_an_empty_section(self):
        for profile in ('standard', 'detailed'):
            with self.subTest(profile=profile):
                tree = self.render(profile=profile)
                self.assertFalse(tree.xpath('//main//section[@id="theses"]'))
                self.assertFalse(tree.xpath('//main//h2[text()="投资逻辑"]'))

    def test_argument_is_a_real_report_section_in_both_profiles(self):
        for profile in ('standard', 'detailed'):
            with self.subTest(profile=profile):
                tree = self.render(profile=profile)
                titles = tree.xpath('//article[@data-thesis-ref="fee"]/h2/text()')
                self.assertEqual(titles, ['年度费用比较'])

    def test_contents_link_targets_the_actual_argument(self):
        tree = self.render()
        links = tree.xpath('//nav//a')
        self.assertEqual(len(links), 1)
        self.assertIn('年度费用比较', links[0].text_content())
        target = tree.get_element_by_id(links[0].get('href')[1:])
        self.assertEqual(target.get('data-thesis-ref'), 'fee')
        self.assertIn('年度费用相差0.40个百分点。', target.text_content())
        self.assertEqual(links[0].get('title'), target.xpath('.//h2')[0].text_content())

    def test_multiple_arguments_retain_order_and_distinct_targets(self):
        arguments = [f'<article data-thesis-ref="t{i}"><h3>判断{i}</h3><p>原文{i}</p></article>'
                     for i in (1, 2, 3)]
        tree = self.render(self.source(arguments=arguments))
        links = tree.xpath('//nav//a')
        targets = [tree.get_element_by_id(link.get('href')[1:]) for link in links]
        self.assertEqual([target.get('data-thesis-ref') for target in targets], ['t1', 't2', 't3'])
        self.assertEqual(len({link.get('href') for link in links}), 3)
        self.assertEqual([target.xpath('./h2/text()')[0] for target in targets], ['判断1', '判断2', '判断3'])

    def test_residual_background_prose_is_not_discarded(self):
        tree = self.render(self.source(before='<p>独立背景，必须保留。</p>'))
        remaining = tree.xpath('//section[@id="theses"]')[0]
        self.assertIn('独立背景，必须保留。', remaining.text_content())
        self.assertTrue(remaining.xpath('./h2[text()="投资逻辑"]'))
        self.assertFalse(remaining.xpath('.//article'))

    def test_bare_text_and_heading_tail_are_not_discarded(self):
        for source, text in ((self.source(before='未包裹的背景说明。'), '未包裹的背景说明。'),
                             (self.source(after='末尾说明。'), '末尾说明。')):
            with self.subTest(source=source):
                tree = self.render(source)
                self.assertIn(text, tree.find('.//main').text_content())

    def test_residual_table_figure_and_image_are_not_discarded(self):
        for residual in ('<table><tr><td>原始值</td><td>12</td></tr></table>',
                         '<figure><div id="retained-plot"></div></figure>',
                         '<img src="data:image/png;base64,AA==" alt="原始图">'):
            with self.subTest(residual=residual):
                tree = self.render(self.source(after=residual))
                self.assertTrue(tree.xpath('//section[@id="theses"]'))

    def test_unrelated_heading_only_section_is_not_removed(self):
        source = self.source().replace('</main>', '<section id="independent"><h2>独立章节</h2></section></main>')
        self.assertTrue(self.render(source).xpath('//section[@id="independent"]/h2'))

    def test_preserved_background_does_not_share_an_anchor_with_the_template(self):
        tree = self.render(self.source(before='<p>独立背景。</p>'))
        identifiers = tree.xpath('//*[@id]/@id')
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertTrue(tree.xpath('//section[@id="theses"]/p[text()="独立背景。"]'))

    def test_skip_link_reaches_the_report_when_no_summary_was_authored(self):
        tree = self.render()
        link = tree.xpath('//a[@class="skip-link"]')[0]
        matches = tree.xpath('//*[@id="'+link.get('href')[1:]+'"]')
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].tag, 'main')

    def test_existing_argument_heading_and_identifier_are_preserved(self):
        argument = '<article id="original-id" data-thesis-ref="fee"><h2>原有标题</h2><h3>下级说明</h3><p>原文。</p></article>'
        tree = self.render(self.source(arguments=[argument]))
        node = tree.get_element_by_id('original-id')
        self.assertEqual(node.xpath('./h2/text()'), ['原有标题'])
        self.assertEqual(node.xpath('./h3/text()'), ['下级说明'])
        self.assertTrue(tree.xpath('//nav//a[@href="#original-id"]'))

    def test_existing_draft_sections_keep_their_heading_levels(self):
        source = ('<html><head></head><body><main><header><h1>已有初稿</h1></header>'
                  '<section id="logic-1"><h2>策略逻辑一</h2><h3>分析依据</h3><p>原文。</p></section>'
                  '</main></body></html>')
        tree = self.render(source)
        self.assertEqual(tree.xpath('//section[@id="logic-1"]/h2/text()'), ['策略逻辑一'])
        self.assertEqual(tree.xpath('//section[@id="logic-1"]/h3/text()'), ['分析依据'])

    def test_heading_attributes_and_inline_content_are_preserved(self):
        argument = '<article data-thesis-ref="fee"><h3 id="argument-title" lang="zh-CN">费用<strong>比较</strong></h3><p>原文。</p></article>'
        heading = self.render(self.source(arguments=[argument])).get_element_by_id('argument-title')
        self.assertEqual(heading.tag, 'h2')
        self.assertEqual(heading.get('lang'), 'zh-CN')
        self.assertEqual(heading.xpath('./strong/text()'), ['比较'])

    def test_argument_header_keeps_its_judgment_and_evidence_link(self):
        argument = ('<article data-thesis-ref="fee"><header class="argument-summary">'
                    '<div class="argument-heading"><h3><a href="#evidence">费用比较</a></h3>'
                    '<span>判断：支持</span></div></header><p>原文。</p></article>')
        tree = self.render(self.source(arguments=[argument]))
        article = tree.xpath('//article[@data-thesis-ref="fee"]')[0]
        self.assertEqual(article.xpath('./header/div/h2/a/@href'), ['#evidence'])
        self.assertEqual(article.xpath('./header/div/span/text()'), ['判断：支持'])
        self.assertTrue(tree.xpath('//nav//a[@href="#'+article.get('id')+'"]'))

    def test_real_case_pipeline_keeps_facts_and_figures_while_fixing_navigation(self):
        with tempfile.TemporaryDirectory(prefix='researchhelper-formal-structure-') as temporary:
            agent = ResearchAgent(ResearchArchive(temporary), ROOT, offline=True)
            for case in ('etf', 'mechanism', 'value', 'mixed'):
                task = agent.start(agent.create_case(case)['id'])
                self.assertEqual(task['status'], '已完成', task.get('error'))
                content = agent.archive.report(task['id'], task['delivery']['version'])['content']
                original = canonical(content)
                for profile in ('standard', 'detailed'):
                    with self.subTest(case=case, profile=profile):
                        result = design(content, ROOT, profile=profile)
                        tree = html.fromstring(result['html'])
                        articles = tree.xpath('//main//article[@data-thesis-ref]')
                        self.assertEqual(len(articles), len(content['theses']))
                        self.assertTrue(all(node.xpath('./h2|./header//h2') for node in articles))
                        self.assertFalse(tree.xpath('//main//section[@id="theses"]/h2[text()="投资逻辑"]'))
                        for article in articles:
                            self.assertTrue(tree.xpath('//nav//a[@href="#'+article.get('id')+'"]'))
                        self.assertEqual(result['document']['dataset'], publication_dataset(content))
                        figures = set(tree.xpath('//main//figure/@data-calculation-ref'))
                        if case == 'value':
                            scenario, = [chart for chart in chart_data(result['html'])
                                         if chart['kind'] == 'valuation_scenarios']
                            base, stress = scenario['calculation_refs']
                            self.assertIn(base, figures)
                            self.assertNotIn(stress, figures)
                            figures.add(stress)
                        self.assertEqual(figures, set(research_figures(content)))
                        self.assertEqual(canonical(content), original)
                self.assertEqual(task['ledger']['used']['data_requests'], 0)
                self.assertEqual(task['ledger']['used']['model_calls'], 0)


if __name__ == '__main__':
    unittest.main()
