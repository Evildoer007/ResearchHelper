"""Client figures follow their arguments; archival context stays in detailed output."""
from copy import deepcopy
import socket,tempfile,unittest
from unittest.mock import patch
from lxml import html
from tests import ROOT
from tests.test_client_report_audience import fixture
from tests.test_market_calendar_coverage import append_calendar_fixture_record
from tests.test_draft_financial_period import add_record,sample,rehash
from researchhelper.contracts import fingerprint,canonical
from researchhelper.draft_sources import observations
from researchhelper.draft_analysis import analyze,execute
from researchhelper.drafts import normalize_report,compose,source_binding
from researchhelper.draft_visuals import logic_figure_plan
from researchhelper.report_designer import design


def bound(content):
    content['content_hash']=fingerprint({key:value for key,value in content.items() if key!='content_hash'})
    return content


def market_fixture():
    _,content=fixture()
    content['draft']['logics'][2].update(title='观察成立条件',analysis='区分实际观察与成立条件。',
        data_refs=['D12'],calculation_refs=[],mechanism=dict(steps=[
            dict(label='已有观测',kind='observation',data_refs=['D12']),
            dict(label='需要验证的条件',kind='condition',data_refs=[])]))
    return bound(content)


def closing_path_content(*, with_calendar, proposal=None):
    from core.session import Session
    from researchhelper.evidence import record_request_purpose, snapshot_record
    from researchhelper.market_calendar import FUNCTION
    from researchhelper.market_series import compile_observation_window, observation_selection

    code = '600519.SH'
    start, end = ('2026-10-23','2026-10-26') if with_calendar else ('2026-10-26','2026-10-27')
    with tempfile.TemporaryDirectory() as folder:
        session = Session(folder)
        try:
            task = session.create('核对600519.SH收盘价变化', objects=[code], cutoff=end,
                                  research_mode='historical')
            need = dict(id='recent_close',source='ifind',operation='cmd_history_quotation',question_ids=['q1'],
                parameters=dict(codes=code,indicators='close',functionpara=dict(Currency='RMB',CPS='1',Fill='Omit'),
                    observation_window=dict(mode='latest_available',as_of=end,lookback_calendar_days=7)))
            need = compile_observation_window(need, observation_selection(task))
            price = snapshot_record(task,'ifind',need['operation'],need['parameters'],dict(errorcode=0,
                tables=[dict(thscode=code,time=[start,end],table=dict(close=[100,102]))]))
            price.update(data_need_ids=[need['id']],question_ids=['q1'])
            record_request_purpose(price,need,['q1'])
            task.update(synthetic=True,source_records=[price])
            if with_calendar:
                append_calendar_fixture_record(task,'get_trade_dates',dict(marketcode='212001',
                    functionpara=FUNCTION,startdate=start,enddate=end),dict(errorcode=0,tables=dict(time=[start,end])))
                task['ledger']['dispatches'][-1]['private_fixture_field'] = 'not-in-frozen-report'
            session.archive.save(task)
            sentence=end+'收盘价较上一交易日上涨2.00%。'
            if proposal is None:
                proposal=dict(title='收盘比较',conclusion=sentence,logics=[dict(title='比较依据',analysis=sentence,
                    mechanism=dict(steps=[dict(label='最近收盘观测',kind='observation',data_refs=['D1']),
                                          dict(label='以前收作为基数',kind='condition')]))])
            saved=session.draft(task['id'],report=proposal,data_requests=[])
            assert saved.get('delivery'), saved.get('error')
            return session.archive.report(task['id'],saved['version'])['content']
        finally:
            session.agent.close_runtime()


def refreshed_closing_path(agent, submit, *, previous_visible, with_calendar, owner_account=None):
    from researchhelper.market_calendar import FUNCTION
    from researchhelper.market_series import compile_observation_window, observation_selection, record_observation_scope

    code = '600519.SH'
    days = ['2026-10-23', '2026-10-26', '2026-10-27'] if with_calendar else [
        '2026-10-26', '2026-10-27', '2026-10-28']
    cutoff = '2026-10-29'

    def retain_quote(task, readings, *, latest):
        params = dict(codes=code, indicators='close', functionpara=dict(Currency='RMB', CPS='1', Fill='Omit'))
        selection = observation_selection(task)
        if latest:
            params['observation_window'] = dict(mode='latest_available', as_of=selection['as_of'],
                                                lookback_calendar_days=14)
        else:
            params.update(startdate=days[0], enddate=days[-1])
        request = compile_observation_window(dict(id='recent_close', source='ifind',
            operation='cmd_history_quotation', parameters=params), selection)
        quote = append_calendar_fixture_record(task, request['operation'], request['parameters'],
            dict(errorcode=0, tables=[dict(thscode=code, time=days, table=dict(close=readings))]))
        quote['data_need_ids'] = [request['id']]
        record_observation_scope(quote, request, ['q1'])
        return quote

    with patch('researchhelper.engine.now', return_value=days[-1]+'T22:00:00+08:00'):
        task = agent.create('核对600519.SH最近实际收盘较上一交易日的涨跌幅。', entry='个股',
                            objects=[code], research_mode='current', owner_account=owner_account)
    ident = task['id']
    task['synthetic'] = True
    old_quote = retain_quote(task, [98, 102, 110], latest=not previous_visible)
    agent.archive.save(task)
    first = submit(ident, dict(title='收盘核对', conclusion='保存初次取得的收盘原值。'))
    assert first['delivery']
    saved_first = agent.archive.report(ident, 1)

    with patch('researchhelper.engine.now', return_value=cutoff+'T22:00:00+08:00'):
        task = agent.prepare_update(ident)
    quote = retain_quote(task, [100, 105, None], latest=True)
    if with_calendar:
        calendar = append_calendar_fixture_record(task, 'get_trade_dates', dict(marketcode='212001',
            functionpara=FUNCTION, startdate=days[0], enddate=cutoff),
            dict(errorcode=0, tables=dict(time=[*days, cutoff])))
    agent.archive.save(task)
    facts = observations(task)
    rows = {row['period']: row for row in facts}
    assert set(rows) == set(days) and all(row['artifact_id'] == quote['id'] for row in facts)
    assert [rows[day]['raw_value'] for day in days] == [100, 105, None]
    assert bool(rows[days[0]].get('previous_close_for')) is not previous_visible

    sentence = days[1]+'收盘价较上一交易日上涨5.00%。'
    proposal = dict(title='收盘核对', conclusion=sentence, logics=[dict(title='日涨跌幅核对',
        analysis=sentence, mechanism=dict(steps=[
            dict(label='最近收盘', kind='observation', data_refs=[rows[days[1]]['id']]),
            dict(label='以前收作基数', kind='condition')]))])
    done = submit(ident, proposal)
    assert done['delivery'] and done['ledger']['used']['model_calls'] == 0
    content = agent.archive.report(ident, 2)['content']
    calc, = [item for item in content['draft']['calculations'] if item.get('comparison_kind')=='previous_session']
    assert calc['inputs'] == dict(a=100,b=105) and calc['display']=='5.00'
    assert [calc['window_start'], calc['window_end']] == days[:2]
    assert calc['data_refs'] == [rows[day]['id'] for day in days[:2]]
    assert calc['session_basis']['source_refs'] == ([calendar['id']] if with_calendar else [])
    assert done['ledger']['used']['data_requests'] == 1 + with_calendar
    assert agent.archive.report(ident, 1) == saved_first
    originals = {record['id']: record for record in content['source_records']}
    for record in (old_quote, quote):
        assert fingerprint(originals[record['id']]['snapshot']) == record['hash']
    return dict(task_id=ident, content=content, days=days, data_refs=calc['data_refs'], calculation=calc['id'],
                previous_visible=previous_visible, with_calendar=with_calendar)


def verify_refreshed_close_page(page, case, *, baseline=False):
    tree = html.document_fromstring(page)
    links = tree.xpath('//section[@id="summary"]//a[@class="quantity-link"]')
    if baseline:
        assert not links and '5.00%' in tree.get_element_by_id('summary').text_content()
        return dict(linked=False, prices=[100,105])
    link, = links
    assert link.text_content() == '5.00%'
    popup = tree.get_element_by_id(link.get('href')[1:])
    assert set(popup.xpath('.//tr/@data-observation-id')) == set(case['data_refs'])
    assert popup.xpath('.//*[local-name()="math"]')
    text = popup.text_content()
    assert '结果：5.00%' in text
    assert ('iFinD交易日历' in text) is case['with_calendar']
    assert all(day in text for day in case['days'][:2])
    assert case['days'][-1] not in text
    return dict(linked=True, prices=[100,105], exchange_calendar=case['with_calendar'])


class ReportVisualScopeTests(unittest.TestCase):
    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No external calls'))
        guard.start();self.addCleanup(guard.stop)

    def test_close_path_popup_replays_inputs_and_sources_from_frozen_report(self):
        for with_calendar in (False, True):
            with self.subTest(with_calendar=with_calendar):
                content = closing_path_content(with_calendar=with_calendar)
                original = canonical(content)
                spec, = [item for item in logic_figure_plan(content) if item['coverage_kind']=='mechanism']
                self.assertEqual(spec['data_refs'], ['D1','D2'])
                self.assertEqual(spec['calculation_refs'], ['A1'])
                self.assertEqual(spec['source_refs'], [content['source_records'][-1]['id']] if with_calendar else [])
                self.assertEqual('calendar_replay' in content, with_calendar)
                self.assertNotIn('not-in-frozen-report', original)
                for profile in ('standard','detailed'):
                    report=design(content, ROOT, profile=profile)
                    tree=html.document_fromstring(report['html'])
                    popup=tree.get_element_by_id(spec['inspection_target'])
                    self.assertEqual(set(popup.xpath('.//tr/@data-observation-id')), {'D1','D2'})
                    self.assertTrue(popup.xpath('.//*[local-name()="math"]'))
                    self.assertIn('结果：2.00%', popup.text_content())
                    self.assertEqual('iFinD交易日历' in popup.text_content(), with_calendar)
                    self.assertNotIn('response_snapshot_hash',report['html'])
                self.assertEqual(canonical(content), original)

    def test_current_close_refresh_replays_daily_arithmetic_and_source_link(self):
        from core.session import Session

        for with_calendar in (False, True):
            for previous_visible in (False, True):
                with self.subTest(with_calendar=with_calendar, previous_visible=previous_visible):
                    with tempfile.TemporaryDirectory() as folder:
                        session = Session(folder)
                        try:
                            case = refreshed_closing_path(session.agent,
                                lambda ident, proposal: session.draft(ident, report=proposal, data_requests=[]),
                                previous_visible=previous_visible, with_calendar=with_calendar)
                            content = case['content']
                            frozen = canonical(content)
                            for profile in ('standard', 'detailed'):
                                verify_refreshed_close_page(design(content, ROOT, profile=profile)['html'], case)
                                figure, = [item for item in logic_figure_plan(content, report_profile=profile)
                                           if item['coverage_kind']=='mechanism']
                                self.assertEqual(figure['calculation_refs'], [case['calculation']])
                                self.assertEqual(set(figure['data_refs']), set(case['data_refs']))
                            if not previous_visible and not with_calendar:
                                legacy = deepcopy(content)
                                legacy.pop('research_mode')
                                verify_refreshed_close_page(design(bound(legacy), ROOT)['html'], case)
                                receipts = legacy['update_from']['market_refreshes']
                                next(iter(receipts.values()))['snapshot_hash'] = 'unbound'
                                verify_refreshed_close_page(design(bound(legacy), ROOT)['html'], case, baseline=True)
                            if not previous_visible and with_calendar:
                                unproved = deepcopy(content)
                                unproved.pop('calendar_replay')
                                verify_refreshed_close_page(design(bound(unproved), ROOT)['html'], case, baseline=True)
                            self.assertEqual(canonical(content), frozen)
                        finally:
                            session.agent.close_runtime()

    def test_close_path_rejects_wrong_calendar_or_changed_price_with_consistent_math(self):
        original = closing_path_content(with_calendar=True)
        for issue in ('legacy_without_receipt','wrong_scope','wrong_event','changed_original','changed_price'):
            with self.subTest(issue=issue):
                content=deepcopy(original)
                replay=content['calendar_replay']
                calendar=content['source_records'][-1]
                if issue=='legacy_without_receipt':
                    content.pop('calendar_replay')
                elif issue=='wrong_scope':
                    replay['research_version'] += 1
                elif issue=='wrong_event':
                    replay['events'][calendar['id']]['event']['network_worker_reaped'] = False
                elif issue=='changed_original':
                    calendar['snapshot']['tables']['time'] = ['2026-10-23','2026-10-24','2026-10-26']
                    calendar['hash'] = fingerprint(calendar['snapshot'])
                else:
                    content['draft']['facts'][1].update(value=101,raw_value=101)
                    inputs = dict(a=101,b=102)
                    content['draft']['calculations'][0].update(inputs=inputs,**execute('raw_change_v1',inputs))
                figure,=[item for item in logic_figure_plan(bound(content)) if item['coverage_kind']=='mechanism']
                self.assertEqual(figure['data_refs'], ['D1'])
                self.assertEqual(figure['calculation_refs'], [])
                self.assertEqual(figure['source_refs'], [])

    def test_report_rate_links_follow_the_checked_text_after_date_edits_and_paragraph_merging(self):
        daily = '2026-10-26收盘价较上一交易日上涨2.00%。'
        hypothesis = '若上涨2.00%，则观察后续成交。'
        proposal = dict(title='收盘比较', conclusion='  最近收盘价较上一交易日上涨2.00%。'
            '目标上涨概率2.00%。'+hypothesis+'\n'+daily+'  ', logics=[None,
            dict(title='比较依据', analysis='说明留存数据的用途。', argument=dict(evidence=daily),
                 implication='最近收盘价较上一交易日上涨2.00%。')])
        content = closing_path_content(with_calendar=True, proposal=proposal)
        for profile in ('standard','detailed'):
            tree=html.document_fromstring(design(content,ROOT,profile=profile)['html'])
            summary=tree.get_element_by_id('summary')
            links=summary.xpath('.//a[@class="quantity-link"]')
            self.assertEqual([link.text_content() for link in links], ['2.00%','2.00%'])
            self.assertIn(hypothesis, summary.text_content())
            self.assertEqual(len(set(link.get('href') for link in links)), 1)
            popup=tree.get_element_by_id(links[0].get('href')[1:])
            self.assertEqual(set(popup.xpath('.//tr/@data-observation-id')), {'D1','D2'})
            self.assertTrue(popup.xpath('.//*[local-name()="math"]'))
            self.assertIn('iFinD交易日历', popup.text_content())
            logic=tree.get_element_by_id('logic-1')
            self.assertEqual(len(logic.xpath('.//a[@class="quantity-link"]')), 2)

    def test_report_rate_links_skip_edited_or_unbound_prose(self):
        original = closing_path_content(with_calendar=True)
        for issue in ('legacy','edited_text','edited_date','unverified_calendar'):
            with self.subTest(issue=issue):
                content=deepcopy(original)
                draft=content['draft']
                if issue=='legacy':
                    draft.pop('quantity_links')
                elif issue=='edited_text':
                    draft['conclusion']=draft['conclusion'].replace('10-26','10-27')
                elif issue=='edited_date':
                    draft['quantity_links']['conclusion']['links'][0]['date']='2026-10-27'
                else:
                    content.pop('calendar_replay')
                tree=html.document_fromstring(design(bound(content),ROOT)['html'])
                summary=tree.get_element_by_id('summary')
                self.assertIn('2.00%',summary.text_content())
                self.assertFalse(summary.xpath('.//a[@class="quantity-link"]'))

    def test_client_has_no_unrelated_supplemental_price_curve(self):
        content=market_fixture();before=canonical(content)
        specs=logic_figure_plan(content)
        self.assertTrue(specs)
        self.assertTrue(all(spec.get('paper_logic') for spec in specs))
        self.assertFalse(any(spec.get('field')=='close' for spec in specs))
        self.assertEqual(canonical(content),before)

    def test_detailed_keeps_all_original_values_without_unrelated_figures(self):
        content=market_fixture();standard=design(content,ROOT);detail=design(content,ROOT,profile='detailed')
        self.assertEqual(standard['document']['dataset'],detail['document']['dataset'])
        self.assertEqual(len(detail['document']['dataset']['rows']),12)
        self.assertFalse(any(spec.get('field')=='close' for spec in logic_figure_plan(content,report_profile='detailed')))
        visible=html.document_fromstring(standard['html']).xpath('//main')[0].text_content()
        self.assertNotIn('补充图表',visible)

    def test_rerendering_legacy_content_preserves_data_without_orphan_figures(self):
        content=market_fixture();content['draft'].pop('argument_visual_scope_version');bound(content)
        frozen = canonical(content)
        output=design(content,ROOT,profile='detailed')
        self.assertFalse(any(spec.get('target','').startswith('supplement-') for spec in logic_figure_plan(content)))
        self.assertEqual(len(output['document']['dataset']['rows']),12)
        self.assertEqual(canonical(content),frozen)

    def test_no_citation_still_delivers_available_data(self):
        content=market_fixture();content['draft']['logics']=[dict(title='研究观察',analysis='保留已取得资料。',implication='',risk='')];bound(content)
        self.assertFalse(logic_figure_plan(content))
        result=design(content,ROOT)
        self.assertIn('保留已取得资料',result['html']);self.assertEqual(len(result['document']['dataset']['rows']),12)

    def test_partial_financial_pair_stays_inside_its_own_logic(self):
        task=sample();task.update(questions=[],evidence=[],calculations=[],knowledge={},gaps=[],tracking=[])
        for index,record in enumerate(task['source_records']):
            period=record['parameters']['indipara'][0]['indiparams'][0]
            record['parameters']['indipara'].append(dict(indicator='ths_operating_cost_stock',indiparams=[period,'1']))
            record['snapshot']['tables'][0]['table']['ths_operating_cost_stock']=[50 if index==0 else 54]
            rehash(record)
        facts=observations(task);task['draft_analysis']=analyze(task,facts)
        refs=[r['id'] for r in facts if r['field'] in ('ths_revenue_stock','ths_operating_cost_stock')]
        proposal=dict(title='盈利观察',conclusion='按同口径比较。',logics=[dict(title='成本与收入',analysis='比较差额占比。',data_refs=refs)])
        task['draft_report']=dict(normalize_report(proposal,task,facts),facts=facts,
            calculations=task['draft_analysis']['results'],source_binding=source_binding(task))
        content=compose(task)
        self.assertFalse(any(spec.get('kind')=='paired' for spec in logic_figure_plan(content)))
        paper=html.document_fromstring(design(content,ROOT,profile='detailed')['html'])
        section=paper.get_element_by_id('logic-1')
        self.assertEqual(set(section.xpath('.//tr/@data-observation-id')),set(refs))
        ratios=[row for row in task['draft_analysis']['results'] if row['method']=='raw_margin_v1']
        self.assertEqual([row['value'] for row in ratios],[50.0,55.0])
        self.assertTrue(all(set(row['data_refs'])<=set(refs) for row in ratios))

    def test_figure_and_reading_follow_named_issuer_and_statement(self):
        task = sample()
        task.update(draft_client_copy_version=2, questions=[], evidence=[], calculations=[], knowledge={},
                    question='比较甲股份600519.SH与乙股份000001.SZ的财务', objects=['600519.SH','000001.SZ'])
        for code, basis, values in [('600519.SH','2',[200,250]), ('000001.SZ','1',[100,110])]:
            for period, value in zip(('20241231','20251231'), values):
                record = add_record(task, period, (value,), fields=('ths_revenue_stock',))
                record['parameters']['codes'] = record['snapshot']['tables'][0]['thscode'] = code
                record['parameters']['indipara'][0]['indiparams'][1] = basis
                rehash(record)
        facts = observations(task)
        task['draft_analysis'] = analyze(task,facts)
        calculations = task['draft_analysis']['results']
        a, parent, b = ['D1','D4'], ['D7','D8'], ['D9','D10']
        a_calc = next(calc['id'] for calc in calculations if calc['data_refs'] == a)
        claimed = '2025年营业收入同比增长20.00%。'
        proposal = dict(title='两家公司收入对照', conclusion='对照已取得的原值。', logics=[
            dict(title='乙股份的营业收入', analysis=claimed, calculation_refs=[a_calc],
                 mechanism=dict(steps=[dict(label='观察乙股份的收入变化',kind='observation',data_refs=a),
                                       dict(label='比较回款',kind='condition',data_refs=[])])),
            dict(title='收入表现', analysis='乙股份'+claimed, data_refs=a),
            dict(title='甲股份母公司报表', analysis='2025年营业收入同比增长25.00%。', data_refs=a+parent),
            dict(title='甲股份与乙股份比较', analysis='分开观察合并报表。', data_refs=a+b)])
        before = canonical(proposal)
        task['draft_report'] = dict(normalize_report(proposal,task,facts), facts=facts,
                                    calculations=calculations, source_binding=source_binding(task))
        content = compose(task)
        self.assertEqual(task['draft_report']['logics'][0]['data_refs'],a)
        self.assertEqual(task['draft_report']['logics'][0]['calculation_refs'],[a_calc])
        self.assertEqual(canonical(proposal),before)
        for profile in ('standard','detailed'):
            with self.subTest(profile=profile):
                output = design(content,ROOT,profile=profile)
                page = html.document_fromstring(output['html'])
                self.assertEqual(len(output['document']['dataset']['rows']),len(facts))
                for index in (1,2):
                    logic = page.get_element_by_id(f'logic-{index}')
                    self.assertIn('缺少可核对的读数',logic.text_content())
                    self.assertFalse(logic.xpath('.//figure|.//tr[@data-observation-id]|.//a[@class="quantity-link"]'))
                    for target in logic.xpath('./h2/a/@href'):
                        self.assertFalse(page.get_element_by_id(target[1:]).xpath('.//tr[@data-observation-id]|.//*[local-name()="math"]'))
                expected = {'logic-3':set(parent),'logic-4':set(a+b)}
                for key, refs in expected.items():
                    section = page.get_element_by_id(key)
                    specs = [block['spec'] for block in output['document']['blocks'] if block['kind']=='chart'
                             and block['spec'].get('paper_logic')==key]
                    presented={ref for spec in specs for ref in spec['data_refs']}
                    presented.update(section.xpath('.//table//*[@data-observation-id]/@data-observation-id'))
                    presented.update(section.xpath('.//p//*[@data-observation-id]/@data-observation-id'))
                    for inputs in section.xpath('.//*[@data-calculation-ref]/@data-calculation-inputs'):
                        presented.update(inputs.split())
                    self.assertEqual(presented, refs)
                    self.assertEqual(set(output['document']['figure_coverage'][key]['data_refs']), refs)
                    for href in section.xpath('.//figcaption//a/@href'):
                        popup = page.get_element_by_id(href[1:])
                        self.assertTrue(set(popup.xpath('.//tr/@data-observation-id'))<=refs)


if __name__=='__main__':unittest.main()
