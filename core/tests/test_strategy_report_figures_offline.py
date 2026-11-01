"""Independent reader tests; public content and closed studies stay immutable."""
import copy
import hashlib
import html
import json
from pathlib import Path
import unittest

from researchhelper.contracts import ContractError
from researchhelper.reports import compose, render, research_figures
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
        return fixture

    def method_content(self, method):
        fixture = self.fixture(methods, 'MethodDependencyWorkflowTests')
        task, host, _ = fixture.prepare(method)
        done = fixture.agent.start(task['id'], host)
        self.assertEqual(done['status'], '已完成', done.get('error'))
        return compose(done)

    def real_content(self):
        dispatch = json.loads((ROOT/'result/acceptance/numeric-contract-live-research/authorization-dispatch.json').read_text())
        report = Path(dispatch['project'])/'result/report.json'
        return json.loads(report.read_text()), report

    def test_real_prices_use_two_exact_observations_not_an_invented_path(self):
        content, _ = self.real_content()
        figures = research_figures(content)
        self.assertEqual(len(figures), 1)
        figure = next(iter(figures.values()))
        self.assertEqual(figure['kind'], 'points')
        self.assertEqual([(row['label'], row['value']) for row in figure['rows']],
                         [('2026-09-29', 4.416), ('2026-09-30', 4.432)])
        self.assertEqual(figure['unit'], '元')
        self.assertEqual(figure['objects'], ['510300.SH'])
        self.assertEqual(figure['sources'], ['ifind'])
        self.assertIn('不表示期间完整路径', figure['note'])

    def test_graph_is_adjacent_to_its_answer_and_only_rendered_once(self):
        content, _ = self.real_content()
        paper = render(content, ROOT)
        self.assertEqual(paper.count("class='research-figure'"), 1)
        self.assertLess(paper.index("class='research-figure'"), paper.index("<section id='facts'"))
        self.assertIn('查看图表数据', paper)
        self.assertIn('同版图表数据', paper)
        self.assertIn("href='#calc_price_change_510300_20260929_30'>计算1</a>", paper)
        self.assertNotIn('>计算calc_price_change_', paper)

    def test_frozen_real_case_is_not_recomputed_or_overwritten(self):
        content, source = self.real_content()
        before = copy.deepcopy(content)
        paths = [source, source.with_suffix('.html')]
        hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
        paper = render(content, ROOT)
        for question in content['questions']:
            self.assertEqual(paper.count(html.escape(question['answer'], quote=True)), 1)
        self.assertEqual(content, before)
        self.assertEqual(hashes, [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths])

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
        content, _ = self.real_content()
        content['calculations'][0]['inputs']['start_price'] = 99
        with self.assertRaises(ContractError):
            render(content, ROOT)

    def test_reader_identity_is_separate_from_frozen_content_template(self):
        content, _ = self.real_content()
        paper = render(content, ROOT)
        self.assertIn("data-reader-version='rh-reader-5'", paper)
        self.assertIn('模板' + content['template_version'], paper)
        self.assertEqual(content['template_version'], 'rh-paper-3')

    def test_chart_source_and_escaping_survive_offline_packaging(self):
        content, _ = self.real_content()
        paper = render(content, ROOT)
        for item in content['evidence']:
            self.assertIn("href='#ev-" + item['id'] + "'", paper)
        self.assertNotIn('<script src=', paper)
        self.assertIn("renderer:'svg'", paper)
        self.assertIn('aria-label=', paper)
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
