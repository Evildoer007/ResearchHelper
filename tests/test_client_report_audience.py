"""Client publication and per-logic internal notes share one offline result.

This new suite leaves historical tests and frozen production reports untouched.
The small fixture is explicitly synthetic; the saved Maotai replay is separate.
"""
from copy import deepcopy
import base64
import json
import socket
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from lxml import html
from tests import ROOT
from researchhelper.contracts import canonical, fingerprint
from researchhelper.draft_analysis import analyze
from researchhelper.draft_workflow import _write
from researchhelper.drafts import normalize_report,compose,source_binding
from researchhelper.publication_content import CLIENT_REPORT_BRIEF
from researchhelper.report_designer import design


def fixture():
    task=dict(id='client-paper-fixture',version=1,input_hash='fixture',cutoff='2026-10-29T00:00:00+08:00',
        question='比较年度经营及近期价格',entry='个股',intent='综合研究',objects=['600519.SH'],synthetic=True,
        source_records=[dict(id='financial',task_id='client-paper-fixture',research_version=1,source='ifind',snapshot={}),
                        dict(id='market',task_id='client-paper-fixture',research_version=1,source='ifind',snapshot={})],
        materials=[],questions=[],evidence=[],calculations=[])
    facts=[]
    for period,values in [('20241231',[10000000000,5000000000,6000000000]),('20251231',[9900000000,4500000000,4000000000])]:
        for field,label,value in zip(['ths_revenue_stock','ths_np_atoopc_stock','ths_ncf_from_oa_stock'],['营业收入','归母净利润','经营现金流'],values):
            facts.append(dict(id='D'+str(len(facts)+1),artifact_id='financial',code='600519.SH',field=field,label=label,
                period=period,value=value,raw_value=value,unit='原始单位待核对',series_basis='annual',axis_role='time'))
    for period,price,pe in [('2026-10-26',1330,20.2),('2026-10-27',1340,20.4),('2026-10-28',1335,20.3)]:
        for field,label,value,unit in [('close','收盘价',price,'元'),('ths_pe_ttm_stock','PE(TTM)',pe,'原始单位待核对')]:
            facts.append(dict(id='D'+str(len(facts)+1),artifact_id='market',code='600519.SH',field=field,label=label,
                period=period,value=value,raw_value=value,unit=unit,series_basis=field,axis_role='time'))
    task['draft_analysis']=analyze(task,facts)
    report=normalize_report(dict(title='经营变化与定价观察',conclusion='利润与现金流改善是经营修复的关键。',
        logics=[dict(title='利润修复取决于经营改善',analysis='利润降幅大于收入，需关注盈利能力变化。',
                     implication='若收入与利润同步回升，经营修复逻辑将增强。',risk='需求走弱可能延后盈利修复。',
                     data_refs=['D1','D2','D4','D5'],calculation_refs=['A1','A2'],workpaper_notes=['内部口径核验备注。']),
                dict(title='现金兑现决定修复质量',analysis='现金流下降较多，不能直接等同于销售回款恶化。',
                     implication='需结合结算与营运资金变化解释现金兑现。',risk='结算时点可能造成阶段性差异。',
                     data_refs=['D3','D6'],calculation_refs=['A3']),
                dict(title='短期定价需结合盈利预期',analysis='价格与估值的近期变化不等于历史高低估判断。',
                     implication='盈利预期变化会影响后续定价。',risk='短期区间不代表长期趋势。',
                     data_refs=[r['id'] for r in facts[6:]])],
        risks=['需求走弱、利润修复不及预期，可能延后经营改善。'],
        workpaper_notes=['仅内部保存的接口诊断。']),task,facts)
    task['draft_report']=dict(report,facts=facts,calculations=task['draft_analysis']['results'],source_binding=source_binding(task))
    return task,compose(task)


class ClientReportAudienceTests(unittest.TestCase):
    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No network or paid model calls'))
        guard.start();self.addCleanup(guard.stop)
        self.task,self.content=fixture()

    def test_client_keeps_business_narrative_and_moves_records_out_of_paper(self):
        before=canonical(self.content);result=design(self.content,ROOT);tree=html.document_fromstring(result['html'])
        visible=''.join(tree.xpath('//main//text()'))
        for word in ('核心结论','策略逻辑一','策略逻辑二','策略逻辑三','风险提示','利润与现金流改善'):
            self.assertIn(word,visible)
        self.assertIn('若报表披露口径不同，同比和比率需重新计算。',tree.get_element_by_id('risk').text_content())
        for word in ('研究草稿','原值','同尺度','核验','ResDesk','接口','内部口径','仅内部保存','完整原始数据'):
            self.assertNotIn(word,visible)
        self.assertFalse(tree.xpath('//main//table|//main//*[contains(@class,"evidence-ledger")]|//details|//summary'))
        self.assertTrue(tree.xpath('//main//a[@href="#complete-report-data"]'))
        self.assertTrue(tree.xpath('//*[@id="complete-report-data"]//table'))
        self.assertEqual(before,canonical(self.content))

    def test_offline_delivery_carries_the_bound_excerpt_and_omits_uncited_attachment_text(self):
        from core.session import Session

        cited = '2025年第三季度营业收入同比增长19%'
        private_page = '未引用的内部附页RH-PRIVATE-PAGE'
        private_tail = '同段未引用的文字RH-PRIVATE-TAIL'
        workpaper = '仅在详版审阅的内部复核备注'
        original = (private_page + '。\n\n' + cited + '。' + private_tail + '。').encode()
        with TemporaryDirectory() as directory:
            session = Session(directory)
            try:
                task = session.create('核对600519.SH第三季度营业收入的报告口径。',objects=['600519.SH'])
                ident = task['id']
                attachment = session.material(ident,'季度披露.txt',original)
                final = session.draft(ident,report=dict(title='收入披露核对',conclusion=cited+'。',
                    logics=[dict(title='第三季度营业收入',analysis=cited+'。',source_refs=[attachment['id']],
                        workpaper_notes=[workpaper],risk='比较采用同一收入定义。')]),data_requests=[])
                self.assertTrue(final.get('delivery'),final.get('error'))
                self.assertEqual(final['ledger']['used']['model_calls'],0)
                saved = session.archive.report(ident,1)
                frozen = session.export(ident).read_text()
                for profile in ('standard','detailed'):
                    with self.subTest(profile=profile):
                        page = session.export(ident,profile=profile).read_text()
                        tree = html.document_fromstring(page)
                        (link,) = tree.get_element_by_id('summary').xpath('.//a[@class="quantity-link"]')
                        excerpt = tree.get_element_by_id(link.get('href')[1:])
                        self.assertEqual(excerpt.xpath('./p[not(@class)]/text()'),[cited])
                        self.assertEqual(excerpt.xpath('./p[@class="figure-source"]/text()'),['段落2'])
                        risk_target = 'logic-1' if profile == 'detailed' else 'risk'
                        self.assertIn('比较采用同一收入定义。',tree.get_element_by_id(risk_target).text_content())
                        self.assertIn('用户提供材料',tree.xpath('//main/footer/p')[0].text_content())
                        self.assertNotIn(private_page,page)
                        self.assertNotIn(private_tail,page)
                        self.assertNotIn(base64.b64encode(original).decode(),page)
                        self.assertEqual(workpaper in page,profile=='detailed')
                        if profile=='standard':
                            self.assertEqual(page,frozen)
                self.assertEqual(session.archive.report(ident,1),saved)
                kept = session.archive.get(ident)['materials'][0]['snapshot']
                self.assertEqual(base64.b64decode(kept['original_base64']),original)
            finally:
                session.agent.close_runtime()

    def test_no_unconfirmed_absolute_amounts_are_plotted_for_clients(self):
        result=design(self.content,ROOT);charts=[b['spec'] for b in result['document']['blocks'] if b['kind']=='chart']
        self.assertEqual(len(charts), 2)
        coverage = result['document']['figure_coverage']
        self.assertEqual([row['status'] for row in coverage.values()], ['numeric_text', 'numeric_text', 'numeric'])
        self.assertEqual(set(coverage['logic-1']['data_refs']), {'D1', 'D2', 'D4', 'D5'})
        self.assertEqual(set(coverage['logic-2']['data_refs']), {'D3', 'D6'})
        self.assertEqual(len(result['document']['dataset']['rows']), 12)
        self.assertEqual(result['document']['dataset']['rows'][0][3], 10000000000)
        self.assertEqual({spec['field'] for spec in charts}, {'close', 'ths_pe_ttm_stock'})
        self.assertFalse(html.document_fromstring(result['html']).xpath('//main//tr[@data-observation-id]'))
        pe=next(s for s in charts if s.get('field')=='ths_pe_ttm_stock')
        self.assertEqual(pe['unit'],'');self.assertEqual(pe['values'],[20.2,20.4,20.3])

    def test_detailed_expands_same_logics_and_preserves_notes_and_raw_values(self):
        client=design(self.content,ROOT);detail=design(self.content,ROOT,profile='detailed')
        tree=html.document_fromstring(detail['html']);text=''.join(tree.xpath('//main//text()'))
        self.assertEqual(client['document']['content_hash'],detail['document']['content_hash'])
        self.assertEqual(client['document']['dataset'],detail['document']['dataset'])
        for word in ('内部口径核验备注。','仅内部保存的接口诊断。','10000000000','数据依据','反证与验证条件'):
            self.assertIn(word,text)
        self.assertTrue(tree.xpath('//*[@data-logic-detail="logic-1"]'))
        self.assertTrue(tree.xpath('//main//table'))

    def test_risk_does_not_disappear_when_only_per_logic_risks_were_supplied(self):
        content=deepcopy(self.content);content['draft']['risks']=[]
        from researchhelper.contracts import fingerprint
        content['content_hash']=fingerprint({k:v for k,v in content.items() if k!='content_hash'})
        tree=html.document_fromstring(design(content,ROOT)['html'])
        for logic in content['draft']['logics']:self.assertIn(logic['risk'],''.join(tree.xpath('//main//*[@id="risk"]//text()')))

    def test_current_draft_deduplicates_risks_without_losing_scope_or_evidence(self):
        from researchhelper.contracts import fingerprint
        from researchhelper.publication_content import DRAFT_READING_POLICY, DRAFT_READING_VERSION

        content = deepcopy(self.content)
        draft = content['draft']
        draft[DRAFT_READING_POLICY] = DRAFT_READING_VERSION
        first, second, third = [item['risk'] for item in draft['logics']]
        draft['logics'][1]['analysis'] += '\n' + second
        distinct = draft['risks'][0]
        extra = '实际结算日期尚未取得。'
        draft['risks'].extend([first, second, distinct])
        draft['reader_notes'].extend([extra, second, extra])
        content['content_hash'] = fingerprint({k:v for k,v in content.items() if k != 'content_hash'})
        before = canonical(content)
        for profile in ('standard','detailed'):
            with self.subTest(profile=profile):
                result = design(content, ROOT, profile=profile)
                tree = html.document_fromstring(result['html'])
                main = tree.xpath('//main')[0].text_content()
                for index, risk in enumerate((first, second, third), 1):
                    self.assertEqual(main.count(risk), 1)
                    target = f'logic-{index}' if profile == 'detailed' else 'risk'
                    self.assertIn(risk, tree.get_element_by_id(target).text_content())
                self.assertEqual(main.count(distinct), 1)
                self.assertEqual(main.count(extra), 1)
                self.assertIn(extra, tree.get_element_by_id('risk').text_content())
                self.assertEqual(len(result['document']['dataset']['rows']), 12)
                coverage = result['document']['figure_coverage']
                expected = ('numeric_text' if profile == 'standard' else 'numeric_table', 'numeric_text', 'numeric')
                self.assertEqual(tuple(row['status'] for row in coverage.values()), expected)
                self.assertEqual(set(coverage['logic-2']['data_refs']), {'D3', 'D6'})
                self.assertEqual(canonical(content), before)

    def test_normalizer_retains_workpaper_notes_but_rejects_nontext(self):
        facts=self.task['draft_report']['facts']
        extra = '若应收账龄拉长，同一销售增量对应的收现压力会上升。'
        value=dict(title='研究',conclusion='结论',workpaper_notes=['备注','备注',{'bad':True}],reader_notes=['适用范围'],
                   logics=[dict(title='逻辑',analysis='判断',detail_analysis=extra,workpaper_notes=['内部','内部',None]),
                           dict(title='另一判断',analysis='第二段',detail_analysis={'bad': True})])
        normalized=normalize_report(value,self.task,facts)
        self.assertEqual(normalized['workpaper_notes'],['备注']);self.assertEqual(normalized['reader_notes'],['适用范围'])
        self.assertEqual(normalized['logics'][0]['workpaper_notes'],['内部'])
        self.assertEqual(normalized['logics'][0]['detail_analysis'], extra)
        self.assertNotIn('detail_analysis', normalized['logics'][1])
        content = deepcopy(self.content)
        content['draft'].update(normalized)
        content['content_hash'] = fingerprint({key: val for key, val in content.items() if key != 'content_hash'})
        for profile in ('standard', 'detailed'):
            paper = html.document_fromstring(design(content, ROOT, profile=profile)['html']).find('.//main')
            self.assertEqual(extra in paper.text_content(), profile == 'detailed')

    def test_app_uses_shared_client_brief_without_extra_model_call(self):
        calls=[]
        class Model:
            def reason(self,task,messages,**options):calls.append((messages,options));return '{}'
        class Engine:model=Model()
        _write(Engine(),self.task,self.task['draft_report']['facts'])
        self.assertEqual(len(calls),1);messages,options=calls[0]
        self.assertTrue(messages[0]['content'].startswith(CLIENT_REPORT_BRIEF))
        self.assertTrue(options['delivery']);self.assertEqual(options['phase'],'draft_report')
        example=json.loads(messages[1]['content'])['output_example']
        self.assertIn('workpaper_notes',example);self.assertIn('reader_notes',example)

    def nav_content(self,values=None,second_object=False):
        from tests.test_draft_arithmetic import nav_fixture
        from researchhelper.draft_sources import observations
        task=nav_fixture('ths_unit_nvnf_fund',values=values)
        if second_object:
            other=nav_fixture('ths_unit_nvnf_fund',values=['7.0000','7.2000'],code='510500.SH')
            for record in other['source_records']:record['id']='second-'+record['id']
            task['source_records'].extend(other['source_records']);task['objects'].append('510500.SH')
        facts=observations(task);task['draft_analysis']=analyze(task,facts)
        report=normalize_report(dict(title='净值读数观察',conclusion='只核对实际读数。',logics=[dict(
            title='区分对象与净值读数',analysis='分别比较已取得的单位净值。',data_refs=[row['id'] for row in facts],
            calculation_refs=[row['id'] for row in task['draft_analysis']['results']])]),task,facts)
        task['draft_report']=dict(report,facts=facts,calculations=task['draft_analysis']['results'],source_binding=source_binding(task))
        return compose(task)

    def test_internal_rows_identify_each_object_without_rewriting_values(self):
        from researchhelper.publication_content import project_content
        content=self.nav_content(second_object=True);before=canonical(content)
        blocks=project_content(content,'detailed')['logic_blocks']['logic-1']
        rows=next(row['rows'] for row in blocks if row['kind']=='table')[1:]
        self.assertEqual([row[0] for row in rows],['510300.SH：单位净值']*3+['510500.SH：单位净值']*2)
        self.assertEqual([row[2] for row in rows],['4.0000','3.8000','4.4000','7.0000','7.2000'])
        self.assertEqual(canonical(content),before)

    def test_internal_calculations_identify_the_object_and_observed_window(self):
        from researchhelper.publication_content import project_content
        content=self.nav_content(second_object=True)
        blocks=project_content(content,'detailed')['logic_blocks']['logic-1']
        paragraphs=[row['text'] for row in blocks if row['kind']=='paragraph']
        for calc in content['draft']['calculations']:
            self.assertIn(calc['code']+'：'+calc['label']+'，'+calc['period'],paragraphs)
        tree=html.document_fromstring(design(content,ROOT)['html'])
        basis=tree.xpath('//*[@id="draft-basis-1"]')[0]
        for calc in content['draft']['calculations']:
            self.assertIn(calc['code']+'：'+calc['label']+'，'+calc['period'],basis.text_content())

    def test_missing_observation_is_readable_but_raw_null_is_retained_in_exports(self):
        from researchhelper.publication_content import project_content,publication_dataset
        content=self.nav_content(values=['4.0000',None,'4.4000'])
        blocks=project_content(content,'detailed')['logic_blocks']['logic-1']
        rows=next(row['rows'] for row in blocks if row['kind']=='table')
        self.assertEqual(rows[2][2],'未返回')
        self.assertIsNone(publication_dataset(content)['rows'][1][3])
        text=html.document_fromstring(design(content,ROOT,profile='detailed')['html']).find('.//main').text_content()
        self.assertNotIn('None',text);self.assertIn('未返回',text)

    def test_internal_calculation_keeps_actual_scope_and_equal_unit_condition(self):
        from researchhelper.publication_content import project_content
        content=self.nav_content();blocks=project_content(content,'detailed')['logic_blocks']['logic-1']
        paragraphs=[row['text'] for row in blocks if row['kind']=='paragraph']
        for calc in content['draft']['calculations']:self.assertIn(calc['condition'],paragraphs)
        self.assertIn('原始单位未明确，比较以各输入尺度一致为前提。',paragraphs)
        tree=html.document_fromstring(design(content,ROOT)['html'])
        inspectors=tree.xpath('//*[@id="draft-basis-1"]')
        self.assertIn('不等于持有人总回报',inspectors[0].text_content())
        self.assertNotIn('按输入金额',inspectors[0].text_content())

    def test_sources_use_the_same_public_label_in_paper_details_and_export(self):
        from tests.test_draft_arithmetic import nav_fixture
        from researchhelper.draft_sources import observations
        task=nav_fixture(values=['4.1856']);facts=observations(task)
        report=normalize_report(dict(title='净值观察',logics=[dict(title='仅核对读数',analysis='不补造序列。',data_refs=['D1'])]),task,facts)
        task['draft_report']=dict(report,facts=facts,calculations=[],source_binding=source_binding(task));content=compose(task)
        before=canonical(content)
        for profile in ('standard','detailed'):
            result=design(content,ROOT,profile=profile);tree=html.document_fromstring(result['html'])
            self.assertNotIn('official',tree.find('.//main').text_content())
            self.assertIn('公开来源',tree.find('.//main').text_content())
            self.assertEqual(result['document']['dataset']['rows'][0][-1],'公开来源')
        self.assertEqual(canonical(content),before)

    def test_client_and_internal_html_are_self_contained(self):
        for profile in ('standard','detailed'):
            tree=html.document_fromstring(design(self.content,ROOT,profile=profile)['html'])
            self.assertFalse(tree.xpath('//script[@src]|//link[@href]|//iframe'))
            self.assertTrue(tree.xpath('//aside[@class="report-toc"]//a'))
            self.assertEqual(tree.xpath('//body/@data-report-template'),['report-'+profile])

if __name__=='__main__':unittest.main()
