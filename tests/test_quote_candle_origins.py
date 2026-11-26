"""行情刷新保留可用原值，K线按原始响应行绑定。"""
import csv
import io
import json
import socket
from tempfile import TemporaryDirectory
from unittest.mock import patch

from lxml import html
import pytest

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import charge, fingerprint, now
from researchhelper.draft_sources import observations
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import _record_current_acquisition, current_query_reference, snapshot_record
from researchhelper.report_designer import render_html


def acquire_quote(task, code, days, columns):
    """Exercise the same cell replacement rules with an isolated response."""
    operation = 'cmd_history_quotation'
    params = dict(codes=code, indicators=','.join(columns), startdate=days[0], enddate=days[-1],
                  functionpara=dict(Currency='RMB', CPS='1', Fill='Omit'))
    response = dict(errorcode=0, tables=[dict(thscode=code, time=days, table=columns)])
    record = snapshot_record(task, 'ifind', operation, params, response)
    charge(task['ledger'], 'data_requests')
    event = dict(kind='data_requests', source='ifind', operation=operation, batch=task['batch'],
                 fingerprint=record['request_hash'], status='成功', time=now(), transport_isolated=True,
                 network_request_started=True, network_worker_reaped=True, synthetic_test_transport=True)
    task['ledger']['dispatches'].append(event)
    _record_current_acquisition(task, record, event)
    assert current_query_reference(task, record)
    task['source_records'].append(record)
    return record['id']


def replay_quotes(agent, submit, *, owner_account=None):
    code = '600519.SH'
    days = ['2026-10-12', '2026-10-13', '2026-10-14']
    columns = dict(open=[100, 104, 106], close=[102, 105, 108], low=[98, 101, 104], high=[105, 109, 110])
    task = agent.create('核对贵州茅台的日频开高低收。', entry='个股', objects=[code],
                        research_mode='current', owner_account=owner_account)
    ident = task['id']
    saved_reports, result = {}, []
    for version in (1, 2, 3, 4):
        if version > 1:
            task = agent.prepare_update(ident)
        if version == 1:
            original = acquire_quote(task, code, days, columns)
        elif version == 2:
            corrected = acquire_quote(task, code, days[1:2], dict(close=[107]))
        elif version == 4:
            complete = acquire_quote(task, code, days[1:2], dict(open=[104], close=[107], low=[101], high=[109]))
        task['synthetic'] = True
        agent.archive.save(task)
        facts = observations(task)
        expected = {(day, field): readings[i] for field, readings in columns.items() for i, day in enumerate(days)}
        if version > 1:
            expected[days[1], 'close'] = 107
        assert {(row['period'], row['field']): row['value'] for row in facts} == expected
        assert len(facts) == len(expected)
        sources = {record['id']: record for record in task['source_records']}
        assert all(fingerprint(record['snapshot']) == record['hash'] for record in sources.values())
        for fact in facts:
            loc = fact['locator']
            table = sources[fact['artifact_id']]['snapshot']['tables'][loc['table']]
            assert table['time'][loc['row']] == fact['period']
            assert table['table'][loc['field']][loc['row']] == fact['value']
            field_source = original
            if fact['period'] == days[1]:
                if version == 4:
                    field_source = complete
                elif version > 1 and fact['field'] == 'close':
                    field_source = corrected
            assert fact['artifact_id'] == field_source
        proposal = dict(title='日频行情', conclusion='对照已保存的日频价格。', logics=[
            dict(title='日频价格变化', analysis='同一标的按日对照四个行情字段。',
                 data_refs=[row['id'] for row in facts])])
        done = submit(ident, proposal)
        assert done.get('delivery'), done.get('error')
        assert done['draft_report']['facts'] == facts
        assert done['ledger']['used']['model_calls'] == 0
        assert done['ledger']['used']['data_requests'] == int(version != 3)
        saved_reports[version] = agent.archive.report(ident, version)
        result.append(dict(task_id=ident, version=version, days=days, facts=facts))
    assert saved_reports == {v: agent.archive.report(ident, v) for v in saved_reports}
    return result


def verify_quote_page(page, replay, profile, *, baseline=False):
    document = json.loads(html.document_fromstring(page).get_element_by_id('rh-publication-document').text)
    assert document['profile'] == profile
    version, days, facts = (replay[key] for key in ('version', 'days', 'facts'))
    expected_cells = {(fact['code'], fact['label'], fact['period'], fact['value']) for fact in facts}
    table = document['dataset']['rows']
    assert len(table) == len(expected_cells)
    assert {tuple(row[:4]) for row in table} == expected_cells
    charts = list(document['visual_sources'].values())
    candles = [chart for chart in charts if chart['auto_visual']['scheme'] == 'candlestick']
    if baseline or version in (1, 4):
        candle, = candles
        fields = ('open', 'close', 'low', 'high')
        by_cell = {(fact['period'], fact['field']): fact for fact in facts}
        expected = [[by_cell[day, field] for field in fields] for day in days]
        assert candle['auto_visual']['data']['labels'] == days
        assert candle['auto_visual']['data']['values'] == [[row['value'] for row in point] for point in expected]
        assert set(candle['data_refs']) == {fact['id'] for fact in facts}
        if not baseline:
            assert candle['point_refs'] == [[row['id'] for row in point] for point in expected]
        assert (len({row['artifact_id'] for row in expected[1]}) == 2) == (version in (2, 3))
    else:
        assert not candles, f'Version {version} used a candle with mixed quote origins'
        assert len(charts) == 4
        for chart in charts:
            field = chart['field']
            expected = sorted((fact['period'], fact['value'], fact['id']) for fact in facts if fact['field'] == field)
            assert list(zip(chart['dates'], chart['values'], chart['data_refs'])) == expected
    return dict(candles=len(candles), schemes=sorted(chart['auto_visual']['scheme'] for chart in charts),
                observed_cells=len(table))


def verify_quote_csv(raw, replay):
    rows = list(csv.reader(io.StringIO(raw.decode('utf-8-sig'))))[1:]
    expected = {(fact['code'], fact['label'], fact['period'], str(fact['value'])) for fact in replay['facts']}
    assert len(rows) == len(expected)
    assert {tuple(row[:4]) for row in rows} == expected


def test_partial_quote_refresh_retains_values_without_mixing_candle_origins():
    with TemporaryDirectory() as folder, patch.object(socket.socket, 'connect', side_effect=AssertionError('No network')):
        agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True, draft_first=True)
        try:
            replay = replay_quotes(agent, lambda ident, proposal: agent.start(
                ident, dict(data_requests=[], draft_report=proposal)))
            for row in replay:
                report = agent.export(row['task_id'], row['version'])
                for profile in ('standard', 'detailed'):
                    verify_quote_page(render_html(report['content'], ROOT, profile=profile), row, profile)
        finally:
            agent.close_runtime()


@pytest.mark.parametrize('case,has_candle', [('complete',True), ('flat',True), ('missing',False),
                                            ('mixed',False), ('outside_range',False)])
def test_single_session_presents_only_same_row_ohlc(tmp_path,case,has_candle):
    with patch.object(socket.socket,'connect',side_effect=AssertionError('No network')):
        agent=ResearchAgent(ResearchArchive(tmp_path),ROOT,offline=True,require_argument_support=True,draft_first=True)
        try:
            code,day='510300.SH','2026-10-15'
            task=agent.create('核对510300.SH单日的开盘、最高、最低和收盘价。',research_mode='current')
            fields=dict(open=['4.12345'],high=['4.25'],low=['4.09'],close=['4.1587'])
            if case=='flat':fields={field:['4.10'] for field in fields}
            if case=='outside_range':fields['high']=['4.12']
            split_close=fields.pop('close') if case=='mixed' else None
            if case=='missing':fields['high']=[None]
            acquire_quote(task,code,[day],fields)
            if split_close:acquire_quote(task,code,[day],dict(close=split_close))
            agent.archive.save(task)
            actual=observations(task)
            proposal=dict(title='价格观察',conclusion='核对单日行情。',logics=[dict(title='价格观察',
                          analysis='按同一交易日列出实际读数。',data_refs=[row['id'] for row in actual])])
            finished=agent.start(task['id'],dict(draft_report=proposal,data_requests=[]))
            assert finished['delivery'] and finished['draft_report']['facts']==actual
            report=agent.archive.report(task['id'],1)
            for profile in ('standard','detailed'):
                paper=html.document_fromstring(render_html(report['content'],ROOT,profile=profile))
                document=json.loads(paper.get_element_by_id('rh-publication-document').text)
                charts=list(document['visual_sources'].values())
                assert len(charts)==int(has_candle)
                assert len(document['dataset']['rows'])==len(actual)
                if not has_candle:
                    continue
                spec=charts[0];data=spec['auto_visual']['data']
                assert spec['auto_visual']['scheme']=='candlestick' and data['labels']==[day]
                by_field={row['field']:row for row in actual}
                ordered=[by_field[field] for field in ('open','close','low','high')]
                assert data['values']==[[row['value'] for row in ordered]]
                assert spec['point_refs']==[[row['id'] for row in ordered]]
                links=paper.xpath('//main//a[@data-chart-observation]')
                assert [link.text for link in links]==[by_field[key]['raw_value'] for key in ('open','high','low','close')]
                assert {link.get('data-chart-observation') for link in links}=={row['id'] for row in ordered}
        finally:
            agent.close_runtime()
