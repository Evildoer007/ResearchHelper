"""Independent reader tests; public content and closed studies stay immutable."""
import copy
import hashlib
import json
from pathlib import Path
import unittest
from lxml import html as dom

from tests.financial_presentation_fixture import SOURCE as FINANCIAL_SOURCE, load_content
from researchhelper.report_designer import _group_figures

from researchhelper.contracts import ContractError
from researchhelper.reports import compose, render, research_figures, READER_VERSION, TEMPLATE_VERSION
from tests import test_document_content_offline as documents
from tests import test_method_dependency_workflow_offline as methods
from tests import test_management_custody_fee_offline as fees
from tests import test_index_change_workflow_offline as indices
from tests import test_earnings_cash_methods_offline as earnings

ROOT = Path(__file__).resolve().parents[2]


class StrategyReportFigureTests(unittest.TestCase):
    def fixture(self, module, name):
        fixture = getattr(module, name)()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        if hasattr(fixture, "agent"):
            self.addCleanup(fixture.agent.close_runtime)
        return fixture

    def method_content(self, method):
        fixture = self.fixture(methods, 'MethodDependencyWorkflowTests')
        task, host, _ = fixture.prepare(method)
        done = fixture.agent.start(task['id'], host)
        self.assertEqual(done['status'], '已完成', done.get('error'))
        return fixture.agent.export(done["id"])["content"]

    def real_content(self):
        # Actual frozen financial content is independent of deleted QA folders.
        # It is not a replacement for the unavailable historical ETF archive.
        return load_content(), FINANCIAL_SOURCE

    def price_content(self):
        """Two explicitly synthetic observations through the ordinary engine."""
        fixture = self.fixture(methods, 'MethodDependencyWorkflowTests')
        task, host, _ = fixture.prepare('observed_price_return_v1')
        done = fixture.agent.start(task['id'], host)
        self.assertEqual(done['status'], '已完成', done.get('error'))
        saved = fixture.agent.export(done['id'])
        folder = fixture.agent.archive.root / 'chart-fixture'
        folder.mkdir()
        source = folder / 'report.json'
        source.write_text(json.dumps(saved['content'], ensure_ascii=False))
        source.with_suffix('.html').write_text(saved['html'])
        return saved['content'], source

    def test_synthetic_prices_use_two_exact_observations_not_an_invented_path(self):
        content, _ = self.price_content()
        figures = research_figures(content)
        self.assertEqual(len(figures), 1)
        figure = next(iter(figures.values()))
        self.assertEqual(figure['kind'], 'points')
        self.assertEqual([(row['label'], row['value']) for row in figure['rows']],
                         [('2026-09-29', 10), ('2026-09-30', 11)])
        self.assertEqual(figure['unit'], '元')
        self.assertEqual(figure['objects'], ['合成测试主体'])
        self.assertEqual(figure['sources'], ['用户提供材料'])
        self.assertIn('不表示期间完整路径', figure['note'])
        self.assertIn('不等于持有总回报', figure['note'])

    def test_graph_is_adjacent_to_its_answer_and_only_rendered_once(self):
        content, _ = self.price_content()
        tree = dom.document_fromstring(render(content, ROOT))
        figures = tree.xpath('//main//figure[contains(concat(" ",normalize-space(@class)," ")," research-figure ")]')
        self.assertEqual(len(figures), 1)
        figure = figures[0]
        group = figure.xpath('ancestor::div[contains(concat(" ",normalize-space(@class)," ")," logic-reading-grid ")]')
        self.assertEqual(len(group), 1)
        answer = tree.get_element_by_id('answer-q1')
        self.assertIn(answer, list(group[0].iterdescendants()))
        self.assertEqual(answer.text_content().count(content['questions'][0]['answer']), 1)
        self.assertTrue(figure.xpath('.//figcaption'))
        ref = content['calculations'][0]['id']
        self.assertEqual(answer.xpath('.//a[@href="#'+ref+'"]/text()'), ['计算1'])
        self.assertTrue(figure.xpath('.//a[@href="#figure-detail-'+ref+'"]'))

    def test_frozen_real_case_is_not_recomputed_or_overwritten(self):
        content, source = self.real_content()
        before = copy.deepcopy(content)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        tree = dom.document_fromstring(render(content, ROOT))
        self.assertIsNotNone(tree.find('.//main'))
        self.assertIsNotNone(tree.get_element_by_id('rh-publication-document'))
        self.assertEqual(content, before)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)

    def test_synthetic_delivery_files_are_not_changed_by_rendering(self):
        content, source = self.price_content()
        before = copy.deepcopy(content)
        paths = (source, source.with_suffix('.html'))
        hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
        tree = dom.document_fromstring(render(content, ROOT))
        self.assertEqual(tree.find('.//main').text_content().count(content['questions'][0]['answer']), 1)
        self.assertEqual(content, before)
        self.assertEqual(hashes, [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths])

    def test_empty_prose_never_reserves_half_a_report_column(self):
        for profile in ('standard', 'detailed'):
            with self.subTest(profile=profile):
                section=dom.fromstring('<section><h2>研究结论</h2><figure data-calculation-ref="c1"><div id="plot"></div></figure></section>')
                _group_figures(section, {'plot': {'kind': 'points', 'reused_time_context': True}}, 1, profile)
                self.assertFalse(section.xpath('.//div[@class="logic-reading-grid"]'))
                self.assertEqual(len(section.xpath('./div[contains(@class,"paper-figure-group")]/figure')), 1)

    def test_one_plot_keeps_unrelated_answers_outside_reading_columns(self):
        section=dom.fromstring('<section><h2>研究结论</h2><article id="a1" class="research-argument"><p>价格判断<a href="#c1">计算1</a></p></article><article id="a2" class="research-argument"><p>另一问题的文字</p></article><figure data-calculation-ref="c1"><div id="plot"></div></figure></section>')
        _group_figures(section, {'plot': {'kind': 'points'}}, 1)
        grids=section.xpath('./div[@class="logic-reading-grid"]')
        self.assertEqual(len(grids), 1)
        self.assertEqual(grids[0].xpath('.//article/@id'), ['a1'])
        self.assertEqual(section.xpath('./article/@id'), ['a2'])
        self.assertLess(list(section).index(grids[0]),list(section).index(section.get_element_by_id('a2')))

    def test_shared_calculation_does_not_assign_a_plot_to_one_arbitrary_answer(self):
        section=dom.fromstring('<section><article id="a1" class="research-argument"><p>原值<a href="#c1">计算1</a></p></article><article id="a2" class="research-argument"><p>风险<a href="#c1">计算1</a></p></article><figure data-calculation-ref="c1"><div id="plot"></div></figure></section>')
        _group_figures(section, {'plot': {'kind': 'points'}}, 1)
        self.assertFalse(section.xpath('.//div[@class="logic-reading-grid"]'))
        self.assertEqual(section.xpath('./article/@id'), ['a1','a2'])
        self.assertEqual(len(section.xpath('.//figure')), 1)

    def test_answer_with_a_data_table_keeps_its_full_reading_width(self):
        section=dom.fromstring('<section><article id="a1" class="research-argument"><p>比较<a href="#c1">计算1</a></p><table><tr><td>完整原值</td><td>期间</td><td>来源</td></tr></table></article><figure data-calculation-ref="c1"><div id="plot"></div></figure></section>')
        original=dom.tostring(section.xpath('.//table')[0])
        _group_figures(section, {'plot': {'kind': 'points'}}, 1)
        self.assertFalse(section.xpath('.//div[@class="logic-reading-grid"]'))
        self.assertEqual(section.xpath('./article/@id'), ['a1'])
        self.assertEqual(dom.tostring(section.xpath('.//table')[0]),original)

    def test_price_and_official_nav_are_separate_named_inputs(self):
        content = self.method_content('etf_nav_premium_v1')
        figure = next(iter(research_figures(content).values()))
        self.assertEqual([(r['label'], r['value']) for r in figure['rows']], [('收盘价', 10.5), ('官方单位净值', 10)])
        self.assertEqual(figure['result_unit'], '%')
        self.assertEqual(figure['periods'], ['2026-09-30'])
        self.assertNotIn('IOPV', figure['title'])

    def test_financial_input_graph_does_not_synthesize_profit_or_annualize(self):
        content = self.method_content('gross_margin_v1')
        figure = next(iter(research_figures(content).values()))
        self.assertEqual([row['value'] for row in figure['rows']], [100, 60])
        self.assertEqual(figure['unit'], '百万元')
        self.assertEqual(figure['periods'], ['2026年上半年'])
        self.assertEqual(figure['result_value'], 40)

    def test_management_custody_components_do_not_become_total_expense_or_return(self):
        fixture = self.fixture(fees, 'ManagementCustodyFeeTests')
        task, host = fixture.prepare()
        content = compose(fixture.agent.start(task['id'], host))
        figure = next(iter(research_figures(content).values()))
        self.assertEqual([row['value'] for row in figure['rows']], [.15, .05, .5, .1])
        self.assertEqual([row['label'] for row in figure['rows']], ['ETF-A管理费', 'ETF-A托管费', 'ETF-B管理费', 'ETF-B托管费'])
        self.assertEqual(figure['unit'], '%/年')
        self.assertEqual(figure['result_unit'], '百分点/年')
        self.assertIn('不是全部费用率', figure['note'])
        self.assertIn('持有收益差', figure['note'])

    def test_mechanism_without_registered_quantities_has_no_fake_chart(self):
        fixture = self.fixture(documents, 'DocumentContentTests')
        task = fixture.agent.create_case('mechanism')
        content = compose(fixture.agent.start(task['id']))
        self.assertEqual(research_figures(content), {})
        paper = render(content, ROOT)
        self.assertNotIn("class='research-figure'", paper)
        self.assertNotIn("id='methods'", paper)

    def test_each_scenario_remains_independent_with_its_original_label(self):
        fixture = self.fixture(documents, 'DocumentContentTests')
        task = fixture.agent.create_case('value')
        content = compose(fixture.agent.start(task['id']))
        figures = research_figures(content)
        calculations = {c['id']: c for c in content['calculations']}
        self.assertEqual(set(figures), set(calculations))
        for ref, figure in figures.items():
            self.assertEqual(len(figure['rows']), 1)
            self.assertEqual(figure['rows'][0]['label'], calculations[ref].get('scenario') or '本项情景')
            self.assertEqual(figure['rows'][0]['value'], calculations[ref]['output']['value'])
            self.assertEqual(figure['refs'], calculations[ref]['input_refs'])

    def test_tampered_figure_inputs_are_rejected_before_rendering(self):
        content, _ = self.price_content()
        content['calculations'][0]['inputs']['start_price'] = 99
        with self.assertRaises(ContractError):
            render(content, ROOT)

    def test_reader_identity_is_separate_from_frozen_content_template(self):
        content, _ = self.price_content()
        before = copy.deepcopy(content)
        tree = dom.document_fromstring(render(content, ROOT))
        self.assertEqual(tree.get('data-reader-version'), READER_VERSION)
        self.assertEqual(tree.get('data-renderer-version'), content['template_version'])
        self.assertEqual(content['template_version'], TEMPLATE_VERSION)
        self.assertEqual(content, before)

    def test_chart_source_and_escaping_survive_offline_packaging(self):
        content, _ = self.price_content()
        paper = render(content, ROOT)
        tree = dom.document_fromstring(paper)
        links = tree.xpath('//main//a/@href')
        for item in content['evidence']:
            self.assertIn('#ev-' + item['id'], links)
        self.assertFalse(tree.xpath('//script[@src]'))
        self.assertRegex(paper, r"renderer\s*:\s*['\"]svg['\"]")
        self.assertTrue(tree.xpath('//*[@aria-label]'))
        self.assertIn('new echarts.graphic.LinearGradient', paper)

    def test_index_variant_currency_and_base_are_visible_not_inferred(self):
        fixture = self.fixture(indices, 'IndexChangeWorkflowTests')
        task, host = fixture.prepare('net_total_return', currency='美元')
        content = compose(fixture.agent.start(task['id'], host))
        figure = next(iter(research_figures(content).values()))
        self.assertEqual(figure['basis'], ['净全收益指数', '美元', '基期2004-12-31，基点100'])
        self.assertEqual(figure['unit'], '点')
        self.assertEqual([r['value'] for r in figure['rows']], [1000, 1050])
        paper = render(content, ROOT)
        self.assertIn('净全收益指数，美元，基期2004-12-31，基点100', paper)

    def test_negative_cash_flow_remains_negative_in_the_graph(self):
        fixture = self.fixture(methods, 'MethodDependencyWorkflowTests')
        task, host, _ = earnings.prepare_financial(fixture.agent, 'operating_cash_profit_ratio_v1', cash=-8)
        content = compose(fixture.agent.start(task['id'], host))
        figure = next(iter(research_figures(content).values()))
        self.assertEqual([r['value'] for r in figure['rows']], [-8, 12])
        self.assertLess(figure['result_value'], 0)
        self.assertIn('不是自由现金流', figure['note'])


if __name__ == '__main__':
    unittest.main()
