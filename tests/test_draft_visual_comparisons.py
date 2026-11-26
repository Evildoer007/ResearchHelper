"""Financial figures use frozen arithmetic, retain data and fail locally."""
import copy
import hashlib
import json
import unittest
from decimal import Decimal
from lxml import html as html_parser
from tests import ROOT
from researchhelper.contracts import fingerprint
from researchhelper.drafts import draft_figures,render
from researchhelper.draft_visuals import ARGUMENT_VISUAL_SCOPE_VERSION,comparison_figures,comparison_table,logic_figure_plan
from researchhelper.report_designer import design

SOURCE=ROOT/'result/acceptance/one-sentence-research-vgjkyxq9/report.json'
HK=ROOT/'result/acceptance/one-sentence-research-_kw2k9ap/report.json'


class DraftVisualComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hashes={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in (SOURCE,HK)}
        cls.content=json.loads(SOURCE.read_text());cls.hk=json.loads(HK.read_text())

    @classmethod
    def tearDownClass(cls):
        assert all(hashlib.sha256(p.read_bytes()).hexdigest()==s for p,s in cls.hashes.items())

    def figures(self,content):
        return comparison_figures(content,draft_figures(content['draft']['facts']))

    def test_comparisons_keep_three_metrics_and_margin_attribution(self):
        figures=self.figures(self.content)
        self.assertEqual([row['kind'] for row in figures], ['changes', 'paired', 'margin_bridge', 'paired'])
        bridge = figures[2]
        data = bridge['data']
        self.assertEqual(bridge['scheme'], 'waterfall')
        self.assertAlmostEqual(data['start'] + sum(step['value'] for step in data['steps']), data['end'])

    def test_growth_is_the_five_actual_registered_changes_without_rounding_source_values(self):
        figure=self.figures(self.content)[0]
        source={row['id']:row for row in self.content['draft']['calculations']}
        self.assertEqual(figure['values'],[source[ref]['value'] for ref in figure['calculation_refs']])
        self.assertEqual(figure['dates'],['营业收入','营业成本','净利润','归母净利润','经营现金流'])
        self.assertTrue(any(value<0 for value in figure['values']))
        self.assertTrue(any(value>0 for value in figure['values']))

    def test_profit_ratios_and_periods_are_independent_literal_endpoints(self):
        figure=self.figures(self.content)[1]
        self.assertEqual(figure['periods'],['20250630','20260630'])
        self.assertEqual([[round(v,2) for v in pair] for pair in figure['pairs']],[[18.01,18.85],[4.32,3.58]])
        self.assertEqual(figure['unit'],'%')

    def test_cash_coverage_is_a_multiple_not_a_cash_collection_claim(self):
        figure, = [row for row in self.figures(self.content) if row.get('unit') == '倍']
        self.assertEqual([round(v,2) for v in figure['pairs'][0]],[1.98,3.03])
        self.assertEqual(figure['unit'],'倍')
        self.assertNotIn('回款改善',figure['name'])

    def test_render_keeps_all_ten_original_values_and_the_frozen_prose(self):
        content=copy.deepcopy(self.content);before=copy.deepcopy(content);html=render(content,ROOT)
        self.assertEqual(content,before)
        tree=html_parser.document_fromstring(html)
        cells=tree.get_element_by_id('complete-report-data').xpath('.//td[@data-raw-value]')
        self.assertEqual(len(cells),10)
        self.assertEqual({cell.get('data-raw-value'):Decimal(cell.text_content().replace(',','')) for cell in cells},
            {row['id']:Decimal(str(row['raw_value'])) for row in content['draft']['facts']})
        self.assertIn(content['draft']['conclusion'],html)
        self.assertTrue(all(row['analysis'] in html for row in content['draft']['logics']))
        self.assertEqual(len(tree.xpath('//main//section[starts-with(@id,"logic-")]')),len(content['draft']['logics']))
        self.assertNotIn('<details',html)

    def test_input_objects_are_never_mutated_by_the_visual_layer(self):
        content=copy.deepcopy(self.content);before=copy.deepcopy(content);self.figures(content)
        self.assertEqual(before,content)

    def test_calculation_with_changed_result_is_not_plotted(self):
        content=copy.deepcopy(self.content);content['draft']['calculations'][0].update(value=999,display='999.00')
        figures=self.figures(content)
        self.assertFalse(any(999 in row.get('values',[]) for row in figures))
        self.assertFalse(any('A1' in row.get('calculation_refs',[]) for row in figures))

    def test_unregistered_methods_or_new_method_versions_are_not_executed(self):
        for method,version in [('unknown',1),('raw_change_v1',999)]:
            content=copy.deepcopy(self.content);content['draft']['calculations'][0].update(method=method,method_version=version)
            self.assertFalse(any('A1' in row.get('calculation_refs',[]) for row in self.figures(content)))

    def test_foreign_sources_produce_no_derived_figures(self):
        content=copy.deepcopy(self.content)
        for row in content['source_records']:row['task_id']='foreign'
        self.assertFalse(any(row.get('kind') for row in self.figures(content)))

    def test_missing_raw_input_keeps_the_rest_of_the_report(self):
        content=copy.deepcopy(self.content);content['draft']['facts'][0]['value']=None
        figures=self.figures(content)
        self.assertFalse(any('A1' in row.get('calculation_refs',[]) for row in figures))
        self.assertTrue(any(row.get('kind')=='paired' and row['unit']=='倍' for row in figures))

    def test_unknown_unit_requires_the_saved_same_scale_condition(self):
        content=copy.deepcopy(self.content)
        for row in content['draft']['calculations']:row['conditional_equal_scale']=False
        self.assertFalse(any(row.get('kind') for row in self.figures(content)))

    def test_inputs_cannot_be_silently_replaced_by_different_raw_values(self):
        content=copy.deepcopy(self.content);content['draft']['calculations'][0]['inputs']['a']=1
        self.assertFalse(any('A1' in row.get('calculation_refs',[]) for row in self.figures(content)))

    def test_comparison_does_not_mix_different_statement_bases(self):
        content=copy.deepcopy(self.content)
        for row in content['draft']['facts']:
            if row['period']=='20250630':row['series_basis']='other_statement'
        self.assertFalse(any(row.get('kind') for row in self.figures(content)))

    def test_unrepresented_additional_raw_period_is_preserved(self):
        content=copy.deepcopy(self.content)
        extra=dict(content['draft']['facts'][0],id='extra',period='20240630')
        content['draft']['facts'].append(extra)
        figures=self.figures(content)
        self.assertTrue(any('extra' in row.get('data_refs',[]) for row in figures))

    def test_legacy_draft_without_registered_results_keeps_raw_figures(self):
        content=copy.deepcopy(self.content);content['draft'].pop('calculations')
        self.assertEqual(len(self.figures(content)),5)
        self.assertFalse(any(row.get('kind') for row in self.figures(content)))

    def test_hk_quote_keeps_the_real_line_and_five_points(self):
        figures=self.figures(self.hk)
        self.assertEqual(len(figures),1);self.assertEqual(figures[0]['type'],'line')
        self.assertEqual(len(figures[0]['values']),5)

    def test_unknown_money_units_are_not_converted_into_a_currency(self):
        figures=self.figures(self.content)
        self.assertEqual({row['unit'] for row in figures},{'%','倍'})

    def test_compact_table_keeps_every_original_value_and_both_periods(self):
        table=comparison_table(self.content)
        self.assertEqual(table['observations'],10)
        self.assertEqual(table['periods'],['2025-06-30','2026-06-30'])
        rows=[item for row in table['rows'] for item in row['observations']]
        self.assertEqual({row['id']:row['raw_value'] for row in rows},
                         {row['id']:row['raw_value'] for row in self.content['draft']['facts']})

    def test_incomplete_or_mixed_unit_table_uses_full_original_layout(self):
        for mixed in (False,True):
            content=copy.deepcopy(self.content)
            if mixed:content['draft']['facts'][0]['unit']='元'
            else:content['draft']['facts'].pop()
            self.assertIsNone(comparison_table(content))

    def test_derived_caption_opens_the_existing_evidence_surface(self):
        html=render(self.content,ROOT)
        tree=html_parser.document_fromstring(html)
        targets=tree.xpath('//*[@id][starts-with(@id,"draft-figure-basis-logic-")]/@id')
        self.assertTrue(targets)
        self.assertTrue(all(tree.xpath('//a[@href="#'+target+'"]') for target in targets))
        self.assertIn('<math xmlns=',html)
        self.assertTrue(all(tree.get_element_by_id(target).get('data-inspection-title') for target in targets))

    def test_cash_and_profit_keep_four_observations_without_a_redundant_plot(self):
        content = copy.deepcopy(self.content)
        content['draft']['argument_visual_scope_version'] = ARGUMENT_VISUAL_SCOPE_VERSION
        facts = {row['id']: row for row in content['draft']['facts']}
        changes = [row for row in content['draft']['calculations'] if row['method'] == 'raw_change_v1'
                   and facts[row['data_refs'][0]]['field'] in ('ths_ncf_from_oa_stock', 'ths_np_atoopc_stock')]
        self.assertEqual(len(changes), 2)
        refs = [ref for row in changes for ref in row['data_refs']]
        content['draft']['logics'] = [dict(title='经营质量分化', analysis='两项经营读数变动的幅度不同。',
                                          implication='', risk='', source_refs=[], knowledge_refs=[],
                                          data_refs=refs, calculation_refs=[row['id'] for row in changes])]
        content.pop('content_hash')
        content['content_hash'] = fingerprint(content)
        for profile in ('standard', 'detailed'):
            with self.subTest(profile=profile):
                self.assertEqual(logic_figure_plan(content, report_profile=profile), [])
                paper = design(content, ROOT, profile=profile)
                coverage = paper['document']['figure_coverage']['logic-1']
                self.assertEqual(set(coverage['data_refs']), set(refs))
                self.assertEqual(coverage['status'], 'numeric_text' if profile == 'standard' else 'numeric_table')
                section = html_parser.document_fromstring(paper['html']).get_element_by_id('logic-1')
                for calculation in changes:
                    self.assertIn(calculation['display'] + '%', section.text_content())


if __name__=='__main__':unittest.main()
