"""Do not combine distinct financial statement requests in a report figure."""
from copy import deepcopy
import socket,unittest
from unittest.mock import patch
from lxml import html
from tests import ROOT
from tests.test_draft_financial_period import sample,add_record,rehash
from researchhelper.contracts import canonical,fingerprint
from researchhelper.draft_sources import observations
from researchhelper.draft_analysis import analyze
from researchhelper.drafts import normalize_report,compose,source_binding
from researchhelper.draft_visuals import comparison_figures,logic_figure_plan
from researchhelper.report_designer import design
from researchhelper.publication_content import requested_financial_statement


def report_fixture(*,mixed=True,statements=('1','2'),cash=False):
    task=sample();task.update(draft_source_reader_version=14,source_records=[],questions=[],evidence=[],calculations=[],knowledge={},gaps=[],tracking=[],theses=[])
    scopes=[(statements[0],('ths_revenue_stock','ths_ncf_from_oa_stock' if cash else 'ths_operating_cost_stock'),[(100,70),(120,72)])]
    if mixed:scopes.append((statements[1],('ths_revenue_stock','ths_np_stock'),[(80,8),(90,18)]))
    for statement,fields,values in scopes:
        for period,amounts in zip(('20241231','20251231'),values):
            record=add_record(task,period,amounts,fields=fields)
            for item in record['parameters']['indipara']:item['indiparams'][1]=statement
            record['snapshot']['synthetic_test_response']=True;rehash(record)
    return freeze_report(task)


def freeze_report(task):
    facts=observations(task);task['draft_analysis']=analyze(task,facts)
    proposal=dict(title='分口径盈利观察',conclusion='分别观察合并与母公司口径，不能相互替代。',logics=[dict(title='收入与现金流变化' if any('ths_ncf_from_oa_stock' in item['snapshot']['tables'][0]['table'] for item in task['source_records']) else '收入成本与利润变化',analysis='分别核对同口径的经营变化，不把不同报表口径当成同一组财务。',data_refs=[row['id'] for row in facts],calculation_refs=[row['id'] for row in task['draft_analysis']['results']],risk='母公司与合并报表范围不同。')])
    task['draft_report']=dict(normalize_report(proposal,task,facts),facts=facts,calculations=task['draft_analysis']['results'],source_binding=source_binding(task))
    return task,compose(task)



def run_cohort_research(folder,resources,policy=None):
    """Exercise the agent with explicit synthetic model and transport replies."""
    import json
    from copy import deepcopy
    from types import SimpleNamespace
    from researchhelper.archive import ResearchArchive
    from researchhelper.engine import ResearchAgent
    from researchhelper.contracts import charge,now,canonical,fingerprint
    from researchhelper.draft_sources import observations
    from researchhelper.evidence import snapshot_record,_record_current_acquisition,_controlled_financial_definitions
    if policy is None:
        from tests.test_draft_discovery_navigation import retained_inputs
        policy=retained_inputs()[3]['snapshot']
    phases=[];operations=[]
    scopes=[('1',('ths_revenue_stock','ths_operating_cost_stock'),[(100,70),(120,72)]),
            ('2',('ths_revenue_stock','ths_np_stock'),[(80,8),(90,18)])]
    def transport(task,provider,operation,params,credentials):
        assert provider=='ifind' and not credentials
        if operation=='get_limit_policy':payload=deepcopy(policy)
        elif operation=='basic_data_service':
            assert params['codes']=='600519.SH'
            period,statement=params['indipara'][0]['indiparams'];scope=next(item for item in scopes if item[0]==statement)
            fields,amounts=scope[1],scope[2][('20241231','20251231').index(period)]
            assert [item['indicator'] for item in params['indipara']]==list(fields)
            payload=dict(errorcode=0,synthetic_test_response=True,
                inputParams=dict(params=[dict(function=field,params=[dict(name='FDREPORT',value=period),dict(name='FL',value=statement)]) for field in fields]),
                tables=[dict(thscode='600519.SH',table={field:[value] for field,value in zip(fields,amounts)})])
        else:raise AssertionError('Unexpected source dispatch: '+operation)
        charge(task['ledger'],'data_requests');record=snapshot_record(task,provider,operation,params,payload)
        if operation=='basic_data_service':record.update(fixed_financial_catalog_version=1,fixed_financial_catalog_digest=fingerprint(_controlled_financial_definitions()))
        event=dict(kind='data_requests',source=provider,operation=operation,fingerprint=record['request_hash'],batch=task['batch'],time=now(),
            status='成功',transport_isolated=True,network_request_started=True,network_worker_reaped=True,synthetic_test_transport=True)
        task['ledger']['dispatches'].append(event);_record_current_acquisition(task,record,event);operations.append(operation);return record
    def reply(task,messages,*,phase,delivery=False,**options):
        phases.append(phase);charge(task['ledger'],'model_calls',delivery=delivery)
        if phase=='draft_plan':
            requests=[]
            if phases.count('draft_plan')==1:
                for statement,fields,values in scopes:
                    for period in ('20241231','20251231'):
                        requests.append(dict(id='financial_'+statement+'_'+period,source='ifind',operation='basic_data_service',
                            parameters=dict(codes='600519.SH',indipara=[dict(indicator=field,indiparams=[period,statement]) for field in fields]),
                            question_ids=[task['questions'][0]['id']],selection_reason='分别取得原问题要求的合并与母公司报表口径，不互相替代。'))
            return canonical(dict(data_requests=requests))
        facts=observations(task);records={record['id']:record for record in task['source_records']};logics=[]
        for statement,fields,values in scopes:
            refs=[row['id'] for row in facts if records[row['artifact_id']]['parameters']['indipara'][0]['indiparams'][1]==statement]
            calculations=[row['id'] for row in task.get('draft_analysis',{}).get('results',[]) if set(row['data_refs'])<=set(refs)]
            logics.append(dict(title='成本变化与收入成本差占比' if statement=='1' else '净利润与收入相对变化',
                analysis='本案例仅核对受控原值的变化，合并与母公司报表分别判断，不推断真实财务或证券收益。',
                data_refs=refs,calculation_refs=calculations,risk='这是合成响应的流程验收，不是真实投研结论。'))
        return canonical(dict(title='分口径经营观察',conclusion='分别比较合并与母公司报表，两组数据不能相互替代。',logics=logics,risks=['合成响应仅用于流程验收，不代表真实财务。']))
    archive=ResearchArchive(folder);agent=ResearchAgent(archive,resources,offline=False,model=SimpleNamespace(reason=reply),source_credentials={},require_argument_support=True,draft_first=True)
    agent.collector.external=transport
    try:
        task=agent.create('研究贵州茅台600519.SH，分别比较2024年、2025年合并报表的营业收入和营业成本，以及母公司报表的营业收入与报表净利润，解释两组变化并生成报告。',
            entry='个股',objects=['600519.SH'],research_mode='current',limits=dict(model_calls=3,data_requests=100,seconds=600))
        task['synthetic']=True;archive.save(task);task=agent.start(task['id'])
        assert task.get('delivery'),dict(error=task.get('error'),phases=phases,operations=operations)
        return dict(task=task,paper=archive.report(task['id'],task['version']),phases=phases,operations=operations)
    finally:agent.close_runtime()


def cohorts(content,spec):
    by_id={row['id']:row for row in content['draft']['facts']}
    return {(by_id[ref].get('series_basis'),by_id[ref]['unit']) for ref in spec['data_refs']}


class FinancialVisualCohorts(unittest.TestCase):
    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No external calls'));guard.start();self.addCleanup(guard.stop)

    def test_growth_never_joins_cost_from_consolidated_with_parent_profit(self):
        _,content=report_fixture();before=canonical(content)
        figures=[spec for spec in comparison_figures(content,[]) if spec.get('kind')=='changes']
        self.assertEqual(len(figures),2)
        self.assertTrue(all(len(cohorts(content,spec))==1 for spec in figures))
        self.assertEqual({tuple(spec['dates']) for spec in figures},{('营业收入','营业成本'),('营业收入','净利润')})
        self.assertEqual(canonical(content),before)

    def test_margin_pair_never_joins_distinct_statement_scopes(self):
        _,content=report_fixture();figures=[spec for spec in comparison_figures(content,[]) if spec.get('kind')=='paired']
        self.assertEqual(len(figures),2)
        self.assertTrue(all(len(cohorts(content,spec))==1 for spec in figures))
        self.assertEqual({tuple(spec['dates']) for spec in figures},{('收入成本差占比',),('报表净利润率',)})

    def test_two_changes_per_scope_use_one_compact_table_with_bound_inputs(self):
        _, content = report_fixture()
        self.assertEqual(logic_figure_plan(content), [])
        page = html.document_fromstring(design(content, ROOT)['html'])
        logic = page.get_element_by_id('logic-1')
        table, = logic.xpath('.//table')
        facts = {row['id']: row for row in content['draft']['facts']}
        statements = set()
        for row in table.xpath('./tbody/tr'):
            inputs = [facts[ref] for ref in row.get('data-calculation-inputs').split()]
            statement = requested_financial_statement(content, inputs)
            self.assertTrue(statement)
            self.assertEqual(len({item['series_basis'] for item in inputs}), 1)
            self.assertIn(statement, row.xpath('./th')[0].text_content())
            statements.add(statement)
        self.assertEqual(statements, {'合并报表', '母公司报表'})
        self.assertFalse(logic.xpath('.//tr[@data-observation-id]'))
        self.assertEqual(set(page.xpath('//*[@id="draft-basis-1"]//tr/@data-observation-id')), set(facts))

    def test_both_profiles_preserve_all_original_rows_and_actual_calculations(self):
        _,content=report_fixture();before=canonical(content)
        reports=[design(content,ROOT,profile=profile) for profile in ('standard','detailed')]
        self.assertEqual(reports[0]['document']['dataset'],reports[1]['document']['dataset'])
        self.assertEqual(len(reports[0]['document']['dataset']['rows']),8)
        for row in reports[0]['document']['dataset']['rows']:
            self.assertTrue('合并报表' in row[1] or '母公司报表' in row[1])
        self.assertEqual([row[3] for row in reports[0]['document']['dataset']['rows']],[100,70,120,72,80,8,90,18])
        for report in reports:
            tree = html.document_fromstring(report['html'])
            section = tree.get_element_by_id('logic-1').text_content()
            self.assertIn('合并报表', section)
            self.assertIn('母公司报表', section)
            self.assertIn('不能相互替代', tree.xpath('//main')[0].text_content())
        self.assertEqual(canonical(content),before)

    def test_single_scope_does_not_add_redundant_captions(self):
        _,content=report_fixture(mixed=False)
        self.assertFalse(any(spec.get('comparison_scope') for spec in comparison_figures(content,[])))
        self.assertTrue(comparison_figures(content,[]))

    def test_missing_requested_scope_does_not_guess_or_prevent_report(self):
        _,content=report_fixture()
        for record in content['source_records']:record['parameters'].pop('indipara',None)
        content['content_hash']=fingerprint({key:value for key,value in content.items() if key!='content_hash'})
        for spec in comparison_figures(content,[]):self.assertEqual(len(cohorts(content,spec)),1)
        result=design(content,ROOT);self.assertEqual(len(result['document']['dataset']['rows']),8)
        self.assertIn('分口径盈利观察',result['html'])


    def test_adjusted_request_names_come_from_the_registered_catalogue(self):
        _,content=report_fixture(statements=('3','4'))
        page = html.document_fromstring(design(content, ROOT)['html'])
        metrics = page.get_element_by_id('logic-1').xpath('.//tr[@data-calculation-ref]/th/text()')
        self.assertTrue(any(name.startswith('合并报表(调整) ') for name in metrics))
        self.assertTrue(any(name.startswith('母公司报表(调整) ') for name in metrics))
        self.assertFalse(any(name.startswith(('合并报表 ', '母公司报表 ')) for name in metrics))

    def test_scope_name_is_not_borrowed_from_wrong_record_or_period(self):
        _,content=report_fixture();row=content['draft']['facts'][0]
        self.assertEqual(requested_financial_statement(content,[row]),'合并报表')
        for mode in ('task','version','provider','operation','code','period','field','duplicate','unregistered','request_hash'):
            changed=deepcopy(content);source=next(item for item in changed['source_records'] if item['id']==row['artifact_id'])
            if mode=='task':source['task_id']='another-task'
            elif mode=='version':source['research_version']=0
            elif mode=='provider':source['source']='unregistered'
            elif mode=='operation':source['operation']='unregistered'
            elif mode=='code':source['parameters']['codes']='000001.SZ'
            elif mode=='period':source['parameters']['indipara'][0]['indiparams'][0]='20240630'
            elif mode=='field':source['parameters']['indipara'][0]['indicator']='ths_np_stock'
            elif mode=='duplicate':source['parameters']['indipara'].append(deepcopy(source['parameters']['indipara'][0]))
            elif mode=='request_hash':source['request_hash']='changed-request'
            else:source['parameters']['indipara'][0]['indiparams'][1]='9'
            if mode!='request_hash':rehash(source)
            with self.subTest(mode=mode):self.assertEqual(requested_financial_statement(changed,[row]),'')

    def test_mixed_scopes_and_publication_dates_do_not_get_a_single_label(self):
        _,content=report_fixture();self.assertEqual(requested_financial_statement(content,content['draft']['facts']),'')
        row=deepcopy(content['draft']['facts'][0]);row['period_basis']='发布日期'
        self.assertEqual(requested_financial_statement(content,[row]),'')


    def test_cost_comparison_keeps_income_and_cost_without_a_profit_figure(self):
        _, content = report_fixture(mixed=False)
        report = design(content, ROOT)
        self.assertFalse(logic_figure_plan(content))
        logic = html.document_fromstring(report['html']).get_element_by_id('logic-1')
        results = {row.xpath('./th')[0].text_content() for row in logic.xpath('.//tr[@data-calculation-ref]')}
        self.assertTrue({'营业收入同比', '营业成本同比'} <= results)
        self.assertFalse(any('利润' in label for label in results))
        self.assertFalse(logic.xpath('.//tr[@data-observation-id]'))

    def test_two_income_and_cash_rates_have_readable_labels_and_numeric_proof(self):
        _, content = report_fixture(mixed=False, cash=True)
        report = design(content, ROOT)
        self.assertFalse(logic_figure_plan(content))
        page = html.document_fromstring(report['html'])
        section = page.get_element_by_id('logic-1')
        reading, = section.xpath('.//p[@class="measured-reading"]')
        self.assertIn('营业收入同比', reading.text_content())
        self.assertIn('经营现金流净额同比', reading.text_content())
        self.assertNotIn('利润', reading.text_content())
        covered = report['document']['figure_coverage']['logic-1']
        self.assertEqual(covered['status'], 'numeric_text')
        self.assertEqual(set(covered['data_refs']), {'D1', 'D2', 'D3', 'D4'})
        self.assertEqual(len(reading.xpath('.//a[@data-inspection-observation]')), 2)

    def test_three_comparable_rates_share_one_chart_across_identical_arguments(self):
        task, _ = report_fixture(mixed=False, cash=True)
        for index, source in enumerate(task['source_records']):
            source['parameters']['indipara'].append(dict(indicator='ths_np_atoopc_stock',
                indiparams=list(source['parameters']['indipara'][0]['indiparams'])))
            source['snapshot']['tables'][0]['table']['ths_np_atoopc_stock'] = [10 if index == 0 else 15]
            rehash(source)
        _, content = freeze_report(task)
        logic = content['draft']['logics'][0]
        content['draft']['logics'] = [deepcopy(logic) for _ in range(8)]
        chart, = logic_figure_plan(content)
        self.assertEqual((chart['paper_logic'], chart['kind'], chart['auto_visual']['scheme']),
                         ('logic-1', 'changes', 'diverging_bar'))
        self.assertEqual(chart['reused_by'], ['logic-'+str(index) for index in range(2, 9)])
        calculations = {row['id']: row for row in content['draft']['calculations']}
        self.assertEqual(chart['auto_visual']['data']['values'],
                         [calculations[ref]['value'] for ref in chart['calculation_refs']])
        self.assertEqual(chart['point_refs'], [calculations[ref]['data_refs'] for ref in chart['calculation_refs']])
        self.assertEqual(set(chart['data_refs']), set(logic['data_refs']))

    def test_small_two_company_comparisons_keep_subjects_and_source_cells(self):
        for cash in (False, True):
            task, _ = report_fixture(mixed=False, cash=cash)
            task['objects'].append('002594.SZ')
            for source in list(task['source_records']):
                fields = tuple(source['snapshot']['tables'][0]['table'])
                amounts = tuple(source['snapshot']['tables'][0]['table'][field][0] for field in fields)
                extra = add_record(task, source['parameters']['indipara'][0]['indiparams'][0], amounts, fields=fields)
                extra['parameters']['codes'] = extra['snapshot']['tables'][0]['thscode'] = '002594.SZ'
                rehash(extra)
            _, content = freeze_report(task)
            self.assertFalse(logic_figure_plan(content))
            page = html.document_fromstring(design(content, ROOT)['html'])
            section = page.get_element_by_id('logic-1')
            self.assertIn('600519.SH', section.text_content())
            self.assertIn('002594.SZ', section.text_content())
            facts = {row['id']: row for row in content['draft']['facts']}
            for result in section.xpath('.//*[@data-calculation-inputs]'):
                codes = {facts[ref]['code'] for ref in result.get('data-calculation-inputs').split()}
                self.assertEqual(len(codes), 1)
                self.assertIn(next(iter(codes)), result.text_content())

    def test_one_sentence_agent_saves_both_scopes_without_a_pdf_or_real_call(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            result=run_cohort_research(folder,ROOT);task=result['task'];content=result['paper']['content']
            self.assertEqual(result['operations'],['get_limit_policy']+['basic_data_service']*4)
            self.assertEqual(len(task['draft_report']['facts']),8);self.assertTrue(task['delivery'])
            self.assertEqual(len(content['draft']['logics']),2)
            self.assertFalse(logic_figure_plan(content))
            self.assertFalse(task['draft_acquisition_coverage']['blocks_report'])
            result = design(content, ROOT)
            report = result['document']
            self.assertEqual(len(report['dataset']['rows']), 8)
            self.assertTrue(all(record['status'] == 'numeric_text' for record in report['figure_coverage'].values()))
            page = html.document_fromstring(result['html'])
            for index, label in ((1, '合并报表'), (2, '母公司报表')):
                section = page.get_element_by_id('logic-'+str(index))
                self.assertIn(label, section.text_content())
                self.assertTrue(section.xpath('.//*[@data-calculation-ref]'))
                self.assertFalse(section.xpath('.//table'))


if __name__=='__main__':unittest.main()
