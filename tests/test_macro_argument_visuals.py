"""Macro arguments retain their own observed plots after combined chart selection."""
from copy import deepcopy
import io
import socket
import json
import unittest
from unittest.mock import patch
from tests import ROOT
from researchhelper.contracts import canonical,fingerprint
from researchhelper.draft_sources import observations
from researchhelper.draft_analysis import analyze
from researchhelper.drafts import normalize_report,compose,source_binding,logic_basis
from researchhelper.draft_visuals import logic_figure_plan
from researchhelper.report_designer import design


def fixture(*, cpi_readings=(1.0, None, .8, .9)):
    task=dict(id='macro-argument-fixture',version=1,input_hash='macro-input',cutoff='2026-10-31T16:00:00+08:00',
        question='比较CPI及分项的已观测变化',entry='宏观',intent='解释变化',objects=['中国CPI'],synthetic=True,
        materials=[],questions=[],evidence=[],calculations=[],knowledge={})
    dates=['2026-06-30','2026-07-31','2026-08-31','2026-09-30']
    series=[('M002826730','CPI:当月同比',list(cpi_readings)),
            ('M002826732','CPI:食品:当月同比',[-1.6,-1.5,-1.4,-1.7]),
            ('M002840897','CPI:消费品:当月同比',[1.1,.2,.8,1.0])]
    snapshot=dict(errorcode=0,tables=[dict(id=[code],index_name=[label]*4,time=dates,value=values)
        for code,label,values in series])
    parameters=dict(indicators=','.join(row[0] for row in series),startdate=dates[0],enddate=dates[-1])
    task['source_records']=[dict(id='macro-original',task_id=task['id'],research_version=1,kind='api',source='ifind',
        operation='edb_service',parameters=parameters,snapshot=snapshot,status='成功待核验',hash=fingerprint(snapshot),
        request_hash=fingerprint(dict(source='ifind',operation='edb_service',parameters=parameters)))]
    metadata=dict(text='```json\n'+json.dumps([dict(country='中国',dataSource='合成测试目录',displayId=code,
        endDate='20260930',freq='M',id=code[1:],indexName=label,node='价格指数',startDate='20260630',unit='%')
        for code,label,_ in series],ensure_ascii=False)+'\n```')
    task['source_records'].append(dict(id='macro-directory',task_id=task['id'],research_version=1,kind='api',source='ifind',
        operation='search_edb',parameters=dict(query='合成宏观目录'),snapshot=metadata,status='成功待核验',hash=fingerprint(metadata)))
    facts=observations(task);task['draft_analysis']=analyze(task,facts)
    report=normalize_report(dict(title='宏观读数观察',conclusion='比较已取得序列，不作贡献分解。',
        logics=[dict(title=title,analysis='比较同一序列的观测区间，缺失点不补值。',data_refs=refs)
                for title,refs in [('总体读数变化',['D1','D4']),('食品读数变化',['D5','D8']),
                    ('消费品读数变化',['D9','D12']),('同月结构比较',['D4','D8','D12'])]],
        risks=['同期变化不证明贡献或因果。']),task,facts)
    task['draft_report']=dict(report,facts=facts,calculations=task['draft_analysis']['results'],source_binding=source_binding(task))
    return compose(task)


def argument(content,index):
    return [row for row in logic_figure_plan(content) if row.get('paper_logic')=='logic-'+str(index)]


class MacroArgumentVisualTests(unittest.TestCase):
    def setUp(self):
        self.network=patch.object(socket.socket,'connect',side_effect=AssertionError('No external calls'))
        self.network.start();self.addCleanup(self.network.stop)
        self.content=fixture()

    def test_each_argument_gets_its_own_chart_without_duplicate_combined_supplement(self):
        before=canonical(self.content);plots=logic_figure_plan(self.content)
        self.assertEqual(len(plots),4)
        self.assertEqual([row['paper_logic'] for row in plots],['logic-1','logic-2','logic-3','logic-4'])
        self.assertEqual([row['auto_visual']['scheme'] for row in plots],['line','line','line','diverging_bar'])
        self.assertFalse(any(row['target'].startswith('supplement') for row in plots))
        self.assertEqual(canonical(self.content),before)

    def test_context_keeps_gaps_and_is_separate_from_the_submitted_argument(self):
        plot=argument(self.content,1)[0]
        self.assertEqual(plot['values'],[1.0,None,.8,.9])
        self.assertEqual(plot['argument_data_refs'],['D1','D4'])
        self.assertEqual(plot['plot_context']['context_data_refs'],['D2','D3'])
        self.assertEqual(plot['point_refs'],[['D1'],['D2'],['D3'],['D4']])
        self.assertEqual([row['id'] for row in logic_basis(self.content,self.content['draft']['logics'][0])[0]],['D1','D4'])

    def test_only_the_cited_interval_is_added(self):
        content=deepcopy(self.content);content['draft']['logics'][1]['data_refs']=['D6','D8']
        plot=argument(content,2)[0]
        self.assertEqual(plot['dates'],['2026-07-31','2026-08-31','2026-09-30'])
        self.assertEqual(plot['plot_context']['context_data_refs'],['D7'])
        self.assertNotIn('D5',plot['data_refs'])

    def test_missing_periods_do_not_count_toward_the_chart_minimum(self):
        content = fixture(cpi_readings=(1.0, None, None, .9))
        self.assertEqual(argument(content, 1), [])
        for index in (2, 3):
            self.assertEqual(argument(content, index)[0]['auto_visual']['scheme'], 'line')
        content['draft']['visual_design_version'] = 1
        self.assertEqual(argument(content,1),[])

    def test_changed_context_cell_disables_only_that_series_expansion(self):
        content=deepcopy(self.content);content['draft']['facts'][1].update(value=999,raw_value=999)
        self.assertEqual(argument(content,1), [])
        self.assertEqual(len(argument(content,2)[0]['values']),4)
        content['draft']['visual_design_version'] = 1
        self.assertEqual(argument(content,1),[])

    def test_changed_original_hash_does_not_supply_context(self):
        content=deepcopy(self.content);content['source_records'][0]['snapshot']['tables'][0]['value'][1]=999
        for index in (1,2,3):
            self.assertEqual(argument(content,index), [])

    def test_another_source_cannot_fill_the_cited_series(self):
        content=deepcopy(self.content);other=deepcopy(content['source_records'][0]);other['id']='other-original'
        content['source_records'].append(other);content['draft']['facts'][1]['artifact_id']=other['id']
        self.assertEqual(argument(content,1), [])

    def test_future_window_is_not_expanded(self):
        content=deepcopy(self.content);content['cutoff']='2026-09-29T16:00:00+08:00'
        self.assertEqual(argument(content,1), [])

    def test_same_month_comparison_keeps_literal_values_and_overlapping_categories(self):
        plot=argument(self.content,4)[0]
        self.assertEqual(plot['context'],'2026-09-30')
        data=plot['auto_visual']['data']
        self.assertEqual(data['labels'],['CPI:消费品:当月同比','CPI:当月同比','CPI:食品:当月同比'])
        self.assertEqual(data['values'],[1.0,.9,-1.7]);self.assertEqual(plot['unit'],'%')
        self.assertEqual(plot['point_refs'],[['D12'],['D4'],['D8']])
        self.assertNotIn('plot_context',plot)

    def test_different_months_or_units_do_not_become_a_comparable_cross_section(self):
        content=deepcopy(self.content);content['draft']['logics'][3]['data_refs']=['D4','D7','D12']
        self.assertEqual(argument(content,4),[])
        content=deepcopy(self.content);content['draft']['facts'][3]['unit']='元'
        self.assertEqual(argument(content,4),[])

    def test_full_multi_series_argument_uses_one_primary_view_per_profile(self):
        content=deepcopy(self.content);logic=content['draft']['logics'][0]
        logic['data_refs']=[row['id'] for row in content['draft']['facts']];content['draft']['logics']=[logic]
        plots=argument(content,1)
        self.assertEqual([row['auto_visual']['scheme'] for row in plots],['small_multiples'])
        detailed = logic_figure_plan(content, report_profile='detailed')
        self.assertEqual([row['auto_visual']['scheme'] for row in detailed],['multi_line'])
        self.assertFalse(any(row.get('argument_only') for row in plots))

    def test_window_view_uses_only_registered_and_cited_changes(self):
        content=deepcopy(self.content)
        logic=content['draft']['logics'][0]
        logic.update(data_refs=[row['id'] for row in content['draft']['facts']],
                     calculation_refs=[row['id'] for row in content['draft']['calculations']])
        content['draft']['logics']=[logic]
        before=canonical(content)
        plots=argument(content,1)
        self.assertEqual([row['auto_visual']['scheme'] for row in plots], ['small_multiples','window_change'])
        window=plots[1]
        calculations={tuple(row['data_refs']):row for row in content['draft']['calculations']}
        for series,refs in zip(window['auto_visual']['data']['series'],window['series_point_refs'],strict=True):
            self.assertEqual(series['values'], [calculations[tuple(ref)]['value'] for ref in refs])
        from pypdf import PdfReader
        from researchhelper.publication_graphics import chart_pdf
        for chart, expected in zip(plots, ('CPI:食品:当月同比','06-30至09-30'), strict=True):
            paper = PdfReader(io.BytesIO(chart_pdf(chart, palette='red_blue'))).pages[0].extract_text()
            self.assertIn(expected, paper)
        self.assertEqual(canonical(content),before)
        logic['calculation_refs'].pop()
        self.assertEqual([row['auto_visual']['scheme'] for row in argument(content,1)],['small_multiples'])
        self.assertEqual([row['auto_visual']['scheme'] for row in logic_figure_plan(content,report_profile='detailed')],['multi_line'])

    def test_single_series_two_changes_keep_the_observed_path_only(self):
        content = deepcopy(self.content)
        draft = content['draft']
        code = draft['facts'][0]['code']
        draft['facts'] = [row for row in draft['facts'] if row['code'] == code]
        refs = [row['id'] for row in draft['facts']]
        draft['calculations'] = [row for row in draft['calculations'] if set(row['data_refs']) <= set(refs)]
        calculations = [row['id'] for row in draft['calculations']]
        self.assertEqual(len(calculations), 2)
        draft['logics'] = [dict(draft['logics'][0],data_refs=refs,calculation_refs=calculations)]
        for profile in ('standard','detailed'):
            with self.subTest(profile=profile):
                plots = logic_figure_plan(content,report_profile=profile)
                self.assertEqual([row['auto_visual']['scheme'] for row in plots], ['line'])
                self.assertEqual(plots[0]['data_refs'],refs)
                self.assertEqual(draft['logics'][0]['calculation_refs'],calculations)

    def test_standard_and_internal_share_all_data_and_per_argument_chart_addresses(self):
        before=canonical(self.content)
        standard=design(self.content,ROOT);detailed=design(self.content,ROOT,profile='detailed')
        self.assertEqual(standard['document']['dataset'],detailed['document']['dataset'])
        self.assertEqual(len(standard['document']['dataset']['rows']),12)
        for result in (standard,detailed):
            coverage=result['document']['figure_coverage']
            self.assertEqual([row['figures'] for row in coverage.values()],[1,1,1,1])
            self.assertEqual(coverage['logic-1']['argument_data_refs'],['D1','D4'])
            self.assertEqual(coverage['logic-1']['context_data_refs'],['D2','D3'])
            self.assertNotIn('src="https://',result['html'])
        self.assertEqual(canonical(self.content),before)


if __name__=='__main__':unittest.main()
