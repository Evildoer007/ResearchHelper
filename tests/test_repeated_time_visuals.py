"""Repeated paths become endpoint comparisons without losing original context."""
from copy import deepcopy
import socket,unittest
from unittest.mock import patch
from lxml import html
from tests import ROOT
from tests.test_macro_argument_visuals import fixture
from researchhelper.contracts import canonical,fingerprint
from researchhelper.draft_visuals import logic_figure_plan,_repeated_endpoint_views
from researchhelper.report_designer import design


def sample():
    content=fixture();content['draft']['logics'][3]['data_refs']=['D1','D4','D5','D8','D9','D12']
    content['draft']['visual_layout_version']=1
    return seal(content)


def seal(content):
    content['content_hash']=fingerprint({key:value for key,value in content.items() if key!='content_hash'})
    return content


def facts(content):return {row['id']:row for row in content['draft']['facts']}
def last(specs):return [row for row in specs if row.get('paper_logic')=='logic-4']


class RepeatedTimeVisualTests(unittest.TestCase):
    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No external I/O'))
        guard.start();self.addCleanup(guard.stop)

    def test_evidence_matrix_stays_full_width_below_prose_and_chart(self):
        from researchhelper.report_designer import _group_figures
        section=html.fromstring('<section><h2>判断</h2><p>利润与现金流应联合观察。</p><div class="report-data-scroll"><table><tr><th>指标</th><th>期间</th><th>单位</th><th>来源</th></tr><tr><td>经营现金流</td><td>2025</td><td>元</td><td>iFinD</td></tr></table></div><figure><div class="chart" id="plot"></div></figure></section>')
        before=html.tostring(section.xpath('.//table')[0])
        _group_figures(section,{'plot':{'kind':'changes','dates':['2024','2025'],'values':[1,2]}},1)
        grid=section.xpath('./div[@class="logic-reading-grid"]')[0]
        self.assertFalse(grid.xpath('.//table'))
        self.assertTrue(grid.xpath('.//p'));self.assertTrue(grid.xpath('.//*[@id="plot"]'))
        table=section.xpath('.//table')[0]
        self.assertEqual(table.getparent().getparent(),section)
        self.assertGreater(list(section).index(table.getparent()),list(section).index(grid))
        self.assertEqual(html.tostring(table),before)

    def test_three_repeated_paths_become_one_exact_endpoint_view(self):
        content=sample();before=canonical(content);specs=logic_figure_plan(content)
        self.assertEqual(len(specs),4);self.assertEqual(len(last(specs)),1)
        combined=last(specs)[0]
        self.assertEqual(combined['auto_visual']['scheme'],'dumbbell')
        self.assertEqual(combined['pairs'],[[1.0,.9],[-1.6,-1.7],[1.1,1.0]])
        self.assertEqual(combined['periods'],['2026-06-30','2026-09-30'])
        self.assertEqual(combined['point_refs'],[['D1','D4'],['D5','D8'],['D9','D12']])
        self.assertEqual(combined['auto_visual']['ledger']['columns'],['指标','2026-06-30','2026-09-30'])
        self.assertTrue(all(len(row['cells'])==2 for row in combined['auto_visual']['ledger']['rows']))
        self.assertEqual(canonical(content),before)

    def test_first_complete_paths_and_missing_points_remain_visible(self):
        content=sample();modern=logic_figure_plan(content);legacy=deepcopy(content);legacy['draft'].pop('visual_layout_version')
        old=logic_figure_plan(legacy)
        self.assertEqual([row['values'] for row in modern[:3]],[row['values'] for row in old[:3]])
        self.assertIsNone(modern[0]['values'][1])
        self.assertEqual({ref for row in modern for ref in row.get('data_refs',[])},{ref for row in old for ref in row.get('data_refs',[])})
        self.assertEqual(len(last(old)),3)

    def test_middle_point_argument_keeps_its_complete_path(self):
        content=sample();content['draft']['logics'][3]['data_refs'].append('D3');result=last(logic_figure_plan(content))
        line=next(row for row in result if row.get('type')=='line')
        self.assertIn('D3',line['data_refs']);self.assertEqual(len(line['dates']),4)
        paired=next(row for row in result if row.get('kind')=='paired')
        self.assertNotIn('CPI:当月同比',paired['dates'])

    def test_one_repeated_path_is_not_replaced_by_a_sparse_chart(self):
        content=sample();content['draft']['logics'][3]['data_refs']=['D1','D4'];result=last(logic_figure_plan(content))
        self.assertEqual(len(result),1);self.assertEqual(result[0]['auto_visual']['scheme'],'line')
        self.assertEqual(len(result[0]['dates']),4)

    def test_unknown_or_legacy_policy_keeps_original_layout(self):
        for version in (None,0,2,True,'1'):
            content=sample()
            if version is None:content['draft'].pop('visual_layout_version')
            else:content['draft']['visual_layout_version']=version
            with self.subTest(version=version):self.assertEqual(len(last(logic_figure_plan(content))),3)

    def test_unknown_units_do_not_form_a_comparison(self):
        content=sample();content['draft'].pop('visual_layout_version');specs=logic_figure_plan(content);rows=facts(content)
        for spec in specs:spec['unit']='原始单位待核对'
        for row in rows.values():row['unit']='原始单位待核对'
        result=_repeated_endpoint_views(specs,rows)
        self.assertEqual(len(last(result)),3);self.assertFalse(any(row.get('reused_time_context') for row in result))

    def test_equal_values_from_different_sources_are_not_reused(self):
        content=sample();content['draft'].pop('visual_layout_version');specs=logic_figure_plan(content);rows=facts(content)
        for spec in last(specs):
            remap={ref:'other-'+ref for ref in spec['data_refs']}
            for previous,new in remap.items():rows[new]=dict(rows[previous],id=new,artifact_id='other-source')
            spec['data_refs']=[remap[ref] for ref in spec['data_refs']]
            spec['point_refs']=[[remap[ref] for ref in cell] for cell in spec['point_refs']]
            spec['argument_data_refs']=[remap.get(ref,ref) for ref in spec['argument_data_refs']]
        self.assertEqual(len(last(_repeated_endpoint_views(specs,rows))),3)

    def test_different_value_or_range_keeps_the_affected_original(self):
        for change in ('value','range'):
            content=sample();content['draft'].pop('visual_layout_version');specs=logic_figure_plan(content);target=last(specs)[0]
            if change=='value':target['values'][0]=999
            else:target['dates'][0]='2026-05-31'
            with self.subTest(change=change):
                result=_repeated_endpoint_views(specs,facts(content))
                self.assertTrue(any(row['target']==target['target'] and row.get('type')=='line' for row in result))

    def test_each_reused_endpoint_retains_its_previously_visible_full_source(self):
        content=sample();specs=logic_figure_plan(content);targets={row['target']:row for row in specs};combined=last(specs)[0]
        self.assertEqual(len(combined['reused_time_context']),3)
        for mapping in combined['reused_time_context']:
            original=targets[mapping['source_figure']]
            self.assertEqual(original['data_refs'],mapping['full_data_refs'])
            self.assertEqual([original['point_refs'][0][0],original['point_refs'][-1][0]],mapping['endpoint_refs'])

    def test_two_report_profiles_keep_all_prose_and_bound_point_inspection(self):
        content=sample();before=canonical(content)
        for profile in ('standard','detailed'):
            result=design(content,ROOT,profile=profile);tree=html.document_fromstring(result['html']);doc=result['document']
            with self.subTest(profile=profile):
                self.assertEqual(len([block for block in doc['blocks'] if block['kind']=='chart']),4)
                self.assertEqual(doc['figure_coverage']['logic-4']['figures'],1)
                self.assertEqual(doc['figure_coverage']['logic-4']['schemes'],['dumbbell'])
                self.assertEqual(len(doc['dataset']['rows']),12)
                for logic in content['draft']['logics']:self.assertIn(logic['analysis'],tree.xpath('//main')[0].text_content())
                self.assertTrue(tree.xpath('//*[@id="draft-figure-basis-logic-4-chart-1"]'))
                self.assertFalse(tree.xpath('//main//details|//script[@src]|//link[@href]'))
        self.assertEqual(canonical(content),before)

    def test_detailed_basis_stays_full_width_and_is_not_duplicated_under_the_chart(self):
        content=sample();result=design(content,ROOT,profile='detailed');tree=html.document_fromstring(result['html'])
        section=tree.get_element_by_id('logic-4')
        self.assertEqual(len(section.xpath('./div[@class="logic-reading-grid"]')),1)
        detail=section.xpath('./div[@class="logic-detail"]')[0]
        self.assertFalse(detail.xpath('ancestor::*[@class="logic-reading-grid"]'))
        self.assertFalse(section.xpath('.//figure//*[contains(concat(" ",normalize-space(@class)," ")," evidence-ledger ")]'))
        self.assertFalse(section.xpath('.//figure/p[@class="figure-context"]'))
        data=detail.xpath('.//table')[0];self.assertEqual(len(data.xpath('./tbody/tr')),6)
        legacy=deepcopy(content);legacy['draft'].pop('visual_layout_version');seal(legacy)
        old=html.document_fromstring(design(legacy,ROOT,profile='detailed')['html']).get_element_by_id('logic-4')
        self.assertEqual(len(old.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," evidence-ledger ")]')),3)
        self.assertFalse(old.xpath('./div[@class="logic-reading-grid"]'))

    def test_reused_comparison_supports_existing_style_choices(self):
        content=sample();result=design(content,ROOT,visual_profile=dict(theme='research',schemes={'logic-4-chart-1':'slope'}))
        chart=next(block['spec'] for block in result['document']['blocks'] if block['kind']=='chart' and block['spec']['target']=='logic-4-chart-1')
        self.assertEqual(chart['auto_visual']['scheme'],'slope')
        self.assertEqual(chart['pairs'],[[1.0,.9],[-1.6,-1.7],[1.1,1.0]])
        self.assertEqual(chart['point_refs'],[['D1','D4'],['D5','D8'],['D9','D12']])


if __name__=='__main__':unittest.main()
