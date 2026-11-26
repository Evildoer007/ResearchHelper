"""Actual report ledger names and literal source data, with no supplier calls."""
import copy
import unittest
from lxml import html
from tests import ROOT
from tests import test_method_dependency_workflow_offline as fixture
from tests import test_fee_source_units_offline as fee_fixtures
from researchhelper.reports import compose, render, research_figures
from researchhelper.visual_selection import ledger_html


class ReportLedgerAccessibilityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.MethodDependencyWorkflowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.agent.close_runtime)

    def test_each_sparse_table_has_its_measurement_name_and_original_data(self):
        for method in ('observed_price_return_v1', 'etf_nav_premium_v1', 'gross_margin_v1'):
            with self.subTest(method=method):
                task, host, _ = self.fixture.prepare(method)
                complete = self.fixture.agent.start(task['id'], host)
                self.assertEqual(complete['status'], '已完成', complete.get('error'))
                content = compose(complete)
                saved = copy.deepcopy(self.fixture.agent.export(task['id']))
                before = copy.deepcopy(content)
                tree = html.document_fromstring(render(content, ROOT, report_profile='detailed'))
                figure = next(iter(research_figures(content).values()))
                ledgers = tree.xpath('//main//figure[@data-calculation-ref="calc_main"][@data-presentation="table"]//table')
                self.assertEqual(len(ledgers), 1)
                self.assertIn(figure['title'], ledgers[0].get('aria-label', ''))
                self.assertTrue(ledgers[0].xpath('./thead//th[@scope="col"]'))
                texts = ledgers[0].xpath('./tbody/tr/td/@data-raw-value')
                for evidence in complete['evidence']:
                    self.assertIn(str(evidence['raw_value']), texts)
                self.assertEqual(content, before)
                self.assertEqual(self.fixture.agent.export(task['id']), saved)

    def test_ledger_name_escapes_literal_title_without_adding_visible_copy(self):
        title = '费用"与<原值>&比较'
        spec = dict(title=title, auto_visual=dict(scheme='lollipop', ledger=dict(
            columns=['对象','原值'], rows=[dict(label='合成行', cells=['12.5'], values=[12.5])], unit='倍')))
        markup = ledger_html(spec, '<div class="chart"></div>')
        tree = html.fromstring(markup)
        table = tree.find('.//table')
        self.assertEqual(table.get('aria-label'), title + '原始数据')
        self.assertFalse(table.xpath('.//caption|.//script|.//原值'))
        self.assertEqual(table.xpath('./tbody/tr/td/text()'), ['12.5'])

    def test_sparse_cash_amounts_keep_original_units_in_both_profiles(self):
        task,host,_=self.fixture.prepare('gross_margin_v1')
        complete=self.fixture.agent.start(task['id'],host)
        self.assertEqual(complete['status'],'已完成',complete.get('gaps'))
        content=compose(complete)
        saved=copy.deepcopy(self.fixture.agent.export(task['id']))
        before=copy.deepcopy(content)
        for profile in ('standard','detailed'):
            with self.subTest(profile=profile):
                tree=html.document_fromstring(render(content,ROOT,report_profile=profile))
                figure=tree.xpath('//main//figure[@data-calculation-ref="calc_main"]')[0]
                self.assertFalse(figure.xpath('.//*[@role="img"]'))
                if profile == 'standard':
                    self.assertEqual(figure.get('data-presentation'),'inline')
                    self.assertEqual(figure.xpath('.//span[@data-raw-value]/@data-raw-value'),['100','60'])
                    target = figure.xpath('./figcaption/a/@href')[0][1:]
                    table = tree.get_element_by_id(target).find('.//table')
                else:
                    self.assertEqual(figure.get('data-presentation'),'table')
                    table = figure.find('.//table')
                self.assertIn('单位百万元',table.get('aria-label'))
                self.assertNotIn('图示单位亿元',table.get('aria-label'))
                self.assertEqual(table.xpath('./tbody/tr/td[@data-raw-value]/@data-raw-value'),['100','60'])
                self.assertEqual(table.xpath('./tbody/tr/td[@data-raw-value]/text()'),['100百万元','60百万元'])
                self.assertIn('单位百万元',figure.text_content())
        self.assertEqual(content,before)
        self.assertEqual(self.fixture.agent.export(task['id']),saved)

    def test_unnamed_legacy_spec_has_a_factual_fallback_name(self):
        spec = dict(auto_visual=dict(scheme='lollipop', ledger=dict(
            columns=['对象','原值'], rows=[dict(label='合成行',cells=['0'],values=[0])], unit='')))
        table = html.fromstring(ledger_html(spec, '<div class="chart"></div>')).find('.//table')
        self.assertEqual(table.get('aria-label'), '图表原始数据')
        self.assertEqual(table.xpath('./tbody/tr/td/text()'), ['0'])

    def test_fee_plot_frequency_does_not_replace_original_row_units(self):
        case=fee_fixtures.FeeSourceUnitsTests();case.setUp()
        self.addCleanup(case.doCleanups);self.addCleanup(case.agent.close_runtime)
        variants=[(None,['%']*4,'单位%'),
                  ({'management_a','custody_b'},['%','%/年','%/年','%'],'各项单位%、%/年'),
                  (set(),['%/年']*4,'单位%/年')]
        for fields,units,caption in variants:
            task,host=case.prepare(fields);done=case.agent.start(task['id'],host)
            self.assertEqual(done['status'],'已完成',done.get('gaps'))
            content=compose(done);before=copy.deepcopy(content)
            saved=copy.deepcopy(case.agent.export(task['id']))
            figure=next(iter(research_figures(content).values()))
            self.assertEqual(figure['unit'],'%/年')
            self.assertEqual([row['unit'] for row in figure['rows']],units)
            for profile in ('standard','detailed'):
                with self.subTest(units=units,profile=profile):
                    tree=html.document_fromstring(render(content,ROOT,report_profile=profile))
                    charts=tree.xpath('//main//figure[@data-calculation-ref]//*[@role="img"]')
                    self.assertEqual(len(charts),1)
                    self.assertIn('；单位%/年',charts[0].get('aria-label'))
                    detail=tree.get_element_by_id(charts[0].get('aria-details'))
                    table=detail.find('.//table')
                    self.assertIn('；'+caption+'；',table.get('aria-label'))
                    self.assertEqual(table.xpath('./tbody/tr/td[@data-raw-value]/text()'),
                        [str(row['value'])+unit for row,unit in zip(figure['rows'],units)])
            self.assertEqual(content,before)
            self.assertEqual(case.agent.export(task['id']),saved)
