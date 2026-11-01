"""Preserve strategy chart assertions with current reader and concise caption."""
from researchhelper.reports import render
from tests import test_strategy_report_figures_offline as original


class CurrentStrategyCharts(original.StrategyReportFigureTests):
    def test_graph_is_adjacent_to_its_answer_and_only_rendered_once(self):
        content, _ = self.real_content()
        paper = render(content, original.ROOT)
        self.assertEqual(paper.count("class='research-figure'"), 1)
        self.assertLess(paper.index("class='research-figure'"), paper.index("<section id='facts'"))
        self.assertIn('查看图表数据', paper)
        self.assertIn('<figcaption', paper)
        self.assertIn("href='#calc_price_change_510300_20260929_30'>计算1</a>", paper)
        self.assertNotIn('>计算calc_price_change_', paper)

    def test_reader_identity_is_separate_from_frozen_content_template(self):
        content, _ = self.real_content()
        paper = render(content, original.ROOT)
        self.assertIn("data-reader-version='rh-reader-6'", paper)
        self.assertIn('模板' + content['template_version'], paper)
        self.assertEqual(content['template_version'], 'rh-paper-3')
