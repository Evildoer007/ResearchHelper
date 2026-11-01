"""Render a completed real study without changing its frozen research."""
import copy
import hashlib
import json
import unittest
from pathlib import Path

from tests import ROOT
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.layout import default_resource_root
from researchhelper.reports import render, compose, METHOD_NOTATION
from researchhelper.visual import research_figures
from researchhelper import visual

STUDY = ROOT/'result/acceptance/one-sentence-research-aq8vm90m'


class FeeSummaryReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = {name:hashlib.sha256((STUDY/name).read_bytes()).hexdigest()
            for name in ('task.json','receipt.json','report.json','report.html')}
        cls.content = json.loads((STUDY/'report.json').read_text())
        origin = getattr(visual,'__frozen_origin__',None)
        cls.resources = (Path(origin.split('!/PYZ.pyz/',1)[0]).parents[2]
                         if origin else default_resource_root())

    @classmethod
    def tearDownClass(cls):
        assert all(hashlib.sha256((STUDY/name).read_bytes()).hexdigest()==value
                   for name,value in cls.original.items())

    def figure(self):
        figures = research_figures(self.content)
        self.assertEqual(len(figures),1)
        return next(iter(figures.values()))

    def test_real_summary_method_has_one_registered_chart(self):
        figure = self.figure()
        self.assertEqual(figure['presentation'],'chart')
        self.assertEqual(figure['categories'],['管理费','托管费','两项合计'])

    def test_components_retain_their_own_original_bindings(self):
        figure = self.figure()
        calculation = self.content['calculations'][0]
        keys = ('management_a','custody_a','management_b','custody_b')
        self.assertEqual([row['ref'] for row in figure['rows'][:4]],
                         [calculation['input_bindings'][key] for key in keys])
        self.assertEqual([row['value'] for row in figure['rows'][:4]],
                         [calculation['inputs'][key] for key in keys])

    def test_totals_are_actual_outputs_not_source_disclosures(self):
        figure = self.figure()
        output = self.content['calculations'][0]['output']
        self.assertEqual([row['value'] for row in figure['rows'][4:]], [output['total_a'],output['total_b']])
        self.assertEqual([row['ref'] for row in figure['rows'][4:]], [None,None])
        self.assertEqual(figure['result_value'],output['value'])
        self.assertEqual(figure['result_unit'],'百分点/年')

    def test_groups_do_not_mix_two_funds_or_add_difference_to_rate_axis(self):
        figure = self.figure()
        self.assertEqual([group['indices'] for group in figure['groups']],[[0,1,4],[2,3,5]])
        self.assertEqual(len(figure['rows']),6)
        self.assertEqual(figure['unit'],'%/年')

    def test_render_is_self_contained_with_no_client_accordions(self):
        page = render(self.content,self.resources)
        self.assertIn("class='chart chart-grouped'",page)
        self.assertIn("echarts.init",page)
        self.assertTrue('Licensed to the Apache Software Foundation' in page)
        self.assertNotIn('<script src=',page)
        self.assertNotIn('<details',page)

    def test_calculated_tooltip_and_math_remain_distinct_from_original_numbers(self):
        page = render(self.content,self.resources)
        self.assertIn("row.ref?'原值：':'计算值：'",page)
        self.assertIn('<msub><mi>T</mi><mi>A</mi></msub>',page)
        self.assertIn('合计不是原件直接披露值',METHOD_NOTATION['management_custody_fee_summary_v1'][2])

    def test_render_does_not_rewrite_content_or_invent_history(self):
        before = copy.deepcopy(self.content)
        page = render(self.content,self.resources)
        self.assertEqual(before,self.content)
        self.assertNotIn("type:'line'",page.split('const figures=',1)[1].split('const signed=',1)[0])
        self.assertEqual(self.figure()['kind'],'bar')

    def test_modified_frozen_total_is_rejected_before_render(self):
        # A renderer checks frozen-content integrity, not a newly fabricated
        # hash or a rerun of financial/source approval against old material.
        content = copy.deepcopy(self.content)
        content['calculations'][0]['output']['total_a'] = .9
        with self.assertRaisesRegex(ContractError,'冻结报告内容已变化'):
            render(content,self.resources)


if __name__=='__main__': unittest.main()
