"""Repeated paths become endpoint comparisons without losing original context."""
from copy import deepcopy
import socket,unittest
from unittest.mock import patch
from lxml import html
from tests import ROOT
from tests.test_macro_argument_visuals import fixture
from researchhelper.contracts import canonical,fingerprint
from researchhelper.draft_visuals import logic_figure_plan,_repeated_endpoint_views,_share_identical_figures
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
        self.assertFalse(last(old))
        self.assertTrue(all(row['reused_by'] == ['logic-4'] for row in old))

    def test_middle_point_argument_links_its_complete_path_and_keeps_its_own_anchors(self):
        content=sample();content['draft']['logics'][3]['data_refs'].append('D3')
        result=logic_figure_plan(content)
        line, = [row for row in result if 'logic-4' in row.get('reused_by', [])]
        self.assertIn('D3',line['data_refs']);self.assertEqual(len(line['dates']),4)
        self.assertEqual(line['plot_context']['context_data_refs'], ['D2', 'D3'])
        self.assertEqual(line['reused_context_data_refs']['logic-4'], ['D2'])
        paired, = last(result)
        self.assertNotIn('CPI:当月同比',paired['dates'])

    def test_one_repeated_path_is_not_replaced_by_a_sparse_chart(self):
        content=sample();content['draft']['logics'][3]['data_refs']=['D1','D4']
        result=logic_figure_plan(content)
        self.assertFalse(last(result))
        common, = [row for row in result if 'logic-4' in row.get('reused_by',[])]
        self.assertEqual(common['auto_visual']['scheme'],'line')
        self.assertEqual(len(common['dates']),4)

    def test_unknown_or_legacy_policy_references_original_paths(self):
        for version in (None,0,3,True,'1'):
            content=sample()
            if version is None:content['draft'].pop('visual_layout_version')
            else:content['draft']['visual_layout_version']=version
            with self.subTest(version=version):
                specs = logic_figure_plan(content)
                self.assertEqual(len(specs), 3)
                self.assertFalse(last(specs))
                self.assertTrue(all(row['reused_by'] == ['logic-4'] for row in specs))
                self.assertFalse(any(row.get('reused_time_context') for row in specs))

    def test_unknown_units_do_not_form_a_comparison(self):
        content=sample();specs=logic_figure_plan(content)[:3];rows=facts(content)
        for spec in specs:spec['unit']='原始单位待核对'
        for row in rows.values():row['unit']='原始单位待核对'
        sources = {source['id']: source for source in content['source_records']}
        result = _repeated_endpoint_views(specs, deepcopy(specs), rows, sources, set(content['draft']['logics'][3]['data_refs']))
        self.assertEqual(result, specs)
        self.assertFalse(any(row.get('reused_time_context') for row in result))

    def test_equal_values_from_different_sources_are_not_reused(self):
        content=sample();specs=logic_figure_plan(content)[:3];baseline=deepcopy(specs);rows=facts(content)
        sources = {source['id']: source for source in content['source_records']}
        sources['other-source'] = dict(sources['macro-original'], id='other-source')
        wanted = set(content['draft']['logics'][3]['data_refs'])
        remapped = set()
        for spec in specs:
            remap={ref:'other-'+ref for ref in spec['data_refs']}
            for previous,new in remap.items():rows[new]=dict(rows[previous],id=new,artifact_id='other-source')
            spec['data_refs']=[remap[ref] for ref in spec['data_refs']]
            spec['point_refs']=[[remap[ref] for ref in cell] for cell in spec['point_refs']]
            remapped.update(remap[ref] for ref in wanted.intersection(remap))
        result = _repeated_endpoint_views(baseline, specs, rows, sources, remapped)
        self.assertEqual(result, specs)
        self.assertEqual(_share_identical_figures([baseline[0], specs[0]]), [baseline[0], specs[0]])

    def test_different_value_or_range_keeps_the_affected_original(self):
        first = logic_figure_plan(sample())[0]
        for change in ('value','range','source'):
            target=dict(deepcopy(first),paper_logic='logic-4',target='logic-4-chart-1')
            data=target['auto_visual']['data']
            if change=='value':data['values'][0]=999
            elif change=='range':data['labels'][0]='2026-05-31'
            else:target['data_refs']=['other-'+ref for ref in target['data_refs']]
            with self.subTest(change=change):
                self.assertEqual(_share_identical_figures([deepcopy(first),target]),[first,target])

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
        self.assertEqual(old.xpath('.//a[@data-reused-figure]/@href'), [f'#logic-{index}-chart-1' for index in (1, 2, 3)])
        self.assertEqual(old.xpath('./div[@class="logic-detail"]//tr/@data-observation-id'), ['D1','D4','D5','D8','D9','D12'])
        self.assertFalse(old.xpath('.//figure'))
        self.assertFalse(old.xpath('./div[@class="logic-reading-grid"]'))

    def test_reused_comparison_supports_existing_style_choices(self):
        content=sample();result=design(content,ROOT,visual_profile=dict(theme='research',schemes={'logic-4-chart-1':'slope'}))
        chart=next(block['spec'] for block in result['document']['blocks'] if block['kind']=='chart' and block['spec']['target']=='logic-4-chart-1')
        self.assertEqual(chart['auto_visual']['scheme'],'slope')
        self.assertEqual(chart['pairs'],[[1.0,.9],[-1.6,-1.7],[1.1,1.0]])
        self.assertEqual(chart['point_refs'],[['D1','D4'],['D5','D8'],['D9','D12']])


if __name__=='__main__':unittest.main()
