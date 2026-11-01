"""Auto selection contracts for both delivered report routes, without network."""
import copy
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tests import ROOT
from researchhelper.visual_selection import select_figures,ledger_html
from researchhelper.drafts import render,draft_figures
from researchhelper.draft_visuals import comparison_figures,catalogue_figures,ohlc_figures
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent

SOURCES={'financial':'one-sentence-research-vgjkyxq9','macro':'one-sentence-research-lfu94vrd','quote':'one-sentence-research-_kw2k9ap'}


def actual(kind):
    return json.loads((ROOT/'result/acceptance'/SOURCES[kind]/'report.json').read_text())


def plan(content):
    figures=comparison_figures(content,draft_figures(content['draft']['facts']))
    return select_figures(content,catalogue_figures(content,ohlc_figures(content,figures)))


class AutomaticReportVisualTests(unittest.TestCase):
    def test_saved_actual_financial_chooses_growth_and_two_pair_comparisons(self):
        content=actual('financial');before=copy.deepcopy(content);figures=plan(content)
        self.assertEqual([row['auto_visual']['scheme'] for row in figures],['diverging_bar','dumbbell','dumbbell'])
        self.assertEqual(figures[0]['auto_visual']['ledger']['rows'][0]['values'][2],content['draft']['calculations'][0]['value'])
        self.assertEqual(content,before)
        for row in figures[1:]:
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

    def test_unknown_malformed_and_mixed_units_keep_fallback_without_blocking(self):
        for spec in [dict(name='原表'),dict(scheme='range',data={'ranges':[{'name':'A'}]}),
                     dict(kind='bar',unit='元',rows=[dict(label='A',value=1,unit='%')])]:
            before=copy.deepcopy(spec);result=select_figures({},[spec])
            self.assertEqual(result,[before]);self.assertEqual(spec,before)

    def test_selection_and_html_never_use_network_or_mutate_frozen_content(self):
        with patch.object(socket.socket,'connect',side_effect=AssertionError('No network allowed')):
            for kind in SOURCES:
                content=actual(kind);before=copy.deepcopy(content);html=render(content,ROOT)
                self.assertIn('data-auto-scheme=',html);self.assertIn('class="evidence-ledger"',html)
                self.assertEqual(content,before)
                self.assertNotIn('src="https://',html)

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
