"""Real retained cells fill chart context, without becoming submitted evidence."""
from copy import deepcopy
import unittest
from tests import ROOT
from researchhelper.contracts import canonical,fingerprint
from researchhelper.draft_sources import observations
from researchhelper.draft_analysis import analyze
from researchhelper.drafts import normalize_report,compose,source_binding,logic_basis
from researchhelper.draft_visuals import logic_figure_plan
from researchhelper.report_designer import design


def fixture():
    task=dict(id='window-fixture',version=1,input_hash='window-input',cutoff='2026-10-31T16:00:00+08:00',
        question='比较观察窗口内的价格与PE',entry='个股',intent='解释变化',objects=['600519.SH'],synthetic=True,
        source_records=[],materials=[],questions=[],evidence=[],calculations=[],knowledge={})
    for ident,field,values,operation in [('prices','close',[10,12,None,11,15],'cmd_history_quotation'),
        ('valuation','ths_pe_ttm_stock',[20,24,22,23,25],'date_sequence')]:
        dates=['2026-10-'+str(day) for day in range(26,31)]
        params=dict(codes='600519.SH',functionpara=dict(Currency='RMB',CPS='1'))
        snapshot=dict(errorcode=0,tables=[dict(thscode='600519.SH',time=dates,table={field:values})])
        task['source_records'].append(dict(id=ident,task_id=task['id'],research_version=1,kind='api',source='ifind',
            operation=operation,parameters=params,snapshot=snapshot,status='成功待核验',hash=fingerprint(snapshot),
            request_hash=fingerprint(dict(source='ifind',operation=operation,parameters=params))))
    facts=observations(task);task['draft_analysis']=analyze(task,facts)
    draft=normalize_report(dict(title='窗口观察',conclusion='描述已取得的窗口，不判断长期估值高低。',
        logics=[dict(title='价格与PE同窗观察',analysis='比较实际价格与PE读数，保留缺失点。',
            data_refs=['D1','D5','D6','D10'],calculation_refs=['A1'])]),task,facts)
    task['draft_report']=dict(draft,facts=facts,calculations=task['draft_analysis']['results'],source_binding=source_binding(task))
    return compose(task)


def selected(content):
    return {row['field']:row for row in logic_figure_plan(content) if row.get('paper_logic')=='logic-1'}


class ObservedWindowVisualTests(unittest.TestCase):
    def setUp(self):self.content=fixture()

    def test_full_window_keeps_missing_cells_and_separates_context_from_argument(self):
        before=canonical(self.content);specs=selected(self.content)
        self.assertEqual(specs['close']['values'],[10,12,None,11,15])
        self.assertEqual(specs['ths_pe_ttm_stock']['values'],[20,24,22,23,25])
        for spec in specs.values():
            self.assertEqual(spec['auto_visual']['scheme'],'line')
            self.assertEqual(len(spec['point_refs']),5)
            self.assertEqual(len(spec['plot_context']['context_data_refs']),3)
            self.assertTrue(set(spec['data_refs']) <= set(spec['argument_data_refs'])|set(spec['plot_context']['context_data_refs']))
            self.assertFalse(set(spec['argument_data_refs']) & set(spec['plot_context']['context_data_refs']))
            self.assertEqual(spec['sync_group'],'logic-1-market')
        self.assertEqual(len(logic_basis(self.content,self.content['draft']['logics'][0])[0]),4)
        self.assertEqual(canonical(self.content),before)

    def test_only_the_observed_anchor_interval_is_added(self):
        content=deepcopy(self.content);content['draft']['logics'][0].update(data_refs=['D2','D4','D7','D9'],calculation_refs=[])
        specs=selected(content)
        self.assertEqual(specs['close']['dates'],['2026-10-27','2026-10-28','2026-10-29'])
        self.assertEqual(specs['close']['values'],[12,None,11])
        self.assertNotIn('D1',specs['close']['data_refs']);self.assertNotIn('D5',specs['close']['data_refs'])

    def test_changed_frozen_cells_or_original_hash_disable_only_their_series_expansion(self):
        for change in ('fact','source'):
            content=deepcopy(self.content)
            if change=='fact':content['draft']['facts'][1].update(value=999,raw_value=999)
            else:content['source_records'][0]['snapshot']['tables'][0]['table']['close'][1]=999
            specs=selected(content)
            with self.subTest(change=change):
                self.assertEqual(specs['close']['values'],[10,15])
                self.assertNotIn('plot_context',specs['close'])
                self.assertEqual(len(specs['ths_pe_ttm_stock']['values']),5)
                self.assertTrue(all('sync_group' not in spec for spec in specs.values()))

    def test_an_uncited_source_cannot_fill_the_window(self):
        content=deepcopy(self.content);other=deepcopy(content['source_records'][0]);other['id']='uncited'
        content['source_records'].append(other);content['draft']['facts'][1]['artifact_id']='uncited'
        spec=selected(content)['close'];self.assertEqual(spec['values'],[10,15]);self.assertNotIn('plot_context',spec)

    def test_no_future_context_and_no_missing_dates_are_invented(self):
        content=deepcopy(self.content);content['cutoff']='2026-10-29T16:00:00+08:00'
        self.assertTrue(all('plot_context' not in spec for spec in selected(content).values()))
        content=deepcopy(self.content);content['draft']['facts'].pop(7)
        specs=selected(content)
        self.assertEqual(len(specs['close']['values']),5);self.assertEqual(len(specs['ths_pe_ttm_stock']['values']),4)
        self.assertNotIn('2026-10-28',specs['ths_pe_ttm_stock']['dates'])
        self.assertTrue(all('sync_group' not in spec for spec in specs.values()))

    def test_table_headers_repeat_without_changing_row_order_or_existing_groups(self):
        from lxml import html,etree
        from researchhelper.report_designer import _table_sections,_blocks
        tree=html.fromstring('<main><table><caption>原值</caption><tr><th scope="col">指标</th><th>原值</th></tr><tbody><tr><td>收入</td><td>0</td></tr></tbody><tr><td>利润</td><td>-2</td></tr><tfoot><tr><td>合计</td><td>-2</td></tr></tfoot></table><table><tr><td>资料</td><td>保留</td></tr></table></main>')
        before=[[_text.text_content() for _text in row] for row in tree.xpath('.//tr')]
        _table_sections(tree)
        self.assertEqual([[_text.text_content() for _text in row] for row in tree.xpath('.//tr')],before)
        self.assertEqual(tree.xpath('.//table[1]/thead/tr/th/@scope'),['col'])
        self.assertEqual(tree.xpath('.//table[1]/*')[0].tag,'caption')
        self.assertEqual(len(tree.xpath('.//thead')),1)
        self.assertEqual(_blocks(tree,{})[0]['rows'],before[:4])
        frozen=etree.tostring(tree);_table_sections(tree);self.assertEqual(etree.tostring(tree),frozen)

    def test_standard_and_detailed_keep_the_same_ten_rows_and_point_addresses(self):
        standard=design(self.content,ROOT);detailed=design(self.content,ROOT,profile='detailed')
        self.assertEqual(standard['document']['dataset'],detailed['document']['dataset'])
        self.assertEqual(len(standard['document']['dataset']['rows']),10)
        for result in (standard,detailed):
            coverage=result['document']['figure_coverage']['logic-1']
            self.assertEqual(set(coverage['argument_data_refs']),{'D1','D5','D6','D10'})
            self.assertEqual(len(coverage['context_data_refs']),6)
        basis=[block for block in detailed['document']['blocks'] if block['kind']=='table' and
            block['rows'][0]==['对象与指标','观测期间','原值','单位']]
        self.assertEqual(len(basis),1)
        self.assertEqual(len(basis[0]['rows']),5)
        for spec in selected(self.content).values():
            for refs in spec['point_refs']:self.assertEqual(len(refs),1)
            self.assertEqual(len(spec['data_refs']),5)


if __name__=='__main__':unittest.main()
