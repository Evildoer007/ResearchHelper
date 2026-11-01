"""Independent arithmetic, applicability and source-scope checks for first drafts."""
import copy
import hashlib
import json
import math
import socket
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.contracts import ContractError, METHOD_REGISTRY, canonical
from researchhelper.draft_analysis import METHODS, execute, analyze, current_analysis,formula_mathml
from researchhelper.draft_sources import observations
from researchhelper.draft_workflow import _write
from researchhelper.drafts import normalize_report,compose,render,source_binding,logic_basis

HK=ROOT/'result/acceptance/one-sentence-research-_kw2k9ap/task.json'
FINANCIAL=ROOT/'result/acceptance/one-sentence-research-q3r0y_qo/task.json'


class DraftArithmeticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hashes={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in (HK,FINANCIAL)}
        cls.hk=json.loads(HK.read_text());cls.financial=json.loads(FINANCIAL.read_text())

    @classmethod
    def tearDownClass(cls):
        assert all(hashlib.sha256(p.read_bytes()).hexdigest()==h for p,h in cls.hashes.items())

    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No real I/O'))
        guard.start();self.addCleanup(guard.stop)

    def test_registry_cannot_be_used_as_a_reviewed_calculation_method(self):
        self.assertFalse(set(METHODS)&set(METHOD_REGISTRY))
        with self.assertRaises(ContractError):execute('__import__',dict(a=1,b=2))

    def test_actual_hk_change_is_minus_5_84_percent_not_7_31_amplitude(self):
        task=copy.deepcopy(self.hk);facts=observations(task);result=analyze(task,facts)
        change=next(row for row in result['results'] if row['method']=='raw_change_v1')
        self.assertEqual((change['display'],change['unit']),('-5.84','%'))
        self.assertEqual(change['data_refs'],['D1','D5'])
        self.assertFalse(change['financial_approval'])

    def test_close_range_is_a_literal_range_not_volatility_or_intraday_amplitude(self):
        result=analyze(self.hk,observations(self.hk))
        bounds=next(row for row in result['results'] if row['method']=='raw_range_v1')
        self.assertEqual((bounds['minimum'],bounds['maximum']),('327.48736','351.417312'))
        self.assertEqual(bounds['unit'],'元')
        self.assertNotIn('振幅',bounds['label']);self.assertNotIn('波动率',bounds['label'])

    def test_actual_financial_growth_and_same_period_ratios_are_reproducible(self):
        result=analyze(self.financial,observations(self.financial))
        values={(row['label'],row['period']):row for row in result['results']}
        self.assertEqual(values[('营业收入原值同比变化','20260630')]['display'],'-7.13')
        self.assertEqual(values[('收入成本原值差占比','20260630')]['display'],'18.85')
        self.assertEqual(values[('报表净利润/收入原值比','20260630')]['display'],'3.58')
        self.assertEqual(values[('经营现金流/报表净利润原值比','20260630')]['display'],'3.03')
        self.assertTrue(all(row['conditional_equal_scale'] for row in result['results']))

    def test_parent_profit_does_not_replace_statement_net_profit(self):
        facts=observations(self.financial)
        for row in facts:
            if row['field']=='ths_np_atoopc_stock':row.update(value=1,raw_value=1)
        result=analyze(self.financial,facts)
        ratios=[row for row in result['results'] if row['method']=='raw_multiple_v1' and row['period']=='20260630']
        self.assertEqual(ratios[0]['display'],'3.03')

    def test_negative_profit_keeps_negative_margin_without_cash_coverage_ratio(self):
        facts=observations(self.financial)
        for row in facts:
            if row['field']=='ths_np_stock' and row['period']=='20260630':row.update(value=-100,raw_value=-100)
        result=analyze(self.financial,facts)
        self.assertFalse(any(row['method']=='raw_multiple_v1' and row['period']=='20260630' for row in result['results']))
        margin=next(row for row in result['results'] if row['method']=='raw_percent_v1' and row['period']=='20260630')
        self.assertLess(margin['value'],0);self.assertTrue(result['notes'])

    def test_nonpositive_prior_year_uses_difference_instead_of_misleading_growth(self):
        facts=observations(self.financial)
        for row in facts:
            if row['field']=='ths_np_stock' and row['period']=='20250630':row.update(value=-100,raw_value=-100)
        result=analyze(self.financial,facts)
        self.assertFalse(any(row['method']=='raw_change_v1' and row['label'].startswith('净利润原值') for row in result['results']))
        self.assertTrue(any(row['method']=='raw_difference_v1' and row['label'].startswith('净利润原值') for row in result['results']))

    def test_different_units_or_statement_bases_are_not_silently_combined(self):
        facts=observations(self.financial)
        for row in facts:
            if row['field']=='ths_ncf_from_oa_stock':row['unit']='万元'
        result=analyze(self.financial,facts)
        self.assertFalse(any(row['method']=='raw_multiple_v1' for row in result['results']))
        self.assertTrue(any(row['method']=='raw_margin_v1' for row in result['results']))

    def test_same_date_conflicting_values_are_not_selected_arbitrarily(self):
        facts=observations(self.hk);conflict=dict(facts[0],id='different',value=999,raw_value=999)
        result=analyze(self.hk,facts+[conflict])
        self.assertEqual(result['results'],[]);self.assertTrue(result['notes'])

    def test_equal_duplicate_numbers_with_different_decimal_precision_are_comparable(self):
        facts=observations(self.hk);duplicate=dict(facts[0],id='same',raw_value='351.417312000')
        result=analyze(self.hk,facts+[duplicate])
        self.assertEqual(len(result['results']),2)

    def test_missing_endpoint_does_not_move_the_start_to_a_later_day(self):
        facts=observations(self.hk);facts[0].update(value=None,raw_value=None)
        result=analyze(self.hk,facts)
        self.assertFalse(any(row['method']=='raw_change_v1' for row in result['results']))
        self.assertTrue(any(row['method']=='raw_range_v1' for row in result['results']))

    def test_nonpositive_stock_close_is_not_treated_as_an_investment_loss(self):
        for value in (0,-1):
            facts=observations(self.hk);facts[-1].update(value=value,raw_value=value)
            result=analyze(self.hk,facts)
            self.assertFalse(any(row['method']=='raw_change_v1' for row in result['results']))
            self.assertTrue(result['notes'])

    def test_foreign_sources_are_not_used(self):
        task=copy.deepcopy(self.hk);facts=observations(task)
        for row in task['source_records']:row['research_version']+=1
        self.assertEqual(analyze(task,facts)['results'],[])

    def test_unknown_fields_do_not_create_speculative_metrics(self):
        facts=observations(self.hk)
        for row in facts:row['field']='unknown_metric'
        self.assertEqual(analyze(self.hk,facts)['results'],[])

    def test_only_finite_registered_inputs_are_executed(self):
        for value in [None,True,float('nan'),float('inf'),'not a number']:
            with self.assertRaises(ContractError):execute('raw_change_v1',dict(a=value,b=2))
        with self.assertRaises(ContractError):execute('raw_percent_v1',dict(a=1,b=0))

    def test_changed_input_invalidates_calculations_without_blocking_prose(self):
        task=copy.deepcopy(self.hk);facts=observations(task);task['draft_analysis']=analyze(task,facts)
        self.assertTrue(current_analysis(task,facts))
        facts[0]=dict(facts[0],value=123,raw_value=123)
        self.assertEqual(current_analysis(task,facts),{})
        result=normalize_report(dict(conclusion='保留正文。',logics=[dict(title='判断',analysis='按新资料继续分析。',calculation_refs=['A1'])]),task,facts)
        self.assertEqual(result['logics'][0]['calculation_refs'],[])
        self.assertEqual(result['conclusion'],'保留正文。')

    def test_model_receives_computed_values_once_without_an_extra_call(self):
        task=copy.deepcopy(self.hk);facts=observations(task);task['draft_analysis']=analyze(task,facts);captured=[]
        class Model:
            def reason(self,task,messages,**options):captured.append(messages);return '{}'
        class Engine:model=Model()
        _write(Engine(),task,facts)
        data=json.loads(captured[0][1]['content'])['data'];self.assertEqual(len(captured),1)
        self.assertEqual(data['calculations'][0]['display'],'-5.84')

    def test_unrelated_uncoded_observation_does_not_hide_valid_arithmetic(self):
        task=copy.deepcopy(self.hk);facts=observations(task)
        facts.append(dict(facts[0],id='D_extra',code='',field='unrelated_metric'))
        task['draft_analysis']=analyze(task,facts)
        current=current_analysis(task,facts)
        self.assertTrue(current)
        self.assertEqual(current['results'][0]['data_refs'],['D1','D5'])
        self.assertEqual(current['results'][0]['display'],'-5.84')

    def test_method_version_change_invalidates_cached_arithmetic(self):
        task=copy.deepcopy(self.hk);facts=observations(task);task['draft_analysis']=analyze(task,facts)
        changed=copy.deepcopy(METHODS['raw_change_v1']);changed['version']=2
        with patch.dict(METHODS,{'raw_change_v1':changed}):self.assertEqual(current_analysis(task,facts),{})

    def test_calculation_reference_opens_its_inputs_and_real_mathml(self):
        task=copy.deepcopy(self.hk);facts=observations(task);task['draft_analysis']=analyze(task,facts)
        report=normalize_report(dict(title='区间观察',conclusion='保留描述。',
            logics=[dict(title='区间变化',analysis='按登记结果说明价格变化。',calculation_refs=['A1'])]),task,facts)
        report.update(facts=facts,calculations=task['draft_analysis']['results'],source_binding=source_binding(task))
        task['draft_report']=report;content=compose(task)
        selected,_=logic_basis(content,content['draft']['logics'][0])
        self.assertEqual([row['id'] for row in selected],['D1','D5'])
        html=render(content,ROOT)
        self.assertIn('<math xmlns=',html);self.assertIn('-5.84',html)
        self.assertNotIn('<details',html)
        self.assertEqual(formula_mathml('unknown'), '')


def nav_fixture(field='unit_nav', values=None, code='510300.SH'):
    """Synthetic contracts only; never a market-data or model-quality claim."""
    from researchhelper.contracts import fingerprint
    task=dict(id='nav-arithmetic-fixture',version=1,input_hash='nav-input',synthetic=True,
        question='核对单位净值读数变化，不把读数当成总回报',entry='ETF',intent='解释变化',
        cutoff='2026-10-31T16:00:00+08:00',research_mode='current',objects=[code],
        source_records=[],materials=[],questions=[],evidence=[],calculations=[],knowledge={})
    values=['4.0000','3.8000','4.4000'] if values is None else values
    dates=['2026-10-'+str(20+i) for i in range(len(values))]
    if field=='unit_nav':
        packets=[('nav-'+str(i),'official','etf_pcf',dict(ts_code=code),
            dict(code=0,data=[dict(ts_code=code,trade_date=day,unit_nav=value)],exchange_origin=dict(raw_token='￥'+str(value))))
            for i,(day,value) in enumerate(zip(dates,values),1)]
    else:
        packets=[('nav-series','ifind','date_sequence',dict(codes=code,functionpara={},
            **(dict(startdate=dates[0],enddate=dates[-1]) if dates else {}),indipara=[dict(indicator=field,indiparams=[''])]),
            dict(errorcode=0,tables=[dict(thscode=code,time=dates,table={field:values})]))]
    for ident,source,operation,params,snapshot in packets:
        task['source_records'].append(dict(id=ident,task_id=task['id'],research_version=1,kind='api',
            source=source,operation=operation,parameters=params,snapshot=snapshot,status='成功待核验',
            hash=fingerprint(snapshot),request_hash=fingerprint(dict(source=source,operation=operation,parameters=params))))
    return task


class DraftFundArithmeticTests(unittest.TestCase):
    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No live I/O'))
        guard.start();self.addCleanup(guard.stop)

    def calculate(self,task):return analyze(task,observations(task))

    def test_pcf_nav_changes_keep_exact_endpoints_and_never_approve_total_return(self):
        task=nav_fixture();before=canonical(task);result=self.calculate(task)
        change=next(row for row in result['results'] if row['method']=='raw_change_v1')
        bounds=next(row for row in result['results'] if row['method']=='raw_range_v1')
        self.assertEqual((change['label'],change['display'],change['period']),('单位净值变化','10.00','2026-10-20至2026-10-22'))
        self.assertEqual(change['inputs'],dict(a='4.0000',b='4.4000'));self.assertEqual(change['data_refs'],['D1','D3'])
        self.assertEqual((bounds['minimum'],bounds['maximum']),('3.8000','4.4000'))
        self.assertIn('不等于持有人总回报',change['condition']);self.assertFalse(change['financial_approval'])
        self.assertEqual(canonical(task),before)

    def test_ifind_nav_uses_the_registered_field_without_inventing_a_currency_unit(self):
        task=nav_fixture('ths_unit_nvnf_fund');result=self.calculate(task)
        self.assertEqual(next(row for row in result['results'] if row['method']=='raw_change_v1')['display'],'10.00')
        self.assertTrue(all(row['conditional_equal_scale'] for row in result['results']))
        self.assertEqual(next(row for row in result['results'] if row['method']=='raw_range_v1')['unit'],'原始单位待核对')
        self.assertTrue(all(row['unit']=='原始单位待核对' for row in observations(task)))

    def test_missing_middle_keeps_valid_endpoints_but_invalid_endpoints_are_not_replaced(self):
        self.assertTrue(any(row['method']=='raw_change_v1' for row in self.calculate(nav_fixture(values=['4',None,'4.4']))['results']))
        for value in (None,0,-1):
            task=nav_fixture(values=['4','3.8',value]);result=self.calculate(task)
            with self.subTest(endpoint=value):
                self.assertFalse(any(row['method']=='raw_change_v1' for row in result['results']))
                self.assertTrue(result['notes']);self.assertEqual(observations(task)[-1]['raw_value'],value)
                bounds=next(row for row in result['results'] if row['method']=='raw_range_v1')
                self.assertGreater(float(bounds['minimum']),0)

    def test_single_nav_is_not_interpolated_or_promoted_to_a_return(self):
        self.assertEqual(self.calculate(nav_fixture(values=['4.1856']))['results'],[])
        self.assertEqual(self.calculate(nav_fixture('accumulated_nav'))['results'],[])

    def test_dividend_counterexample_keeps_negative_level_change_with_explicit_scope(self):
        result=self.calculate(nav_fixture(values=['10','9']))
        change=next(row for row in result['results'] if row['method']=='raw_change_v1')
        self.assertEqual(change['display'],'-10.00');self.assertIn('分红再投资',change['condition'])
        self.assertIn('投资者现金流',change['condition']);self.assertFalse(result['financial_approval'])

    def test_foreign_sources_and_currency_bases_do_not_join(self):
        task=nav_fixture();task['source_records'][1]['task_id']='foreign';task['source_records'][2]['research_version']=2
        self.assertEqual(self.calculate(task)['results'],[])
        from researchhelper.contracts import fingerprint
        task=nav_fixture('ths_unit_nvnf_fund',values=['4','4.4']);source=task['source_records'][0]
        other=copy.deepcopy(source);other['id']='other-currency';other['parameters']['functionpara']={'Currency':'USD'}
        source['snapshot']['tables'][0]['time']=source['snapshot']['tables'][0]['time'][:1]
        source['snapshot']['tables'][0]['table']['ths_unit_nvnf_fund']=['4']
        other['snapshot']['tables'][0]['time']=other['snapshot']['tables'][0]['time'][1:]
        other['snapshot']['tables'][0]['table']['ths_unit_nvnf_fund']=['4.4']
        for row in (source,other):
            row['hash']=fingerprint(row['snapshot']);row['request_hash']=fingerprint({k:row[k] for k in ('source','operation','parameters')})
        task['source_records'].append(other);self.assertEqual(self.calculate(task)['results'],[])

    def test_nav_and_pb_writing_guidance_is_bound_to_actual_objects(self):
        from researchhelper.draft_analysis import interpretation_context
        task=nav_fixture();guide=interpretation_context(task,observations(task));rules={row['id']:row for row in guide['rules']}
        self.assertEqual(rules['nav_scope']['objects'],['510300.SH']);self.assertFalse(guide['delivery_blocking'])
        task=nav_fixture('ths_pb_latest_stock',code='600519.SH');rules=interpretation_context(task,observations(task))['rules']
        self.assertEqual([row['id'] for row in rules],['valuation_scope'])
        task=nav_fixture();task['draft_analysis']=self.calculate(task);captured=[]
        class Model:
            def reason(self,task,messages,**options):captured.append(messages);return '{}'
        class Engine:model=Model()
        _write(Engine(),task,observations(task));payload=json.loads(captured[0][1]['content'])
        self.assertEqual(len(captured),1);self.assertIn('不等于持有人总回报',payload['data']['calculations'][0]['condition'])

    def test_old_calculation_cache_is_invalidated_after_nav_support(self):
        task=nav_fixture();facts=observations(task);task['draft_analysis']=analyze(task,facts)
        self.assertTrue(current_analysis(task,facts));task['draft_analysis']['version']=1
        self.assertEqual(current_analysis(task,facts),{})

    def single_nav_content(self,refs):
        task=nav_fixture(values=['4.1856']);facts=observations(task)
        task['draft_analysis']=analyze(task,facts)
        report=normalize_report(dict(title='单点净值观察',conclusion='仅核对一项已取得的净值。',
            logics=[dict(title='净值所属日与公布日分开',analysis='单一读数不据此推断区间表现。',data_refs=refs)]),task,facts)
        task['draft_report']=dict(report,facts=facts,calculations=[],source_binding=source_binding(task))
        return compose(task)

    def test_single_observation_stays_visible_in_both_reports_without_a_fabricated_plot(self):
        from lxml import html
        from researchhelper.report_designer import design
        content=self.single_nav_content(['D1']);before=canonical(content)
        for profile in ('standard','detailed'):
            with self.subTest(profile=profile):
                result=design(content,ROOT,profile=profile);tree=html.document_fromstring(result['html'])
                rows=tree.xpath('//main//div[contains(@class,"argument-observations")]//tr[@data-observation-id="D1"]')
                self.assertEqual(len(rows),1)
                cells=rows[0].xpath('./th/text()|./td/text()|./td/a/text()')
                self.assertEqual(cells,['510300.SH','单位净值','2026-10-20','4.1856','元','公开来源'])
                self.assertEqual(rows[0].xpath('./td/a/@href'),['#draft-basis-1'])
                self.assertFalse(tree.xpath('//main//*[contains(concat(" ",normalize-space(@class)," ")," chart ")]'))
                coverage=result['document']['figure_coverage']['logic-1']
                self.assertEqual((coverage['status'],coverage['figures'],coverage['tables'],coverage['data_refs']),('numeric_table',0,1,['D1']))
                self.assertEqual(len(result['document']['dataset']['rows']),1)
        self.assertEqual(canonical(content),before)

    def test_unassigned_single_value_appears_in_key_data_without_guessing_a_logic_binding(self):
        from lxml import html
        from researchhelper.report_designer import design
        content=self.single_nav_content([]);result=design(content,ROOT)
        tree=html.document_fromstring(result['html']);data=tree.get_element_by_id('data')
        self.assertIn('4.1856',data.text_content());self.assertIn('2026-10-20',data.text_content())
        self.assertEqual(result['document']['figure_coverage']['logic-1']['status'],'unvisualized')
        self.assertFalse([row for row in result['document']['blocks'] if row['kind']=='chart'])

    def test_report_keeps_all_nav_points_and_missing_cells_in_both_profiles(self):
        from researchhelper.draft_visuals import logic_figure_plan
        from researchhelper.report_designer import design
        task=nav_fixture('ths_unit_nvnf_fund',values=['4',None,'4.4']);facts=observations(task);task['draft_analysis']=analyze(task,facts)
        draft=normalize_report(dict(title='净值读数观察',conclusion='只核对单位净值，不推断持有人回报。',
            logics=[dict(title='净值变化与实际回报分开',analysis='中间原值缺失，保留实际起终读数。',
                data_refs=['D1','D3'],calculation_refs=['A1'])]),task,facts)
        task['draft_report']=dict(draft,facts=facts,calculations=task['draft_analysis']['results'],source_binding=source_binding(task))
        content=compose(task);figures=logic_figure_plan(content)
        nav=next(row for row in figures if row.get('field')=='ths_unit_nvnf_fund')
        self.assertEqual(nav['values'],[4,None,4.4]);self.assertEqual(nav['point_refs'],[['D1'],['D2'],['D3']])
        self.assertEqual(nav['plot_context']['context_data_refs'],['D2'])
        documents=[design(content,ROOT,profile=profile)['document'] for profile in ('standard','detailed')]
        self.assertEqual(documents[0]['dataset'],documents[1]['dataset']);self.assertEqual(len(documents[0]['dataset']['rows']),3)
        self.assertTrue(all(doc['figure_coverage']['logic-1']['figures'] for doc in documents))


if __name__=='__main__':unittest.main()
