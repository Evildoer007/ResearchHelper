"""Keep explicit local-material requests local while still writing a report."""
import json
import socket
from types import SimpleNamespace
from unittest.mock import patch

from lxml import html
import pytest

from core.session import Session
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import canonical, charge
from researchhelper.draft_sources import (acquisition_coverage, local_scope_text, local_sources_only, offline_requested,
                                          stated_latest_requests)
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import data_request_contract
from tests import ROOT
from tests.test_draft_account_policy import financial_request, quote_request


@pytest.mark.parametrize(('question', 'missing'), [
    ('核对510300.SH最近一个交易日的单位净值。请勿联网。', ('510300.SH的最近单位净值',)),
    ('核对600519.SH最新收盘价，只用本地材料。', ('600519.SH的最近收盘价',)),
    ('核对贵州茅台最新收盘价。不联网。', ('贵州茅台的最近收盘价',)),
    ('请勿联网。核对510300.SH最新单位净值。', ('510300.SH的最近单位净值',)),
    ('核对510300.SH最新单位净值，只用本地材料，给出简短报告。', ('510300.SH的最近单位净值',)),
    ('核对600519.SH最新收盘价和510300.SH最新单位净值。只用附件。',
     ('600519.SH的最近收盘价', '510300.SH的最近单位净值')),
    ('核对600519.SH最新收盘价及PE(TTM)。只用附件。', ('600519.SH的最近收盘价', '600519.SH的最近PE(TTM)')),
    ('全程离线，核对600519.SH最新收盘价及PE(TTM)。', ('600519.SH的最近收盘价', '600519.SH的最近PE(TTM)')),
    ('核对510300.SH最新单位净值，本轮只用新上传的附件。', ('510300.SH的最近单位净值',)),
])
def test_local_recent_market_report_names_missing_value(tmp_path, question, missing):
    from researchhelper.report_designer import render_html

    session = Session(tmp_path)
    try:
        initial = session.create(question, research_mode='current')
        task = session.status(initial['id'])
        coverage = acquisition_coverage(task)
        assert stated_latest_requests(task, coverage) == []
        tools = session.tools(task['id'], scope='acquisition')
        assert not tools.get('stated_latest_requests') and not tools['data_requests']['tools']
        with (patch.object(socket.socket, 'connect', side_effect=AssertionError('No external network')),
              patch.object(session.agent.collector, 'external', side_effect=AssertionError('External dispatch')) as collect):
            done = session.resume(task['id'])
        collect.assert_not_called()
        assert done['status'] == '部分完成' and done['delivery'] and not done['draft_report']['facts']
        assert not done['draft_report']['logics']
        assert done['ledger']['used']['data_requests'] == done['ledger']['used']['model_calls'] == 0
        for item in missing:
            assert f'现有资料无法核对{item}' in done['draft_report']['readout_gaps']
        report = session.archive.report(task['id'], 1)
        for profile in ('standard', 'detailed'):
            document = html.document_fromstring(render_html(report['content'], ROOT, profile=profile))
            summary = document.get_element_by_id('summary')
            title = document.find('.//main//h1').text_content()
            assert title == done['draft_report']['title'] and title not in ('研究报告', '混合研究')
            if question.startswith('核对600519.SH最新收盘价及PE'):
                assert title == '600519.SH收盘价与PE(TTM)核对'
            assert not document.xpath('//footer[contains(@class,"report-source-line")]')
            for item in missing:
                assert f'现有资料无法核对{item}。' in summary.text_content()
            assert not summary.xpath('.//a[@class="quantity-link"]')
            assert '策略逻辑' not in document.find('.//main').text_content()
    finally:
        session.agent.close_runtime()


@pytest.mark.parametrize('missing_endpoint', [False, True])
def test_retained_unit_nav_report_uses_only_the_requested_date_range(tmp_path, missing_endpoint):
    from researchhelper.market_series import NAV_INDICATOR
    from researchhelper.report_designer import render_html

    engine = ResearchAgent(ResearchArchive(tmp_path), ROOT, offline=True, draft_first=True,
                           require_argument_support=True)
    code = '510300.SH'
    dates = ['2026-10-19', '2026-10-20', '2026-10-21', '2026-10-22']
    nav = [4, 4.1, None if missing_endpoint else 4.2, 4.3]
    shares = 'ths_fund_shares_fund'
    try:
        task = engine.create(f'本轮不联网，核对{code}从{dates[0]}至{dates[2]}的单位净值及区间变化。',
                             research_mode='current')
        response = dict(errorcode=0, tables=[dict(thscode=code, time=dates, table={NAV_INDICATOR:nav,shares:[100]*4})])
        engine.add_snapshot(task['id'], 'ifind', 'date_sequence', dict(codes=code, startdate=dates[0],
            enddate=dates[-1], functionpara={'Fill':'Blank'},
            indipara=[dict(indicator=name,indiparams=['']) for name in (NAV_INDICATOR,shares)]), response)
        with patch.object(socket.socket, 'connect', side_effect=AssertionError('No external network')):
            done = engine.start(task['id'])
        assert done['delivery'] and all(done['ledger']['used'][key]==0 for key in ('model_calls', 'data_requests'))
        saved, = done['source_records']
        assert saved['snapshot']==response and not saved.get('question_ids')
        draft = done['draft_report']
        assert [(row['field'],row['period'],row['value']) for row in draft['facts']]==[
            (NAV_INDICATOR, date, value) for date,value in zip(dates[:3], nav[:3])]
        changes = [row for row in draft['calculations'] if row['method']=='raw_change_v1']
        if missing_endpoint:
            assert not changes and any('缺少2026-10-21的可用单位净值' in note for note in draft['reader_notes'])
        else:
            change, = changes
            assert (change['window_start'],change['window_end'],change['display'])==(dates[0],dates[2],'5.00')
            assert '不等于持有人总回报' in change['condition']
        content = engine.archive.report(task['id'], 1)['content']
        for profile in ('standard','detailed'):
            page = html.document_fromstring(render_html(content, ROOT, profile=profile))
            summary = page.get_element_by_id('summary').text_content()
            assert '2026-10-22' not in summary and '基金份额' not in summary
            assert ('5.00%' in summary) is not missing_endpoint
            wire = json.loads(page.get_element_by_id('rh-publication-document').text)
            assert [row[2] for row in wire['dataset']['rows']]==dates[:3]
            if missing_endpoint:
                assert not any(row['kind']=='chart' and row['spec']['type']=='line' and
                               row['spec'].get('field')==NAV_INDICATOR for row in wire['blocks'])
    finally:
        engine.close_runtime()


def test_previous_latest_policy_keeps_the_frozen_scope(tmp_path):
    from researchhelper.market_series import STATED_LATEST_COMPOUND_VERSION, STATED_LATEST_POLICY

    session = Session(tmp_path)
    try:
        task = session.create('核对510300.SH最新单位净值，不联网。', research_mode='current')
        assert acquisition_coverage(task)['stated_latest_observation']['local_sources_only']
        earlier = dict(task, **{STATED_LATEST_POLICY: STATED_LATEST_COMPOUND_VERSION})
        assert 'stated_latest_observation' not in acquisition_coverage(earlier)
    finally:
        session.agent.close_runtime()


def test_local_latest_report_names_the_known_missing_session(tmp_path):
    from researchhelper.market_calendar import FUNCTION
    from researchhelper.market_series import NAV_INDICATOR, series_settings
    from tests.test_market_calendar_coverage import append_calendar_fixture_record

    session = Session(tmp_path)
    try:
        code, previous, last = '510300.SH', '2026-11-12', '2026-11-13'
        with patch('researchhelper.engine.now', return_value='2026-11-13T14:00:00+00:00'):
            task = session.create(f'核对{code}最近一个交易日的单位净值，只用本地材料。')
        task.update(batch=1, status='已取消', run_mode='model', synthetic=True)
        append_calendar_fixture_record(task, 'date_sequence', dict(codes=code, startdate='2026-10-14', enddate=previous,
            functionpara=series_settings(task, code), indipara=[dict(indicator=NAV_INDICATOR, indiparams=[''])]),
            dict(errorcode=0, tables=[dict(thscode=code, time=[previous], table={NAV_INDICATOR:[4.11]})]))
        append_calendar_fixture_record(task, 'get_trade_dates', dict(startdate='2026-10-14', enddate=last,
            marketcode='212001', functionpara=FUNCTION), dict(errorcode=0, tables=dict(time=[previous,last])))
        session.archive.save(task)
        saved = session.finish_draft(task['id'])
        assert saved['delivery'] and saved['ledger']['used']['model_calls'] == 0
        assert saved['ledger']['used']['data_requests'] == task['ledger']['used']['data_requests']
        assert saved['draft_report']['readout_gaps'] == [f'{code}在最近一个交易日{last}的单位净值暂缺']
        report = html.document_fromstring(session.archive.report(task['id'], 1)['html'])
        assert f'{last}的单位净值暂缺' in report.get_element_by_id('summary').text_content()
        assert not report.get_element_by_id('summary').xpath('.//a[@class="quantity-link"]')
        assert previous in report.find('.//main').text_content()
    finally:
        session.agent.close_runtime()


@pytest.mark.parametrize('question', [
    '解释510300.SH最新单位净值为什么变化。请勿联网。',
    '不要查询600519.SH最新收盘价。请勿联网。',
    '用上交所申赎清单核对510300.SH最新单位净值，只用iFinD。请勿联网。',
    '核对510300.SH最新单位净值，只用本地材料。接着分析板块趋势。',
])
def test_local_instruction_does_not_erase_other_scope_constraints(tmp_path, question):
    session = Session(tmp_path)
    try:
        task = session.archive.get(session.create(question, research_mode='current')['id'])
        coverage = acquisition_coverage(task)
        assert 'stated_latest_observation' not in coverage and 'stated_latest_observations' not in coverage
        assert not stated_latest_requests(task, coverage)
    finally:
        session.agent.close_runtime()


@pytest.mark.parametrize(('scope', 'offline', 'writes_with_model'), [
    ('只用附件', False, True),
    ('只基于我上传的材料', False, True),
    ('本次只用新上传的附件', False, True),
    ('仅依据我刚上传的两份报告', False, True),
    ('不再取数', False, True),
    ('不联网', False, False),
    ('请勿联网', False, False),
    ('本轮全程不联网', False, False),
    ('这次请全程离线', False, False),
    ('只用附件', True, False),
])
def test_explicit_local_scope_skips_planning_and_preserves_attachment(tmp_path, scope, offline, writes_with_model):
    phases = []
    statement = '订单集中交付可能推高短期营运资金需求。'

    def reason(task, messages, *, phase, delivery=False, **kwargs):
        phases.append(phase)
        charge(task['ledger'], 'model_calls', delivery=delivery)
        if phase == 'draft_plan':
            return canonical(dict(data_requests=[quote_request()]))
        document, = json.loads(messages[-1]['content'])['data']['documents']
        assert document['name'] == '经营说明.txt'
        return canonical(dict(title='交付节奏与营运资金', conclusion=statement,
            logics=[dict(title='交付节奏', analysis=f'原件写道“{statement}”',
                         source_refs=[document['source_ref']])]))

    archive = ResearchArchive(tmp_path)
    agent = ResearchAgent(archive, ROOT, offline=offline, model=SimpleNamespace(reason=reason), draft_first=True)
    try:
        task = agent.create(f'核对600519.SH材料中对交付节奏与营运资金的说明，{scope}。',
                            research_mode='current')
        agent.add_material(task['id'], '经营说明.txt', statement.encode())
        with (patch.object(socket.socket, 'connect', side_effect=AssertionError('No external network')),
              patch.object(agent.collector, 'external', side_effect=AssertionError('External data dispatched')) as collect):
            finished = agent.start(task['id'])
        collect.assert_not_called()
        assert phases == (['draft_report'] if writes_with_model else [])
        assert finished['ledger']['used']['data_requests'] == 0
        assert finished['ledger']['used']['model_calls'] == int(writes_with_model)
        assert finished['delivery']
        paper = html.document_fromstring(archive.report(task['id'], finished['version'])['html'])
        main = paper.xpath('//main')[0]
        assert statement in main.text_content()
        assert '经营说明.txt' in paper.text_content()
        if not writes_with_model:
            assert '材料原文' in main.text_content()
            assert '公开原文' not in main.text_content()
        assert not data_request_contract(finished, first_report=True)['tools']
    finally:
        agent.close_runtime()


def test_skill_honors_local_scope_even_if_host_proposes_a_financial_request(tmp_path):
    statement = '订单集中交付可能推高短期营运资金需求。'
    session = Session(tmp_path)
    try:
        task = session.create('核对600519.SH材料中的营运资金，只有附件？只用附件。')
        session.agent.add_material(task['id'], '经营说明.txt', statement.encode())
        assert session.tools(task['id'], scope='acquisition')['data_requests']['tools'] == []
        with patch.object(session.agent.collector, 'external', side_effect=AssertionError('External dispatch')) as collect:
            saved = session.draft(task['id'], data_requests=[financial_request(), quote_request()])
        collect.assert_not_called()
        assert saved['delivery'] and saved['ledger']['used']['data_requests'] == saved['ledger']['used']['model_calls'] == 0
        assert saved['draft_report']['logics'][0]['title'] == '材料原文'
    finally:
        session.agent.close_runtime()


@pytest.mark.parametrize('quote', [
    '“设备说明，不联网，只用附件。”', '「设备说明，只用新上传的附件。」',
    '《演练手册：练习开始，不联网，随后恢复》', '"示例：先登记，不联网，再处理"',
    '`离线约定，禁止联网。`', '```text\n说明，不联网，只用附件。\n```',
])
def test_quoted_statements_do_not_set_execution_scope(quote):
    question = f'解释引文{quote}所说的操作限制。'
    assert not offline_requested(question)
    assert not local_sources_only({'question':question})
    assert local_scope_text(question) == question
    requested = '本轮不联网，'+question
    assert offline_requested(requested)
    assert local_scope_text(requested) == '，'+question


def test_standalone_definition_uses_knowledge_with_offline_scope_only(tmp_path):
    engine = ResearchAgent(ResearchArchive(tmp_path), ROOT, offline=True, draft_first=True,
                           require_argument_support=True)
    try:
        question = '本次请保持离线，解释CPI同比和CPI环比有什么区别。'
        task = engine.create(question, research_mode='current')
        answer = engine.start(task['id'])
        assert answer['status']=='已完成' and answer['question']==question
        assert all(answer['ledger']['used'][key]==0 for key in ('model_calls','data_requests'))
        assert answer['questions'][0]['knowledge_refs'][0]['id']=='rh_k_cpi_reading'
        limited = engine.create(question+'只用新上传的附件。', research_mode='current')
        assert engine.knowledge.standalone_definition(limited) is None
    finally:
        engine.close_runtime()


def test_offline_historical_research_does_not_endorse_a_late_upload(tmp_path):
    statement = '订单集中交付可能推高短期营运资金需求。'
    archive = ResearchArchive(tmp_path)
    agent = ResearchAgent(archive, ROOT, offline=True, draft_first=True)
    try:
        task = agent.create('研究600519.SH的营运资金和股价走势，不联网。',
                            cutoff='2025-12-31', research_mode='historical')
        agent.add_material(task['id'], '经营说明.txt', statement.encode())
        saved = agent.start(task['id'])
        assert saved['delivery'] and saved['ledger']['used']['data_requests'] == saved['ledger']['used']['model_calls'] == 0
        paper = html.document_fromstring(archive.report(task['id'], saved['version'])['html'])
        assert statement not in paper.xpath('//main')[0].text_content()
        assert all(not row.get('document_excerpts') for row in saved['draft_report']['logics'])
    finally:
        agent.close_runtime()


@pytest.mark.parametrize('question', [
    '查600519.SH收盘价，不下载PDF。',
    '查600519.SH收盘价，尽量只用已有材料。',
    '解释这个工具为什么不能联网。',
    '查600519.SH收盘价，不查净值。',
])
def test_metric_preferences_are_not_global_acquisition_bans(question):
    assert not local_sources_only(dict(question=question))
