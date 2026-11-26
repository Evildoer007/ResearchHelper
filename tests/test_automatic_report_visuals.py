"""Auto selection contracts for both delivered report routes, without network."""
import copy
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tests import ROOT
from researchhelper.visual_selection import select_figures,ledger_html,report_chart_supported
from researchhelper.drafts import render,draft_figures
from researchhelper.draft_visuals import comparison_figures,catalogue_figures,ohlc_figures,logic_figure_plan
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent

SOURCES={'financial':'one-sentence-research-vgjkyxq9','macro':'one-sentence-research-lfu94vrd','quote':'one-sentence-research-_kw2k9ap'}


def actual(kind):
    return json.loads((ROOT/'result/acceptance'/SOURCES[kind]/'report.json').read_text())


def plan(content):
    figures=comparison_figures(content,draft_figures(content['draft']['facts']))
    return select_figures(content,catalogue_figures(content,ohlc_figures(content,figures)))


class AutomaticReportVisualTests(unittest.TestCase):
    def test_saved_actual_financial_candidates_preserve_growth_pairs_and_margin_bridge(self):
        content=actual('financial');before=copy.deepcopy(content);figures=plan(content)
        self.assertEqual([row['auto_visual']['scheme'] for row in figures],
                         ['diverging_bar', 'grouped_bar', 'waterfall', 'grouped_bar'])
        self.assertEqual(figures[0]['auto_visual']['ledger']['rows'][0]['values'][2],content['draft']['calculations'][0]['value'])
        self.assertEqual(content,before)
        bridge=figures[2]['auto_visual']['data']
        self.assertAlmostEqual(bridge['start']+sum(step['value'] for step in bridge['steps']),bridge['end'])
        for row in (figures[1], figures[3]):
            for item in row['auto_visual']['ledger']['rows']:
                d=item['display_derivation']
                self.assertAlmostEqual(d['value'],d['inputs'][1]-d['inputs'][0])
                self.assertEqual(d['unit'],'个百分点' if row['unit']=='%' else row['unit'])

    def test_macro_alignment_preserves_all_27_cells_and_actual_dates(self):
        content=actual('macro');before=copy.deepcopy(content);figures=plan(content)
        self.assertEqual([row['auto_visual']['scheme'] for row in figures],['multi_line','heatmap'])
        left,right=[row['auto_visual']['data'] for row in figures]
        self.assertEqual(right['matrix'],[row['values'] for row in left['series']])
        self.assertEqual(sum(len(row['values']) for row in left['series']),27)
        self.assertEqual(content,before)

    def test_close_only_quote_is_line_not_candles(self):
        figures=plan(actual('quote'));self.assertEqual(len(figures),1)
        self.assertEqual(figures[0]['auto_visual']['scheme'],'line')
        self.assertEqual(len(figures[0]['auto_visual']['data']['values']),5)

    def test_sparse_missing_and_zero_values_remain_honest(self):
        raw=dict(target='test',type='line',dates=['2026-01-01','2026-01-02'],values=[0,None],unit='元')
        fig=select_figures({},[raw])[0]
        self.assertEqual(fig['auto_visual']['scheme'],'lollipop')
        self.assertEqual(fig['auto_visual']['data']['values'],[0,None])
        row=fig['auto_visual']['ledger']['rows'][0]
        self.assertEqual(row['cells'],['0','未返回','0至0'])
        self.assertEqual(raw['values'],[0,None])
        self.assertFalse(report_chart_supported(fig))

    def test_visible_readings_determine_report_chart_eligibility(self):
        for scheme, data, expected in (
            ('grouped_bar', {'series': [{'values': [1, None]}, {'values': [None, 0]}]}, False),
            ('heatmap', {'matrix': [[1, None], [0, 2]]}, True),
            ('lollipop', {'values': [0, 2], 'reference': 0, 'sampleCount': 120}, False),
            ('scatter', {'points': [{'x': 0, 'y': 2}]}, False),
            ('evidence', {'qualitative': True, 'nodes': [], 'links': []}, False),
            ('evidence', {'qualitative': True, 'nodes': [{'id': 'A', 'name': '已核对'}, {'id': 'B', 'name': '推断'}],
                          'links': [{'source': 'A', 'target': 'B'}]}, True),
            ('event_timeline', {'qualitative': True, 'events': [{'date': '2026-08-02'}]}, False),
            ('event_timeline', {'qualitative': True, 'events': [
                {'date': '2026-08-02', 'name': '公告一', 'state': '已公告'},
                {'date': '2026-08-04', 'name': '公告二', 'state': '已公告'}]}, True),
            ('line', {'labels': ['A', 'B', 'C', 'D'], 'values': [1, None, 2, None]}, False),
            ('line', {'labels': ['A', 'B', 'C', 'D', 'E'], 'values': [1, None, 2, None, 3]}, False),
            ('line', {'labels': ['A', 'B', 'C', 'D'], 'values': [1, None, 2, 3]}, True),
            ('area', {'labels': ['A', 'B', 'C'], 'measure': 'scale', 'values': [1, -2, 3]}, False),
            ('dumbbell', {'labels': ['A', 'B'], 'periods': ['甲', '乙'], 'pairs': [[1, 2], [3, None]]}, False),
            ('candlestick', {'labels': ['A', 'B'], 'values': [[1, 3, 4, 2], [1, 3, 4, 2]]}, False),
            ('unregistered', {'labels': ['A', 'B', 'C'], 'values': [1, 2, 3]}, False),
        ):
            with self.subTest(scheme=scheme):
                self.assertEqual(report_chart_supported({'scheme': scheme, 'data': data}), expected)

    def test_retained_quote_reuses_the_exact_chart_and_keeps_all_logics_and_evidence(self):
        from lxml import html
        from researchhelper.report_designer import design

        content = actual('quote')
        before = copy.deepcopy(content)
        for profile in ('standard', 'detailed'):
            with self.subTest(profile=profile):
                report = design(content, ROOT, profile=profile)
                tree = html.fromstring(report['html'])
                plot, = tree.xpath('//main//*[@role="img"]/@id')
                self.assertEqual(tree.xpath('//a[@data-reused-figure]/@href'), ['#'+plot, '#'+plot])
                coverage = report['document']['figure_coverage']
                for argument in ('logic-2', 'logic-3'):
                    self.assertEqual(coverage[argument]['figure_links'], [plot])
                    self.assertEqual(coverage[argument]['data_refs'], coverage['logic-1']['data_refs'])
                self.assertEqual(len(report['document']['dataset']['rows']), 5)
        self.assertEqual(content, before)

    def test_retained_report_uses_same_source_paths_and_sparse_original_tables(self):
        from lxml import html
        from researchhelper.report_designer import design
        cpi = actual('macro')
        for profile in ('standard', 'detailed'):
            merged, = [spec for spec in logic_figure_plan(cpi, report_profile=profile)
                       if spec['paper_logic'] == 'logic-4']
            self.assertEqual(merged['auto_visual']['scheme'], 'multi_line')
            rows = {row['id']: row for row in cpi['draft']['facts']}
            for points, series in zip(merged['series_point_refs'], merged['auto_visual']['data']['series'], strict=True):
                self.assertEqual([rows[ref]['value'] for ref, in points], series['values'])
                self.assertEqual([rows[ref]['period'] for ref, in points], merged['data']['labels'])
        byd = actual('financial')
        result = design(byd, ROOT)
        section = html.document_fromstring(result['html']).get_element_by_id('logic-3')
        self.assertFalse(section.xpath('.//*[@class="chart"]'))
        refs = set(byd['draft']['logics'][2]['data_refs'])
        source_rows = [row for row in byd['draft']['facts'] if row['id'] in refs]
        self.assertTrue(source_rows)
        self.assertTrue(all(row['unit'] == '原始单位待核对' for row in source_rows))
        self.assertFalse(section.xpath('.//tr/@data-observation-id'))
        detailed = design(byd, ROOT, profile='detailed')
        workpaper = html.document_fromstring(detailed['html']).get_element_by_id('logic-3')
        self.assertEqual(set(workpaper.xpath('.//tr/@data-observation-id')), refs)
        self.assertEqual(set(section.xpath('.//tr/@data-calculation-ref')), set(byd['draft']['logics'][2]['calculation_refs']))

    def test_unknown_malformed_and_mixed_units_keep_fallback_without_blocking(self):
        for spec in [dict(name='原表'),dict(scheme='range',data={'ranges':[{'name':'A'}]}),
                     dict(kind='bar',unit='元',rows=[dict(label='A',value=1,unit='%')])]:
            before=copy.deepcopy(spec);result=select_figures({},[spec])
            self.assertEqual(result,[before]);self.assertEqual(spec,before)

    def test_selection_and_html_never_use_network_or_mutate_frozen_content(self):
        with patch.object(socket.socket,'connect',side_effect=AssertionError('No network allowed')):
            for kind in SOURCES:
                from lxml import html as html_tree
                content=actual(kind);before=copy.deepcopy(content)
                for profile in ('standard','detailed'):
                    with self.subTest(kind=kind,profile=profile):
                        paper=render(content,ROOT,report_profile=profile)
                        tree=html_tree.fromstring(paper)
                        charts=tree.xpath('//*[contains(concat(" ",normalize-space(@class)," ")," chart ")]')
                        self.assertTrue(charts)
                        self.assertIn('RHVisuals.bindReports',paper)
                        planned={row['target']:row for row in logic_figure_plan(content, report_profile=profile)}
                        for chart in charts:
                            figure=next(chart.iterancestors('figure'))
                            spec=planned[chart.get('id')]
                            ledgers=figure.xpath('.//*[@class="evidence-ledger"]')
                            self.assertEqual(bool(ledgers), profile=='detailed' and bool(spec['auto_visual'].get('ledger')))
                            basis,=figure.xpath('.//figcaption//a/@href')
                            record=tree.get_element_by_id(basis.removeprefix('#'))
                            if spec.get('kind')=='margin_bridge':
                                self.assertTrue(record.xpath('.//math'))
                                self.assertEqual(set(record.xpath('.//tr/@data-observation-id')),set(spec['data_refs']))
                        self.assertEqual(content,before)
                        self.assertNotIn('src="https://',paper)

    def test_verified_engine_delivery_uses_same_selector(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(socket.socket,'connect',side_effect=AssertionError('No network allowed')):
            agent=ResearchAgent(ResearchArchive(folder),ROOT,offline=True)
            task=agent.create_case('etf');task=agent.start(task['id'])
            self.assertEqual(task['status'],'已完成')
            report=agent.export(task['id'],task['version'])
            self.assertIn('auto_visual',report['html']);self.assertIn('rh-visual-profile',report['html'])
            self.assertIn('evidence-ledger',report['html'])
            self.assertEqual(task['ledger']['used']['model_calls'],0)
            self.assertEqual(task['ledger']['used']['data_requests'],0)

    def test_ledger_escapes_content_and_retains_source_navigation(self):
        spec=select_figures({},[dict(target='x',type='bar',horizontal=True,dates=['<A>'],values=[1],unit='%')])[0]
        html=ledger_html(spec,'<div id="x"></div>','source-1')
        self.assertIn('&lt;A&gt;',html);self.assertIn('href="#source-1"',html)
        self.assertNotIn('>\u003cA\u003e<',html)


if __name__=='__main__':unittest.main()
