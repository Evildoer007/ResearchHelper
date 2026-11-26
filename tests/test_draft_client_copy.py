"""Unknown financial currency proposals do not become client amounts."""
from copy import deepcopy
import socket,tempfile,unittest
from unittest.mock import patch
from tests import ROOT
from tests.test_draft_financial_period import sample
from researchhelper.contracts import canonical,fingerprint,ContractError
from researchhelper.draft_sources import observations,writing_context,client_writing_context
from researchhelper.draft_analysis import analyze
from researchhelper.drafts import normalize_report
from researchhelper.publication_content import client_research_text,draft_logic_title,prepare_draft_client_copy
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from core.session import Session


def fixture():
    task=sample();task.update(draft_source_reader_version=8,draft_client_copy_version=1)
    facts=observations(task);task['draft_analysis']=analyze(task,facts)
    refs=[row['id'] for row in facts]
    proposal=dict(title='降价驱动盈利变化',conclusion='营业收入由100元升至120元，其他条件仍需检验。',
        logics=[dict(title='经营表现',argument=dict(claim='需要检验增长能否持续。',
            evidence='营业收入120元，净利润12元；经营现金流18元。',reasoning='收入并不等于现金回款。'),
            data_refs=refs,calculation_refs=[])],risks=['需求与回款仍有不确定性。'])
    return task,facts,proposal


class DraftClientCopyTests(unittest.TestCase):
    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No external calls'))
        guard.start();self.addCleanup(guard.stop)

    def test_client_text_preserves_boundary_and_remaps_checked_numbers(self):
        draft=('金额仅以已登记的同比增速呈现。收入增长54.8%，该项数据缺少可核对的依据，'
               '经营现金流增长2.6%。销售回款仍需结合现金流量表附注判断。')
        links = [dict(start=draft.index(text), end=draft.index(text)+len(text), original=text)
                 for text in ('54.8%', '2.6%')]
        text, projected = client_research_text(draft, links)
        self.assertEqual(text, '收入增长54.8%，经营现金流增长2.6%。销售回款仍需结合现金流量表附注判断。')
        self.assertEqual([text[ref['start']:ref['end']] for ref in projected], ['54.8%', '2.6%'])
        self.assertEqual(draft_logic_title('', '经营现金流增长2.6%，表明现金增量落后于利润增量。'),
                         '现金增量落后于利润增量')

    def test_client_editorial_pass_preserves_the_specific_risk_and_evidence(self):
        note = '收入同比增长3.55%。后续表现仍有待进一步观察。若营业成本继续上升，毛利率将低于25%。以上分析仅供参考。'
        number = '25%'
        left = note.index(number)
        filtered, links = client_research_text(note, [dict(start=left, end=left+len(number))], editorial=True)
        self.assertEqual(filtered, '收入同比增长3.55%。若营业成本继续上升，毛利率将低于25%。')
        self.assertEqual(filtered[links[0]['start']:links[0]['end']], number)

    def test_macro_headline_and_named_subseries_have_separate_readings(self):
        task, _, _ = fixture()
        task.update(draft_client_copy_version=2, entry='宏观', objects=[],
                    question='研究中国2026年1月至9月CPI同比及消费品、食品价格的分化。')
        task['source_records'] = [dict(id='economic', task_id=task['id'], research_version=task['version'],
                                      source='ifind', operation='edb_service')]
        observations = [
            ('M000000001', '中国:CPI:当月同比', '2026-01-31', 0.2),
            ('M000000001', '中国:CPI:当月同比', '2026-07-31', 0.5),
            ('M000000001', '中国:CPI:当月同比', '2026-09-30', 0.9),
            ('M000000002', '中国:CPI:消费品:当月同比', '2026-09-30', 1.0),
            ('M000000003', '中国:CPI:食品:当月同比', '2026-09-30', -1.7),
        ]
        facts = [dict(id=f'D{index}', artifact_id='economic', code=code, field='value', label=label,
                      value=value, raw_value=value, unit='%', period=period, axis_role='time', series_basis=code)
                 for index, (code, label, period, value) in enumerate(observations, 1)]
        task['draft_analysis'] = analyze(task, facts)
        valid = ('2026年1月至9月，中国CPI当月同比由0.2%升至0.9%。'
                 '2026年9月中国消费品CPI当月同比为1.0%。2026年9月中国食品CPI当月同比为-1.7%。')
        false_extreme = '2026年7月中国CPI当月同比0.5%为区间低点。'
        true_extreme = '2026年1月中国CPI当月同比0.2%为区间低点。'
        wrong_series = '2026年9月中国消费品CPI当月同比为0.9%。'
        proposal = dict(title='中国CPI价格跟踪', conclusion=valid+false_extreme+true_extreme+wrong_series)
        report = normalize_report(proposal, task, facts)
        self.assertIn(valid, report['conclusion'])
        self.assertIn('2026年7月中国CPI当月同比0.5%。', report['conclusion'])
        self.assertIn(true_extreme, report['conclusion'])
        self.assertNotIn(wrong_series, report['conclusion'])
        self.assertTrue(any(row['reason']=='draft_economic_extreme_contradicted' and row['data_refs']==['D2','D1']
                            for row in report['copy_adjustments']))

    def test_unknown_amounts_are_replaced_by_registered_relative_comparisons(self):
        task,facts,value=fixture();before=canonical((task,facts,value))
        result,changes=prepare_draft_client_copy(task,value,facts)
        text=canonical(result)
        self.assertNotIn('100元',text);self.assertNotIn('120元',text)
        self.assertIn('20.00%',text);self.assertIn('-10.00%',text)
        self.assertIn('其他条件仍需检验',text);self.assertIn('收入并不等于现金回款',text)
        self.assertTrue(all(row.get('financial_approval') is False for row in changes))
        self.assertEqual(canonical((task,facts,value)),before)

    def test_normalization_retains_original_clause_and_comparison_references(self):
        task,facts,value=fixture();result=normalize_report(value,task,facts)
        self.assertIn('copy_adjustments',result)
        self.assertTrue(any(row['original'].startswith('营业收入由100元') for row in result['copy_adjustments']))
        self.assertTrue(result['logics'][0]['calculation_refs'])
        self.assertEqual(result['title'],'600519.SH研究报告')
        self.assertEqual(value['title'],'降价驱动盈利变化')

    def test_explicit_factual_checking_keeps_literal_raw_values_without_adopting_currency(self):
        task,facts,value=fixture();result,changes=prepare_draft_client_copy(task,value,facts,'fact')
        self.assertIn('100（单位未明）',result['conclusion'])
        self.assertIn('120（单位未明）',result['conclusion'])
        self.assertNotIn('100元',result['conclusion']);self.assertTrue(changes)

    def test_known_units_percentages_and_stock_prices_are_unchanged(self):
        task,facts,value=fixture()
        for row in facts:row['unit']='元'
        task['draft_analysis']=analyze(task,facts)
        result,changes=prepare_draft_client_copy(task,value,facts)
        self.assertEqual(result,value);self.assertEqual(changes,[])
        task,facts,value=fixture();value['conclusion']='收盘价100元，收入同比20.00%，区间价格不代表总回报。'
        result,changes=prepare_draft_client_copy(task,value,facts)
        self.assertEqual(result['conclusion'],value['conclusion'])

    def test_unrelated_budget_and_unmatched_numbers_are_not_rewritten(self):
        task,facts,value=fixture();value['conclusion']='用户设定预算100元，假设额度999万元。'
        result,_=prepare_draft_client_copy(task,value,facts)
        self.assertEqual(result['conclusion'],value['conclusion'])

    def test_only_named_amount_phrase_changes_and_surrounding_analysis_survives(self):
        task,facts,value=fixture()
        value['conclusion']='收盘价100元与营业收入120元同时观察，收入改善尚需业务证据，不推定因果。'
        result,_=prepare_draft_client_copy(task,value,facts)
        self.assertIn('收盘价100元',result['conclusion'])
        self.assertIn('营业收入同比20.00%同时观察',result['conclusion'])
        self.assertIn('收入改善尚需业务证据，不推定因果',result['conclusion'])

    def test_missing_comparison_does_not_invent_growth(self):
        task,facts,value=fixture();facts=facts[3:];task['draft_analysis']=analyze(task,facts)
        value['conclusion']='营业收入120元，其他条件仍需检验。'
        result,changes=prepare_draft_client_copy(task,value,facts)
        self.assertIn('120（单位未明）',result['conclusion'])
        self.assertNotIn('20.00%',result['conclusion']);self.assertTrue(changes)

    def test_rounding_of_currency_proposal_does_not_approve_units(self):
        task,facts,value=fixture();facts[0].update(value=170899152276.34,raw_value=170899152276.34)
        facts[3].update(value=168838102514.79,raw_value=168838102514.79)
        task['draft_analysis']=analyze(task,facts);value['conclusion']='营业收入由1708.99亿元降至1688.38亿元。'
        result,changes=prepare_draft_client_copy(task,value,facts)
        self.assertIn('-1.21%',result['conclusion']);self.assertNotIn('亿元',result['conclusion'])
        self.assertEqual(facts[0]['unit'],'原始单位待核对');self.assertTrue(changes)

    def test_foreign_source_cannot_supply_a_replacement_comparison(self):
        task,facts,value=fixture()
        for source in task['source_records']:source['task_id']='foreign'
        result,changes=prepare_draft_client_copy(task,value,facts)
        self.assertEqual(result,value);self.assertEqual(changes,[])

    def test_legacy_policy_keeps_frozen_copy_unchanged(self):
        task,facts,value=fixture();task.pop('draft_client_copy_version')
        result,changes=prepare_draft_client_copy(task,value,facts)
        self.assertEqual(result,value);self.assertEqual(changes,[])
        self.assertEqual(client_writing_context(task,facts),writing_context(task,facts))

    def test_client_writer_receives_relative_arithmetic_and_retains_workpaper_raw_values(self):
        task,facts,value=fixture();before=canonical((task,facts))
        context=client_writing_context(task,facts)
        self.assertEqual(len(context['public_value_scope']['relative_amount_refs']),6)
        self.assertTrue(all('value' not in group['columns'] for group in context['observations']))
        self.assertTrue(all('raw_value_available' in group['columns'] for group in context['observations']))
        self.assertTrue(context['calculations'])
        self.assertEqual(canonical((task,facts)),before)
        self.assertEqual([row['raw_value'] for row in facts],[100,10,20,120,12,18])

    def test_explicit_comparison_writer_still_receives_literal_values(self):
        task,facts,value=fixture();task.update(draft_question_scope_version=2,
            questions=[dict(id='q1',kind='comparison',question='直接比较财务原值',required=True)])
        context=client_writing_context(task,facts)
        self.assertEqual(context['public_value_scope']['relative_amount_refs'],[])
        self.assertTrue(all('value' in group['columns'] for group in context['observations']))

    def test_invalid_policy_is_not_silently_used(self):
        task,facts,value=fixture();task['draft_client_copy_version']=True
        with self.assertRaises(ContractError):client_writing_context(task,facts)
        with self.assertRaises(ContractError):prepare_draft_client_copy(task,value,facts)

    def test_missing_units_do_not_block_a_saved_report(self):
        with tempfile.TemporaryDirectory() as folder:
            engine=ResearchAgent(ResearchArchive(folder),ROOT,offline=True,draft_first=True,require_argument_support=True)
            try:
                # Keep this case outside the separately reviewed Moutai scope.
                task=engine.create('比较600000.SH财务，形成报告',objects=['600000.SH'])
                wire,facts,value=fixture()
                from tests.test_draft_financial_period import rehash
                for source in wire['source_records']:
                    source.update(task_id=task['id'],research_version=task['version'])
                    source['parameters']['codes']='600000.SH'
                    source['snapshot']['tables'][0]['thscode']='600000.SH'
                    rehash(source)
                task['source_records']=wire['source_records'];engine.archive.save(task)
                done=engine.start(task['id'],dict(data_requests=[],draft_report=value))
                self.assertTrue(done.get('delivery'),done.get('error'))
                self.assertEqual(len(done['draft_report']['facts']),6)
                self.assertTrue(done['draft_report'].get('copy_adjustments'))
                self.assertEqual(done['ledger']['used']['model_calls'],0)
                from lxml import html
                from researchhelper.report_designer import render_html

                saved = engine.archive.report(task['id'], 1)
                client = html.document_fromstring(render_html(saved['content'], ROOT, profile='standard'))
                detailed = html.document_fromstring(render_html(saved['content'], ROOT, profile='detailed'))
                self.assertNotIn('未确认', client.find('.//main').text_content())
                self.assertIn('20.00%', client.find('.//main').text_content())
                self.assertTrue(detailed.xpath('//main//table'))
            finally:engine.close_runtime()

    def test_same_numbers_from_another_measure_period_or_security_do_not_bind(self):
        from tests.test_client_report_audience import fixture as report_fixture

        task,_=report_fixture()
        task['draft_client_copy_version']=2
        facts=task['draft_report']['facts']
        report=normalize_report(dict(title='经营与价格',
            conclusion='2025年营业收入100亿元。2026-10-27收盘价1335元。2026-10-28收盘价1335元。归母净利润同比下降10.00%。',
            logics=[dict(title='观察两只股票',
                analysis='600519.SH收盘价1335元和000001.SZ收盘价1335元。扣非归母净利润同比下降10.00%。',
                data_refs=['D11','D2','D5'])]),task,facts)
        self.assertNotIn('100亿元',report['conclusion'])
        self.assertNotIn('10000000000',report['conclusion'])
        self.assertNotIn('2026-10-27收盘价1335元',report['conclusion'])
        self.assertIn('2026-10-28收盘价1335元',report['conclusion'])
        self.assertIn('归母净利润同比下降10.00%',report['conclusion'])
        self.assertIn('600519.SH收盘价1335元',report['logics'][0]['analysis'])
        self.assertNotIn('000001.SZ收盘价1335元',report['logics'][0]['analysis'])
        self.assertNotIn('扣非归母净利润同比下降10.00%',report['logics'][0]['analysis'])

    def test_cited_original_supports_whole_number_with_the_same_measure_and_period(self):
        task,_,_=fixture()
        task['draft_client_copy_version']=2
        source=dict(id='filing',kind='material',task_id=task['id'],research_version=task['version'],
            parts=[dict(locator='第2页',text='2025年营业收入同比增长19%。')])
        task['materials']=[source]
        proposal=dict(title='营业收入',conclusion='经营情况以已取得原件为依据。',
            logics=[dict(title='原件口径',source_refs=['filing'],
                analysis='2025年营业收入较上年同期增加19%。2025年营业收入同比增长9%。'
                         '2024年营业收入同比增长19%。2025年营业收入环比增长19%。'
                         '2025年营业收入同比下降19%。')])
        result=normalize_report(proposal,task,[])
        text=result['logics'][0]['analysis']
        self.assertIn('2025年营业收入较上年同期增加19%',text)
        self.assertNotIn('同比增长9%',text)
        self.assertNotIn('2024年营业收入同比增长19%',text)
        self.assertNotIn('环比增长19%',text)
        self.assertNotIn('同比下降19%',text)
        source['task_id']='foreign'
        foreign=normalize_report(proposal,task,[])
        self.assertNotIn('19%',foreign['logics'][0]['analysis'])

    def test_registered_yoy_and_source_rate_do_not_change_comparison_basis(self):
        from tests.test_client_report_audience import fixture as report_fixture

        task,_=report_fixture()
        task['draft_client_copy_version']=2
        facts=task['draft_report']['facts']
        task['source_records'].append(dict(id='economic', task_id=task['id'], research_version=task['version'],
            source='ifind', operation='edb_service'))
        facts.extend(dict(id=ident,artifact_id='economic',code=code,field='value',label=label,
            value=value,raw_value=value,unit='%',period='2026-09-30',axis_role='time')
            for ident,code,label,value in [('D13','M000000001','CPI当月同比',0.4),
                                           ('D14','M000000002','CPI当月环比',0.3)])
        task['draft_analysis']=analyze(task,facts)
        conclusion=('2025年归母净利润环比下降10.00%。归母净利润同比下降10.00%且收盘价1335元。'
                    '2026年9月CPI同比为0.40%。2026年9月CPI环比为0.30%。'
                    '2026年9月CPI较上季上升0.30%。')
        proposal=dict(title='经营与价格',conclusion=conclusion,logics=[dict(title='经营变化',
            analysis='2025年归母净利润环比由1.00%降至10.00%。',data_refs=['D2','D5'])])
        report=normalize_report(proposal,task,facts)
        self.assertIn('归母净利润同比下降10.00%且收盘价1335元',report['conclusion'])
        self.assertIn('2026年9月CPI同比为0.40%',report['conclusion'])
        self.assertIn('2026年9月CPI环比为0.30%',report['conclusion'])
        self.assertNotIn('环比下降10.00%',report['conclusion'])
        self.assertNotIn('较上季上升0.30%',report['conclusion'])
        self.assertNotIn('10.00%',report['logics'][0]['analysis'])

        task,facts,proposal=fixture()
        task['draft_client_copy_version']=2
        proposal['conclusion']='2025年净利润同比增长10.00%。2025年净利润同比增长20.00%。'
        report=normalize_report(proposal,task,facts)
        self.assertNotIn('同比增长10.00%',report['conclusion'])
        self.assertIn('同比增长20.00%',report['conclusion'])

    def test_financial_copy_binds_issuer_and_statement_before_matching_the_value(self):
        from tests.test_draft_financial_period import add_record, rehash, sample
        task = sample()
        task.update(draft_client_copy_version=2, source_records=[],
                    question='比较甲股份600519.SH与乙股份000001.SZ的营业收入',
                    objects=['600519.SH', '000001.SZ'])
        field = 'ths_revenue_stock'
        series = [('600519.SH', '1', 100, 120), ('600519.SH', '2', 200, 260),
                  ('600519.SH', '3', 300, 360), ('000001.SZ', '1', 200, 210)]
        for code, statement, base, current in series:
            for period, value in [('20241231', base), ('20251231', current)]:
                record = add_record(task, period, (value,), fields=(field,))
                record['parameters']['codes'] = record['snapshot']['tables'][0]['thscode'] = code
                record['parameters']['indipara'][0]['indiparams'][1] = statement
                rehash(record)
        facts = observations(task)
        task['draft_analysis'] = analyze(task, facts)
        scoped = [
            '甲股份合并报表2025年营业收入同比增长20.00%',
            '甲股份母公司报表2025年营业收入同比增长30.00%',
            '甲股份合并报表（调整）2025年营业收入同比增长20.00%',
            '乙股份合并报表2025年营业收入同比增长5.00%',
        ]
        incorrect = [
            '2025年营业收入同比增长20.00%',
            '甲股份2025年营业收入同比增长20.00%',
            '甲股份母公司口径2025年营业收入同比增长20.00%',
            '乙股份（600519.SH）合并报表2025年营业收入同比增长20.00%',
        ]
        proposal = dict(title='两家公司的可比收入', conclusion='。'.join(scoped+incorrect)+'。',
            logics=[dict(title='对照两家', analysis=scoped[0]+'且'+scoped[3]+'。', data_refs=['D2','D8']),
                    dict(title='从已指定的母公司记录取数',
                         analysis='2025年营业收入同比增长30.00%。', data_refs=['D4']),
                    dict(title='错引其它公司的原值', analysis='乙股份合并报表2025年营业收入120元。', data_refs=['D2']),
                    dict(title='核对母公司原值', analysis='甲股份母公司报表2025年营业收入260元。', data_refs=['D4'])])
        before = canonical((task, facts, proposal))
        report = normalize_report(proposal, task, facts)
        self.assertEqual(report['conclusion'].split('。')[:4], scoped)
        self.assertEqual(report['conclusion'].count('缺少可核对的读数'), 4)
        pairs = [link['data_refs'] for link in report['quantity_links']['conclusion']['links']]
        self.assertEqual(pairs, [['D1','D2'], ['D3','D4'], ['D5','D6'], ['D7','D8']])
        self.assertEqual(report['logics'][0]['analysis'], scoped[0]+'且'+scoped[3]+'。')
        self.assertIn('30.00%', report['logics'][1]['analysis'])
        self.assertNotIn('120', report['logics'][2]['analysis'])
        self.assertFalse(report['logics'][2].get('quantity_links'))
        self.assertIn('甲股份母公司报表2025年营业收入同比30.00%', report['logics'][3]['analysis'])
        self.assertEqual(canonical((task, facts, proposal)), before)

    def test_financial_disclosure_preserves_issuer_and_statement_in_the_paraphrase(self):
        task, _, _ = fixture()
        task.update(draft_client_copy_version=2, question='比较甲股份600519.SH和乙股份000001.SZ',
                    objects=['600519.SH', '000001.SZ'])
        task['materials'] = [dict(id='filing', kind='material', task_id=task['id'], research_version=task['version'],
            parts=[dict(locator='第2页', text='甲股份（600519.SH）合并报表2025年归属于母公司所有者的净利润同比增长19%。')])]
        legitimate = '甲股份合并报表2025年归母净利润较上年同期增加19%'
        checks = [legitimate, '乙股份合并报表2025年归母净利润同比增长19%',
                  '甲股份母公司报表2025年归母净利润同比增长19%']
        report = normalize_report(dict(title='核对公告', conclusion='按原件核对利润口径。', logics=[
            dict(title='公告原值', analysis='。'.join(checks)+'。', source_refs=['filing'])]), task, [])
        logic = report['logics'][0]
        self.assertIn(legitimate, logic['analysis'])
        self.assertEqual(logic['analysis'].count('19%'), 1)
        self.assertEqual(logic['analysis'].count('缺少可核对的读数'), 2)
        self.assertEqual([link['kind'] for link in logic['quantity_links']['analysis']['links']], ['document_excerpt'])

    def test_saved_financial_amount_links_only_to_the_named_sample_and_period(self):
        from lxml import html
        from researchhelper.report_designer import render_html
        from tests.test_draft_financial_period import add_record

        actual = '600519.SH合并报表2026年上半年营业收入为120元。'
        forecast = '预计600519.SH合并报表2026年上半年营业收入为120元。'
        unassigned = '2026年上半年行业样本营业收入为120元。'
        with tempfile.TemporaryDirectory() as folder:
            session = Session(folder)
            try:
                task = session.create('核对行业样本600519.SH两个报告期的营业收入。',
                                      objects=['600519.SH'], research_mode='current')
                task['synthetic'] = True
                for day in ('20260630', '20251231'):
                    add_record(task, day, (120,), fields=('ths_revenue_stock',))
                session.archive.save(task)
                saved = session.draft(task['id'], report=dict(title='样本财务核对', conclusion=actual+forecast+unassigned,
                    logics=[dict(title='已取金额', analysis=actual), dict(title='样本范围', analysis=unassigned)]))
                self.assertTrue(saved.get('delivery'), saved.get('error'))
                draft = saved['draft_report']
                self.assertEqual(draft['logics'][0]['data_refs'], ['D1'])
                self.assertNotIn('120元', draft['logics'][1]['analysis'])
                self.assertNotIn('quantity_links', draft['logics'][1])
                self.assertEqual(saved['ledger']['used']['model_calls'], 0)
                content = session.archive.report(saved['id'], 1)['content']
                for profile in ('standard', 'detailed'):
                    tree = html.parse(str(session.export(saved['id'], profile=profile))).getroot()
                    summary = tree.get_element_by_id('summary')
                    link, = summary.xpath('.//a[@class="quantity-link"]')
                    self.assertEqual(link.text_content(), '120元')
                    self.assertIn(actual+forecast, summary.text_content())
                    detail = tree.get_element_by_id(link.get('href')[1:])
                    self.assertEqual(detail.xpath('.//tr/@data-observation-id'), ['D1'])
                    self.assertIn('20260630', detail.text_content())
                    self.assertIn('iFinD', detail.text_content())

                # Even a re-hashed report cannot attach an altered cell as an original.
                altered = deepcopy(content)
                draft = altered['draft']
                draft['facts'][0]['locator']['row'] = 10
                draft['quantity_links']['conclusion']['links'][0]['row_hash'] = fingerprint(draft['facts'][0])
                altered['content_hash'] = fingerprint({key:value for key,value in altered.items() if key != 'content_hash'})
                tree = html.document_fromstring(render_html(altered, ROOT))
                self.assertFalse(tree.get_element_by_id('summary').xpath('.//a[@class="quantity-link"]'))
            finally:
                session.agent.close_runtime()

    def test_nav_readings_cannot_supply_investor_returns_in_prose_or_figure(self):
        from tests.test_client_report_audience import fixture as report_fixture
        task, _ = report_fixture()
        task.update(draft_client_copy_version=2, entry='ETF', objects=['510300.SH'],
                    question='核对510300.SH的单位净值与投资收益口径')
        facts = [dict(id=f'D{index}',artifact_id='market',code='510300.SH',field='ths_unit_nvnf_fund',
                      label='单位净值',period=day,value=value,raw_value=value,unit='元',axis_role='time')
                 for index,(day,value) in enumerate((('2026-10-27',1),('2026-10-28',1.2)),1)]
        task['draft_analysis'] = analyze(task,facts)
        span = '2026-10-27至2026-10-28'
        good = span+'单位净值上涨20.00%（未计投资回报）。'
        wrong = [(name,span+name+'上涨20.00%。') for name in ('持有收益率','投资回报率','总收益率')]
        wrong += [('区间变化',span+'上涨20.00%（分红再投资回报）。'),
                  ('单位净值收益率',span+'上涨20.00%。')]
        proposal = dict(title='基金原值',conclusion='区分数据口径。',logics=[
            dict(title='单位净值变化',analysis=good,data_refs=['D1','D2']),
            *[dict(title=title,analysis=claim,data_refs=['D1','D2']) for title,claim in wrong]])
        report = normalize_report(proposal,task,facts)
        self.assertEqual(report['logics'][0]['analysis'],good)
        self.assertFalse(report['logics'][0].get('off_scope_data_refs'))
        for logic in report['logics'][1:]:
            self.assertNotIn('20.00%',logic['analysis'])
            self.assertEqual(logic['off_scope_data_refs'],['D1','D2'])
            self.assertEqual(logic['data_refs'],['D1','D2'])

    def test_individual_sample_does_not_supply_industry_totals_or_other_sectors(self):
        from tests.test_draft_financial_period import sample
        task = sample()
        task.update(draft_client_copy_version=2,entry='行业',question='选择甲股份600519.SH作为行业样本')
        actual = '非银金融行业整体2025年营业收入同比增长19%。'
        task['materials'] = [dict(id='filing',kind='material',task_id=task['id'],research_version=task['version'],
                                  parts=[dict(locator='第2页',text=actual)])]
        facts = observations(task)
        task['draft_analysis'] = analyze(task,facts)
        good = '所选公司的2025年营业收入同比增长20.00%，不代表全行业。'
        claim = '2025年营业收入同比增长20.00%。'
        wrong_industry = '房地产行业整体2025年营业收入同比增长19%。'
        report = normalize_report(dict(title='样本与行业比较',conclusion='分别核对口径。',logics=[
            dict(title='样本观察',analysis=good,data_refs=['D1','D4']),
            dict(title='非银金融行业整体',analysis=claim,data_refs=['D1','D4']),
            dict(title='行业汇总原文',analysis=actual+wrong_industry,source_refs=['filing'])]),task,facts)
        self.assertEqual(report['logics'][0]['analysis'],good)
        self.assertFalse(report['logics'][0].get('off_scope_data_refs'))
        self.assertIn('行业汇总营业收入同比缺少可核对的读数',report['logics'][1]['analysis'])
        self.assertTrue({'D1','D4'}<=set(report['logics'][1]['off_scope_data_refs']))
        self.assertIn(actual,report['logics'][2]['analysis'])
        self.assertNotIn(wrong_industry,report['logics'][2]['analysis'])

    def test_component_contribution_does_not_borrow_a_headline_change(self):
        from tests.test_client_report_audience import fixture as report_fixture

        task, _ = report_fixture()
        task.update(draft_client_copy_version=2, entry='宏观', question='区分CPI总读数与食品贡献', objects=[])
        next(record for record in task['source_records'] if record['id'] == 'market')['operation'] = 'edb_service'
        facts = [dict(id=f'D{index}', artifact_id='market', source='ifind', source_operation='edb_service',
                      code=code, label=label, field='value', period=period, value=value, raw_value=value,
                      unit=unit, axis_role='time')
                 for index, (code, label, period, value, unit) in enumerate((
                     ('M0001', '中国:CPI:当月同比', '2026-08-31', 0.9, '%'),
                     ('M0001', '中国:CPI:当月同比', '2026-09-30', 1.0, '%'),
                     ('M0002', '中国:CPI:食品:当月同比贡献', '2026-08-31', 0.05, '百分点'),
                     ('M0002', '中国:CPI:食品:当月同比贡献', '2026-09-30', 0.10, '百分点'),
                     ('M0003', '中国:CPI:非食品:当月同比贡献', '2026-09-30', 0.10, '百分点'),
                 ), 1)]
        task['draft_analysis'] = analyze(task, facts)
        headline = '最近两期CPI读数上升0.10个百分点。'
        unsupported = '食品价格拉动CPI上升0.10个百分点。'
        exact = '2026年9月中国:CPI:食品:当月同比贡献为0.10个百分点。'
        different = '2026年9月CPI读数上升0.10个百分点来自食品价格。'
        preface = '受食品价格影响，CPI上升0.10个百分点。'
        items = [(title, body, refs) for title, body, refs in (
            ('总读数', headline, ['D1', 'D2']),
            ('食品价格贡献', unsupported, ['D1', 'D2']),
            ('食品价格贡献', '上升0.10个百分点。', ['D1', 'D2']),
            ('食品价格贡献', exact, ['D1', 'D2', 'D4', 'D5']),
            ('单一数据与分项', headline + unsupported, ['D1', 'D2']),
            ('驱动', different, ['D1', 'D2']),
            ('驱动', preface, ['D1', 'D2']),
            ('食品价格贡献', exact, ['D5']),
            ('食品价格贡献', exact.replace('9月', '8月'), ['D4']),
            ('食品价格贡献', exact.replace('9月', '第三季度'), ['D4']),
        )]
        report = normalize_report(dict(title='CPI观察', conclusion=headline+unsupported,
            logics=[dict(title=title, analysis=analysis, data_refs=refs) for title, analysis, refs in items]), task, facts)
        self.assertEqual(report['conclusion'].count('0.10个百分点'), 1)
        logics = report['logics']
        self.assertEqual(logics[0]['analysis'], headline)
        self.assertEqual(logics[3]['analysis'], exact)
        self.assertEqual(set(logics[3]['off_scope_data_refs']), {'D1', 'D2', 'D5'})
        self.assertIn(headline, logics[4]['analysis'])
        self.assertEqual(set(logics[4]['off_scope_data_refs']), {'D3', 'D4', 'D5'})
        for index in (1, 2, 5, 6, 7, 8, 9):
            self.assertNotIn('0.10个百分点', logics[index]['analysis'])
            self.assertIn('分项贡献缺少可核对的读数', logics[index]['analysis'])
            self.assertEqual(set(logics[index]['off_scope_data_refs']), {row['id'] for row in facts})
        self.assertEqual(facts[3]['raw_value'], 0.10)

    def test_document_attribution_preserves_the_whole_original_with_the_named_driver(self):
        task, _, _ = fixture()
        task.update(draft_client_copy_version=2, entry='宏观', objects=[])
        original = '2026年9月非食品价格下降0.4%，影响CPI下降约0.08个百分点'
        task['materials'] = [dict(id='release', kind='material', task_id=task['id'], research_version=task['version'],
            parts=[dict(locator='第1页', text='导语。'+original+'。下一句。')])]
        report = normalize_report(dict(title='CPI披露', conclusion='核对公告原句。', logics=[
            dict(title='非食品贡献', analysis=original+'。', source_refs=['release']),
            dict(title='食品贡献', analysis=original.replace('非食品', '食品')+'。', source_refs=['release']),
            dict(title='总读数', analysis='CPI下降约0.08个百分点。', source_refs=['release']),
        ]), task, [])
        first, swapped, headline = report['logics']
        self.assertEqual(first['analysis'], original+'。')
        self.assertNotIn('0.4%', swapped['analysis'])
        self.assertNotIn('0.08个百分点', swapped['analysis'])
        self.assertNotIn('0.08个百分点', headline['analysis'])
        link = first['quantity_links']['analysis']['links'][-1]
        self.assertEqual(link['original'], '0.08个百分点')
        self.assertEqual(link['kind'], 'document_excerpt')
        text = task['materials'][0]['parts'][0]['text']
        self.assertEqual(text[link['document']['start']:link['document']['end']], original)

    def test_missing_facts_still_save_and_export_with_the_hypothesis_and_original(self):
        from lxml import html

        with tempfile.TemporaryDirectory() as folder:
            session=Session(folder)
            try:
                task=session.create('分析600519.SH价格与估值',objects=['600519.SH'])
                raw='收盘价999元。市盈率21.90。若PE达到28倍则上涨概率80%。现金流按999元/日计算。'
                report=dict(title='估值与价格',conclusion=raw,
                    logics=[dict(title='判断条件',analysis='盈利兑现影响估值支撑。')])
                saved=session.draft(task['id'],report=report,data_requests=[])
                self.assertTrue(saved.get('delivery'),saved.get('error'))
                self.assertEqual(saved['draft_report']['facts'],[])
                self.assertTrue(any(item['payload']['conclusion']==raw for item in saved['draft_submissions']))
                self.assertTrue(all(saved['ledger']['used'][key]==0 for key in ('model_calls','data_requests')))
                for profile in ('standard','detailed'):
                    path=session.export(task['id'],profile=profile)
                    body=html.parse(str(path)).find('.//main').text_content()
                    self.assertIn('若PE达到28倍',body)
                    self.assertIn('盈利兑现影响估值支撑',body)
                    for unsupported in ('999元','21.90','80%'):
                        self.assertNotIn(unsupported,body)
            finally:session.agent.close_runtime()

    def test_saved_report_distinguishes_same_value_annual_ytd_and_single_quarter(self):
        from lxml import html
        from tests.test_draft_financial_period import add_record, rehash
        from researchhelper.report_designer import design

        kept = ('2025年全年营业收入同比增长20.00%', '2025年前三季度营业收入同比增长20.00%',
                '2025年前三季度较2024年同期营业收入同比增长20.00%',
                '2025年1月1日至2025年9月30日营业收入同比增长20.00%')
        unlinked = ('营业收入同比增长20.00%', '若营业收入同比增长20.00%则观察现金回款',
                    '预计2025年全年营业收入同比增长20.00%')
        rejected = ('2025年上半年营业收入同比增长20.00%', '2025年第三季度营业收入同比增长20.00%',
                    '2025Q3营业收入同比增长20.00%', '第三季度营业收入同比增长20.00%',
                    '2025年7至9月营业收入同比增长20.00%',
                    '2025年前三季度较2024年全年营业收入同比增长20.00%',
                    '2025年上半年营业收入120元')
        with tempfile.TemporaryDirectory() as folder:
            session = Session(folder)
            try:
                task = session.create('比较600519.SH各期间营业收入，核对单季和累计', objects=['600519.SH'])
                task['synthetic'] = True
                for day, value in (('20251231', 120), ('20241231', 100), ('20250930', 96), ('20240930', 80)):
                    add_record(task, day, (value,), fields=('ths_revenue_stock',))
                session.archive.save(task)
                raw = '。'.join(kept + unlinked + rejected) + '。'
                proposal = dict(title='各期间收入变化', conclusion=raw,
                    logics=[dict(title='期间口径', analysis=kept[1]+'。', data_refs=['D3'])])
                saved = session.draft(task['id'], report=proposal, data_requests=[])
                self.assertTrue(saved.get('delivery'), saved.get('error'))
                self.assertEqual([row['raw_value'] for row in saved['draft_report']['facts']], [120,100,96,80])
                self.assertTrue(any(row['payload']['conclusion']==raw for row in saved['draft_submissions']))
                for profile in ('standard', 'detailed'):
                    file = session.export(task['id'], profile=profile)
                    tree = html.parse(str(file))
                    body = tree.find('.//main').text_content()
                    for expression in kept + unlinked:
                        self.assertIn(expression, body)
                    for expression in rejected:
                        self.assertNotIn(expression, body)
                    links = tree.getroot().get_element_by_id('summary').xpath('.//a[@class="quantity-link"]')
                    self.assertEqual([link.text_content() for link in links], ['20.00%'] * 4)
                    annual, ytd, *same_ytd = [link.get('href') for link in links]
                    self.assertNotEqual(annual, ytd)
                    self.assertEqual(same_ytd, [ytd, ytd])
                    for target, refs, dates in ((annual, ['D2','D1'], ['2024-12-31','2025-12-31']),
                                                (ytd, ['D4','D3'], ['2024-09-30','2025-09-30'])):
                        popup = tree.getroot().get_element_by_id(target[1:])
                        self.assertEqual(popup.xpath('.//tr/@data-observation-id'), refs)
                        self.assertEqual(popup.xpath('.//*[local-name()="math"]//*[local-name()="mtext"]/text()'),
                                         ['本期原值','基期原值'])
                        for role, date in zip(('基期','本期'), dates):
                            self.assertIn(role+'，'+date, popup.text_content())
                        self.assertIn('结果：20.00%', popup.text_content())
                        self.assertIn('iFinD', popup.text_content())
                    logic = tree.getroot().get_element_by_id('logic-1')
                    self.assertEqual(logic.xpath('.//a[@class="quantity-link"]/@href'), [ytd])

                # A different source cell or an edited reporting basis cannot
                # become the basis of the frozen number, even if resealed.
                frozen = session.archive.report(task['id'],saved['version'])['content']
                for changed in ('original_value','period_basis','different_calculation','legacy'):
                    with self.subTest(changed=changed):
                        content = deepcopy(frozen)
                        if changed == 'original_value':
                            source = content['source_records'][0]
                            source['snapshot']['tables'][0]['table']['ths_revenue_stock'][0] = 126
                            rehash(source)
                        elif changed == 'period_basis':
                            content['draft']['facts'][0]['period_kind'] = 'point_in_time'
                        elif changed == 'different_calculation':
                            binding = content['draft']['quantity_links']['conclusion']['links']
                            binding[0]['calculation_refs'] = binding[1]['calculation_refs']
                            binding[0]['data_refs'] = binding[1]['data_refs']
                        else:
                            content['draft'].pop('quantity_links')
                        content['content_hash'] = fingerprint({key: value for key,value in content.items() if key != 'content_hash'})
                        tree = html.document_fromstring(design(content, ROOT)['html'])
                        summary = tree.get_element_by_id('summary')
                        self.assertIn(kept[0], summary.text_content())
                        self.assertEqual(summary.xpath('.//a[@class="quantity-link"]/@href'), [] if changed == 'legacy' else [ytd]*3)
            finally:
                session.agent.close_runtime()

    def test_document_paraphrase_uses_the_disclosed_quarter_duration(self):
        task,_,_=fixture()
        task['draft_client_copy_version']=2
        task['materials']=[dict(id='filing',kind='material',task_id=task['id'],research_version=task['version'],
            parts=[dict(locator='第2页',text='2025年第三季度营业收入同比增长19%。'
                                           '2025年前三季度营业收入同比增长12%。')])]
        proposal=dict(title='营业收入',conclusion='按原件区分报告期间。',logics=[
            dict(title='原件期间',source_refs=['filing'],
                 analysis='2025Q3营业收入同比增长19%。2025年1—9月营业收入同比增长12%。'
                          '2025Q3营业收入同比增长12%。2025年前三季度营业收入同比增长19%。'
                          '2025年全年营业收入同比增长19%。')])
        text=normalize_report(proposal,task,[])['logics'][0]['analysis']
        self.assertIn('2025Q3营业收入同比增长19%',text)
        self.assertIn('2025年1—9月营业收入同比增长12%',text)
        self.assertNotIn('2025Q3营业收入同比增长12%',text)
        self.assertNotIn('2025年前三季度营业收入同比增长19%',text)
        self.assertNotIn('2025年全年营业收入同比增长19%',text)

    def test_document_numbers_open_only_the_bound_original_passage(self):
        from lxml import html
        from researchhelper.report_designer import design

        q3 = '2025年第三季度营业收入同比增长19%'
        ytd = '2025年前三季度实际营业收入同比超预期增长19%'
        forecast = '管理层预计，2026年第三季度营业收入同比增长19%'
        fact_from_forecast = '2026年第三季度营业收入同比增长19%'
        with tempfile.TemporaryDirectory() as folder:
            session = Session(folder)
            try:
                task = session.create('核对600519.SH公告中营业收入的报告期和预测',objects=['600519.SH'])
                task['synthetic'] = True
                session.archive.save(task)
                payload = ('另一段的文字。\n\n'+q3+'。同段其他内容。\n\n'+ytd+'。\n\n'+forecast+'。').encode()
                material = session.material(task['id'],'季度披露.txt',payload)
                citation = material['id']
                proposal = dict(title='收入披露',conclusion=q3+'。'+ytd+'。',
                    logics=[dict(title='期间比较',source_refs=[citation],
                        analysis='2025Q3营业收入较上年同期增长19%。',implication=ytd+'。'),
                        dict(title='已披露的展望',source_refs=[citation],
                             analysis=fact_from_forecast+'。'+forecast+'。')])
                result = session.draft(task['id'],report=proposal,data_requests=[])
                self.assertTrue(result.get('delivery'),result.get('error'))
                self.assertEqual(result['ledger']['used']['model_calls'],0)
                content = session.archive.report(task['id'],result['version'])['content']
                for profile in ('standard','detailed'):
                    tree = html.parse(str(session.export(task['id'],profile=profile))).getroot()
                    summary = tree.get_element_by_id('summary')
                    links = summary.xpath('.//a[@class="quantity-link"]')
                    self.assertEqual([node.text_content() for node in links],['19%','19%'])
                    targets = [node.get('href') for node in links]
                    self.assertEqual(len(set(targets)),2)
                    for index,target,excerpt in zip((2,3),targets,(q3,ytd)):
                        popup = tree.get_element_by_id(target[1:])
                        self.assertEqual(popup.get('data-inspection-title'),'披露原文')
                        self.assertEqual(popup.xpath('./h3/text()'),['季度披露.txt'])
                        self.assertEqual(popup.xpath('./p[@class="figure-source"]/text()'),[f'段落{index}'])
                        self.assertEqual(popup.xpath('./p[not(@class)]/text()'),[excerpt])
                        self.assertFalse(popup.xpath('.//*[local-name()="math"]'))
                    logic = tree.get_element_by_id('logic-1')
                    self.assertEqual(logic.xpath('.//a[@class="quantity-link"]/@href'),targets)
                    projection = tree.get_element_by_id('logic-2')
                    self.assertEqual(projection.xpath('.//a[@class="quantity-link"]'),[])
                    self.assertIn(forecast,projection.text_content())
                    self.assertIn('缺少可核对的读数',projection.text_content())

                for change in ('text','location','owner','ref','duplicate','clipped_clause'):
                    with self.subTest(change=change):
                        altered = deepcopy(content)
                        original = altered['materials'][0]
                        part = original['snapshot']['parts'][1]
                        links = altered['draft']['quantity_links']['conclusion']['links']
                        if change == 'text':
                            part['text'] = part['text'].replace('19%','18%')
                        elif change == 'location':
                            part['locator'] = '段落5'
                        elif change == 'owner':
                            original['task_id'] = 'foreign'
                        elif change == 'ref':
                            links[0]['document']['part_index'] = 2
                        elif change == 'duplicate':
                            altered['materials'].append(deepcopy(original))
                        else:
                            part['text'] = part['text'].replace('19%。','19%后的条件。',1)
                        altered['content_hash'] = fingerprint({key: value for key,value in altered.items() if key!='content_hash'})
                        tree = html.document_fromstring(design(altered,ROOT)['html'])
                        summary = tree.get_element_by_id('summary')
                        self.assertIn(q3,summary.text_content())
                        self.assertEqual(summary.xpath('.//a[@class="quantity-link"]/@href'),
                                         [] if change in ('owner','duplicate') else targets[1:])
            finally:
                session.agent.close_runtime()

    def test_matching_disclosures_without_a_unique_location_keep_the_copy_unlinked(self):
        task,_,_=fixture()
        task['draft_client_copy_version']=2
        claim = '2025年第三季度营业收入同比增长19%。'
        task['materials'] = [dict(id='filing',kind='material',task_id=task['id'],research_version=task['version'],
            parts=[dict(locator='第2页',text=claim),dict(locator='第3页',text=claim)])]
        result = normalize_report(dict(title='财务披露',conclusion=claim,
            logics=[dict(title='已披露读数',analysis=claim,source_refs=['filing'])]),task,[])
        self.assertEqual(result['conclusion'],claim)
        self.assertNotIn('quantity_links',result)
        self.assertNotIn('quantity_links',result['logics'][0])

    def test_respective_amounts_bind_in_the_order_of_the_named_periods(self):
        task,facts,proposal=fixture()
        task['draft_client_copy_version']=2
        for row in facts:
            row['unit']='元'
        task['draft_analysis']=analyze(task,facts)
        for years,amounts in (('2024年和2025年','100元和120元'), ('2024年与2025年','100元、120元'),
                              ('2024年、2025年','100元，120元')):
            with self.subTest(years=years,amounts=amounts):
                sentence=years+'营业收入分别为'+amounts+'。'
                report=normalize_report(dict(proposal,conclusion=sentence),task,facts)
                self.assertEqual(report['conclusion'],sentence)
        swapped='2024年与2025年营业收入分别为120元，100元。'
        report=normalize_report(dict(proposal,conclusion=swapped),task,facts)
        self.assertNotIn('120元',report['conclusion'])
        self.assertNotIn('100元',report['conclusion'])

    def test_latest_price_is_checked_against_all_admitted_dates_and_dated(self):
        from tests.test_client_report_audience import fixture as report_fixture

        task,_=report_fixture()
        task['draft_client_copy_version']=2
        facts=task['draft_report']['facts']
        proposal=dict(title='价格观察',conclusion='最新收盘价为1340元。最近收盘价为1335元。'
            '2026-10-27收盘价为1340元。最新收盘价为1340。'
            '截至最新交易日，收盘价为1340元，PE(TTM)为20.30。',logics=[
                dict(title='行情核对',analysis='最近收盘价为1340元。',data_refs=['D9'])])
        result=normalize_report(proposal,task,facts)
        self.assertNotIn('最新收盘价为1340',result['conclusion'])
        self.assertNotIn('最近收盘价为1340',result['logics'][0]['analysis'])
        self.assertNotIn('截至最新交易日',result['conclusion'])
        self.assertEqual(result['conclusion'].count('1340元'),1)
        self.assertIn('2026-10-27收盘价为1340元',result['conclusion'])
        self.assertIn('2026-10-28收盘价为1335元',result['conclusion'])
        self.assertIn('2026-10-28PE(TTM)为20.30',result['conclusion'])
        dated=[row for row in result['copy_adjustments'] if row['reason']=='market_observation_date_bound']
        self.assertEqual({ref for row in dated for ref in row['data_refs']},{'D11','D12'})

    def test_missing_same_day_and_conflicting_market_reads_keep_precise_boundaries(self):
        from tests.test_client_report_audience import fixture as report_fixture

        task,_=report_fixture()
        task['draft_client_copy_version']=2
        facts=task['draft_report']['facts']
        quote=next(row for row in facts if row['id']=='D11')
        facts.append(dict(quote,id='next',period='2026-10-29',value=None,raw_value=None))
        facts.append(dict(quote,id='future',period='2026-10-30',value=1399,raw_value=1399))
        proposal=dict(title='价格观察',conclusion='截至2026-10-29，最新收盘价为1335元。'
            '今日收盘价为1335元。最新收盘价为1399元。'
            '截至2026-10-27，最新收盘价为1340元。',logics=[dict(title='日期核对',analysis='按实际报价日期。')])
        result=normalize_report(proposal,task,facts)
        self.assertIn('截至2026-10-29，2026-10-28收盘价为1335元',result['conclusion'])
        self.assertNotIn('今日收盘价为1335元',result['conclusion'])
        self.assertNotIn('1399元',result['conclusion'])
        self.assertIn('截至2026-10-27，收盘价为1340元',result['conclusion'])
        facts.append(dict(quote,id='conflict',value=1300,raw_value=1300))
        result=normalize_report(dict(proposal,conclusion='最新收盘价为1335元。'),task,facts)
        self.assertNotIn('1335元',result['conclusion'])

    def test_latest_claim_uses_the_named_security_and_explicit_valuation_method(self):
        from tests.test_client_report_audience import fixture as report_fixture

        task,_=report_fixture()
        task['draft_client_copy_version']=2
        facts=task['draft_report']['facts']
        quote=next(row for row in facts if row['id']=='D11')
        for row in facts:
            row['object_label']='贵州茅台'
        facts.append(dict(quote,id='second-stock',code='000001.SZ',object_label='平安银行',
                          value=10,raw_value=10))
        for ident, field, value in (('latest','ths_pb_latest_stock',8),('mrq','ths_pb_mrq_stock',5)):
            facts.append(dict(quote,id=ident,field=field,label='市净率',unit='倍',value=value,raw_value=value))
        proposal=dict(title='行情与估值',conclusion='贵州茅台最新收盘价为1335元。平安银行最近收盘价为10元。'
            '最新收盘价为10元。最新PB(MRQ)读数为5.00倍。最新PB(MRQ)读数为8.00倍。',
            logics=[dict(title='行情与估值',analysis='保留报价日期与实际估值口径。')])
        result=normalize_report(proposal,task,facts)
        self.assertIn('贵州茅台2026-10-28收盘价为1335元',result['conclusion'])
        self.assertIn('平安银行2026-10-28收盘价为10元',result['conclusion'])
        self.assertEqual(result['conclusion'].count('10元'),1)
        self.assertIn('2026-10-28PB(MRQ)读数为5.00倍',result['conclusion'])
        self.assertNotIn('8.00倍',result['conclusion'])

    def test_interval_return_and_previous_session_return_remain_distinct_in_saved_reports(self):
        from lxml import html
        from researchhelper.evidence import snapshot_record

        days=['2026-10-26','2026-10-27','2026-10-28']
        window='2026-10-26至2026-10-28收盘价区间上涨0.38%'
        daily='收盘价较上一交易日下跌0.37%'
        wrong_day='收盘价较上一交易日上涨0.38%'
        wrong_base='2026-10-27至2026-10-28收盘价区间上涨0.38%'
        proposal=dict(title='收盘变化',conclusion='。'.join((window,daily,wrong_day,wrong_base))+'。',
            logics=[dict(title='日与区间',analysis=daily+'。')])
        with tempfile.TemporaryDirectory() as folder:
            session=Session(folder)
            try:
                for missing in (False,True):
                    task=session.create('比较600519.SH的区间涨跌幅与上一交易日涨跌幅',
                        objects=['600519.SH'], cutoff=days[-1], research_mode='historical')
                    params=dict(codes='600519.SH',indicators='close',startdate=days[0],enddate=days[-1],
                                functionpara=dict(Currency='RMB',CPS='1',Fill='Omit'))
                    snapshot=dict(errorcode=0,tables=[dict(thscode='600519.SH',time=days,
                        table=dict(close=[1330,None if missing else 1340,1335]))])
                    task.update(synthetic=True,source_records=[snapshot_record(task,'ifind','cmd_history_quotation',params,snapshot)])
                    session.archive.save(task)
                    saved=session.draft(task['id'],report=proposal,data_requests=[])
                    self.assertTrue(saved.get('delivery'),saved.get('error'))
                    daily_rows=[row for row in saved['draft_report']['calculations']
                                if row.get('comparison_kind')=='previous_session']
                    self.assertEqual(len(daily_rows),0 if missing else 1)
                    if daily_rows:
                        self.assertEqual(daily_rows[0]['data_refs'],['D2','D3'])
                        self.assertIn(daily_rows[0]['id'],saved['draft_report']['logics'][0]['calculation_refs'])
                    for profile in ('standard','detailed'):
                        path=session.export(saved['id'],profile=profile)
                        body=html.parse(str(path)).find('.//main').text_content()
                        self.assertIn(window,body)
                        self.assertEqual('2026-10-28'+daily in body,not missing)
                        self.assertNotIn(wrong_day,body)
                        self.assertNotIn(wrong_base,body)
            finally:
                session.agent.close_runtime()

    def test_dated_market_levels_open_exact_cells_in_both_saved_profiles(self):
        from lxml import html
        from researchhelper.evidence import snapshot_record
        from researchhelper.market_series import NAV_INDICATOR, PE_TTM_INDICATOR, SERIES_FAMILIES
        from researchhelper.report_designer import render_html

        stock, peer, etf = '600519.SH', '000001.SZ', '510300.SH'
        earlier, later = '2026-10-29', '2026-10-30'
        reading = '2026年10月30日600519.SH收盘价为1392.05元。'
        prose = (reading + '2026-10-29，600519.SH收盘价为1392.05元。'
                 '000001.SZ最新收盘价为1392.05元。2026-10-29，510300.SH单位净值为4.1176。'
                 '截至2026-10-30，600519.SH PE(TTM)为20.351234。预计2026-10-30 600519.SH收盘价为1392.05元。'
                 '预计2026-10-30 600519.SH PE(TTM)为20.351234。')
        with tempfile.TemporaryDirectory() as folder:
            session = Session(folder)
            try:
                task = session.create('梳理样本的行情、估值与净值。', objects=[stock, peer, etf], research_mode='current')
                quote = snapshot_record(task, 'ifind', 'cmd_history_quotation',
                    dict(codes=stock+','+peer, indicators='close', startdate=earlier, enddate=later,
                         functionpara=dict(Currency='RMB', CPS='1', Fill='Omit')),
                    dict(errorcode=0, tables=[dict(thscode=code, time=[earlier, later], table=dict(close=values))
                        for code, values in ((stock, [1392.05, 1392.05]), (peer, [1400, 1392.05]))]))
                series = []
                for code, entry, field, values in ((stock, '个股', PE_TTM_INDICATOR, [19.7, 20.351234]),
                                                   (etf, 'ETF', NAV_INDICATOR, [4.1176, None])):
                    series.append(snapshot_record(task, 'ifind', 'date_sequence',
                        dict(codes=code, startdate=earlier, enddate=later, functionpara=dict(Fill='Blank'),
                             indipara=[dict(indicator=field, indiparams=SERIES_FAMILIES[entry][field])]),
                        dict(errorcode=0, tables=[dict(thscode=code, time=[earlier, later], table={field: values})])))
                task.update(synthetic=True, source_records=[quote, *series])
                session.archive.save(task)
                saved = session.draft(task['id'], report=dict(title='行情与净值', conclusion=prose,
                    logics=[dict(title='时点行情', analysis=reading)]), data_requests=[])
                self.assertTrue(saved.get('delivery'), saved.get('error'))
                self.assertEqual(saved['ledger']['used']['model_calls'], 0)
                paper = session.archive.report(task['id'], 1)['content']
                links = saved['draft_report']['quantity_links']['conclusion']['links']
                expected = [(stock, later, '1392.05元'), (stock, earlier, '1392.05元'),
                            (peer, later, '1392.05元'), (etf, earlier, '4.1176'), (stock, later, '20.351234')]
                self.assertEqual([(link['code'], link['date'], link['original']) for link in links], expected)
                for profile in ('standard', 'detailed'):
                    tree = html.parse(str(session.export(saved['id'], profile=profile))).getroot()
                    anchors = tree.get_element_by_id('summary').xpath('.//a[@class="quantity-link"]')
                    self.assertEqual([a.text_content() for a in anchors], [item[2] for item in expected[:-1]] + ['20.35'])
                    for anchor, (code, day, _) in zip(anchors, expected):
                        details = tree.get_element_by_id(anchor.get('href')[1:])
                        cell_row, = details.xpath('.//tr[@data-observation-id]')
                        cells = cell_row.xpath('./td/text()')
                        self.assertEqual((cells[0], cells[2]), (code, day))
                        self.assertIn('iFinD', details.text_content())
                    summary = tree.get_element_by_id('summary').text_content()
                    self.assertIn('预计2026-10-30 600519.SH收盘价为1392.05元。', summary)
                    self.assertIn('预计2026-10-30 600519.SH PE(TTM)为20.351234。', summary)
                    self.assertEqual(anchors[-1].get('data-original-display'), '20.351234')
                    logic_link, = tree.get_element_by_id('logic-1').xpath('.//a[@class="quantity-link"]')
                    self.assertEqual(logic_link.get('href'), anchors[0].get('href'))

                altered = deepcopy(paper)
                link = altered['draft']['quantity_links']['conclusion']['links'][0]
                row = next(row for row in altered['draft']['facts'] if row['id'] == link['data_refs'][0])
                row['locator']['row'] = 0
                link['row_hash'] = fingerprint(row)
                altered['content_hash'] = fingerprint({key: value for key, value in altered.items() if key != 'content_hash'})
                changed = html.document_fromstring(render_html(altered, ROOT)).get_element_by_id('summary')
                self.assertIn(reading, changed.text_content())
                self.assertEqual(len(changed.xpath('.//a[@class="quantity-link"]')), len(expected) - 1)
            finally:
                session.agent.close_runtime()

    def test_market_links_exclude_ambiguous_or_unobserved_levels_and_preserve_previous_versions(self):
        from researchhelper.draft_sources import CLIENT_COPY_ATTRIBUTION_VERSION, CLIENT_COPY_VERSION
        from tests.test_client_report_audience import fixture as report_fixture

        task, _ = report_fixture()
        task.update(objects=['600519.SH', '000001.SZ'], draft_client_copy_version=CLIENT_COPY_VERSION)
        facts = task['draft_report']['facts']
        price = next(row for row in facts if row['id'] == 'D11')
        facts.append(dict(price, id='peer', code='000001.SZ', object_label='平安银行'))
        task['draft_analysis'] = analyze(task, facts)
        sentences = (
            '2026-10-28 600519.SH收盘价为1335元。', '2026-10-28收盘价为1335元。',
            '2026-10-27至2026-10-28 600519.SH收盘价为1335元。',
            '2026-10-28 600519.SH目标收盘价为1335元。', '2026-10-28 600519.SH收盘价约为1335元。',
            '2026-10-28 600519.SH前复权收盘价为1335元。',
            '2026-10-28 600519.SH市盈率LYR为20.30。',
        )
        task['original_quantity_qualifier_version'] = 2
        proposal = dict(title='行情核对', conclusion=''.join(sentences),
                        logics=[dict(title='收盘读数', analysis=sentences[0], data_refs=['D11'])])
        current = normalize_report(proposal, task, facts)
        link, = current['quantity_links']['conclusion']['links']
        self.assertEqual(link['start'], sentences[0].index('1335'))
        self.assertEqual(link['data_refs'], ['D11'])
        conflicted = facts + [dict(price, id='different-series', raw_value=1399, value=1399)]
        self.assertNotIn('quantity_links', normalize_report(proposal, task, conflicted))
        legacy = normalize_report(proposal, dict(task, draft_client_copy_version=CLIENT_COPY_ATTRIBUTION_VERSION), facts)
        self.assertNotIn('quantity_links', legacy)

    def test_market_date_header_limits_values_without_a_global_report_gate(self):
        from researchhelper.draft_sources import CLIENT_COPY_MARKET_LEVEL_VERSION, CLIENT_COPY_VERSION
        from tests.test_client_report_audience import fixture as report_fixture

        task, _ = report_fixture()
        task['draft_client_copy_version'] = CLIENT_COPY_VERSION
        facts = task['draft_report']['facts']
        good = '2026年10月26日，600519.SH收盘价为1330元。'
        wrong = '2026年10月26日，600519.SH收盘价为1335元。'
        plain = '2026年10月28日。600519.SH收盘价为1340元。'
        as_of = '截至2026年10月29日，最新收盘价为1335元。'
        invalid = '截至2026年2月31日，最新收盘价为1340元。'
        proposal = dict(title='日终收盘核对', conclusion=good+wrong+plain+as_of+invalid,
                        logics=[dict(title='同日读数', analysis=good+wrong)])
        report = normalize_report(proposal, task, facts)
        self.assertIn(good, report['conclusion'])
        self.assertIn(plain, report['conclusion'])
        self.assertIn('截至2026年10月29日，2026-10-28收盘价为1335元。', report['conclusion'])
        self.assertNotIn(wrong, report['conclusion'])
        self.assertNotIn(invalid, report['conclusion'])
        self.assertIn('所述时点的收盘价缺少可核对的读数', report['logics'][0]['analysis'])
        links = report['quantity_links']['conclusion']['links']
        self.assertEqual([(link['date'], link['original'], link['data_refs']) for link in links],
                         [('2026-10-26', '1330元', ['D7']), ('2026-10-28', '1335元', ['D11'])])
        legacy = normalize_report(proposal, dict(task, draft_client_copy_version=CLIENT_COPY_MARKET_LEVEL_VERSION), facts)
        self.assertIn(wrong, legacy['conclusion'])

    def test_disclosed_daily_return_is_not_substituted_for_a_close_comparison(self):
        from lxml import html
        from researchhelper.evidence import snapshot_record

        with tempfile.TemporaryDirectory() as folder:
            session=Session(folder)
            try:
                task=session.create('核对600519.SH披露涨跌幅',objects=['600519.SH'],
                    cutoff='2026-10-28',research_mode='historical')
                params=dict(ts_code='600519.SH',start_date='20261028',end_date='20261028',
                            fields='ts_code,trade_date,pct_chg')
                payload=dict(code=0,data=[dict(ts_code='600519.SH',trade_date='20261028',pct_chg=2.0)])
                task.update(synthetic=True,source_records=[snapshot_record(task,'tinyshare','daily',params,payload)])
                session.archive.save(task)
                proposal=dict(title='披露读数',conclusion='披露涨跌幅为2.00%。收盘价较上一交易日上涨2.00%。',
                    logics=[dict(title='来源口径',analysis='以原始披露为依据。')])
                saved=session.draft(task['id'],report=proposal,data_requests=[])
                self.assertTrue(saved.get('delivery'),saved.get('error'))
                self.assertIn('2026-10-28披露涨跌幅为2.00%',saved['draft_report']['conclusion'])
                self.assertNotIn('收盘价较上一交易日上涨2.00%',saved['draft_report']['conclusion'])
                page=html.parse(str(session.export(task['id'],profile='standard')))
                link,=page.xpath('//*[@id="summary"]//a[@class="quantity-link"]')
                popup,=page.xpath('//*[@id=$target]',target=link.get('href')[1:])
                self.assertEqual(link.text_content(),'2.00%')
                self.assertIn('披露涨跌幅',popup.text_content())
                self.assertIn('Tinyshare',popup.text_content())
                self.assertFalse(popup.xpath('.//*[local-name()="math"]'))
            finally:
                session.agent.close_runtime()

    def test_latest_daily_change_uses_latest_close_across_sources_even_with_older_citations(self):
        from lxml import html
        from researchhelper.evidence import snapshot_record

        code = '600519.SH'
        latest = '最新收盘价较上一交易日上涨10.00%'
        historical = '2026-10-28收盘价较上一交易日上涨10.00%'
        with tempfile.TemporaryDirectory() as folder:
            session = Session(folder)
            try:
                for have_previous in (False, True):
                    with self.subTest(have_previous=have_previous):
                        task = session.create('核对600519.SH最近收盘价和日涨跌幅', objects=[code],
                            cutoff='2026-10-29', research_mode='historical')
                        quote_days = ['2026-10-28', '2026-10-29']
                        current = snapshot_record(task, 'ifind', 'cmd_history_quotation',
                            dict(codes=code, indicators='close', startdate=quote_days[0], enddate=quote_days[1],
                                 functionpara=dict(Currency='RMB', CPS='1', Fill='Omit')),
                            dict(errorcode=0, tables=[dict(thscode=code,time=quote_days,
                                table=dict(close=[1100 if have_previous else None, 1144]))]))
                        older = snapshot_record(task, 'tinyshare', 'daily', dict(ts_code=code,
                            start_date='20261027', end_date='20261028', fields='ts_code,trade_date,close,pct_chg'),
                            dict(code=0,data=[dict(ts_code=code,trade_date='20261027',close=1000,pct_chg=None),
                                dict(ts_code=code,trade_date='20261028',close=1100,pct_chg=2)]))
                        task.update(synthetic=True, source_records=[current, older])
                        session.archive.save(task)
                        proposal = dict(title='近期收盘与日涨跌幅', conclusion='。'.join((latest,
                            '最近一个交易日收涨10.00%', historical,
                            '截至2026-10-28，最新收盘价较上一交易日上涨10.00%',
                            '截至2026-10-29，最新收盘价较上一交易日上涨10.00%',
                            '最新收盘价较上一交易日上涨4.00%', '最近一个交易日收涨4.00%', '最新收盘价为1144元',
                            '最新披露涨跌幅为2.00%'))+'。', logics=[
                            dict(title='引用较早的收盘', analysis=latest+'。', data_refs=['D3','D4']),
                            dict(title='最近的收盘', analysis='截至最近交易日，收盘价较上一交易日上涨4.00%。'),
                            dict(title='历史比较', analysis=historical+'。', data_refs=['D3','D4'])])
                        saved = session.draft(task['id'], report=proposal, data_requests=[])
                        self.assertTrue(saved.get('delivery'), saved.get('error'))
                        self.assertEqual(saved['ledger']['used']['model_calls'], 0)
                        self.assertEqual(saved['ledger']['used']['data_requests'], 0)
                        report = saved['draft_report']
                        self.assertIn('2026-10-29收盘价为1144元', report['conclusion'])
                        self.assertIn('2026-10-28披露涨跌幅为2.00%', report['conclusion'])
                        self.assertIn('截至2026-10-28，收盘价较上一交易日上涨10.00%', report['conclusion'])
                        self.assertNotIn('截至2026-10-29，2026-10-28收盘价较上一交易日上涨10.00%', report['conclusion'])
                        self.assertEqual(report['conclusion'].count('上涨10.00%'), 2)
                        self.assertNotIn('收涨10.00%', report['conclusion'])
                        self.assertNotIn('10.00%', report['logics'][0]['analysis'])
                        self.assertIn(historical, report['logics'][2]['analysis'])
                        same_day = '2026-10-29收盘价较上一交易日上涨4.00%'
                        self.assertEqual(same_day in report['conclusion'], have_previous)
                        self.assertEqual('2026-10-29收涨4.00%' in report['conclusion'], have_previous)
                        self.assertEqual(same_day in report['logics'][1]['analysis'], have_previous)
                        self.assertEqual(report['logics'][1]['analysis'].count('截至最近交易日'), 0 if have_previous else 1)
                        for profile in ('standard', 'detailed'):
                            body = html.parse(str(session.export(task['id'],profile=profile))).find('.//main').text_content()
                            self.assertIn(historical, body)
                            self.assertNotIn('2026-10-28'+latest, body)
                            self.assertEqual(same_day in body, have_previous)
            finally:
                session.agent.close_runtime()

    def test_latest_daily_change_stays_unverified_when_sources_disagree_on_that_close(self):
        from tests.test_client_report_audience import fixture as report_fixture

        task, _ = report_fixture()
        task['draft_client_copy_version'] = 2
        task['source_records'][1].update(operation='cmd_history_quotation', parameters=dict(codes='600519.SH',
            indicators='close', startdate='2026-10-26', enddate='2026-10-28',
            functionpara=dict(Currency='RMB', CPS='1', Fill='Omit')))
        facts = task['draft_report']['facts']
        latest = next(row for row in facts if row['id'] == 'D11')
        other = dict(id='other-source', task_id=task['id'], research_version=task['version'], source='tinyshare')
        task['source_records'].append(other)
        facts.append(dict(latest,id='conflicting-close', artifact_id=other['id'], source='tinyshare',
            raw_value=1345, value=1345, series_basis='other-source'))
        task['draft_analysis'] = analyze(task, facts)
        self.assertTrue(any(item.get('comparison_kind') == 'previous_session'
                            for item in task['draft_analysis']['results']))
        result = normalize_report(dict(title='行情核对', conclusion='最新收盘价较上一交易日下跌0.37%。'), task, facts)
        self.assertNotIn('下跌0.37%', result['conclusion'])

    def test_reusing_a_report_does_not_repeat_structured_argument_paragraphs(self):
        task,facts,value=fixture()
        value['conclusion']='比较已取得数据，不推定因果。'
        value['logics'][0]['argument']=dict(claim='收入变化需要结合成本。',evidence='只使用已取得的数据。',
            reasoning='收入和现金流有不同含义。',condition='以同口径为前提。')
        value['logics'][0]['analysis']='保留补充分析。'
        first=normalize_report(value,task,facts);reused=first
        for _ in range(3):reused=normalize_report(reused,task,facts)
        self.assertEqual(reused['logics'][0]['analysis'],first['logics'][0]['analysis'])
        self.assertEqual(reused['logics'][0]['argument'],first['logics'][0]['argument'])
        for paragraph in first['logics'][0]['argument'].values():
            self.assertEqual(reused['logics'][0]['analysis'].count(paragraph),1)
        self.assertTrue(reused['logics'][0]['analysis'].startswith('保留补充分析。'))

    def test_argument_merging_preserves_distinct_prose_and_multiline_blocks(self):
        task,facts,value=fixture();value['conclusion']='仅比较已取得资料。'
        value['logics'][0]['analysis']='收入变化需要结合成本，但还需要检查其他因素。'
        value['logics'][0]['argument']=dict(claim='收入变化需要结合成本。',
            evidence='第一项实际依据。\n第二项实际依据。')
        first=normalize_report(value,task,facts);again=normalize_report(first,task,facts)
        expected='收入变化需要结合成本，但还需要检查其他因素。\n收入变化需要结合成本。\n第一项实际依据。\n第二项实际依据。'
        self.assertEqual(first['logics'][0]['analysis'],expected)
        self.assertEqual(again['logics'][0]['analysis'],expected)


if __name__=='__main__':unittest.main()
