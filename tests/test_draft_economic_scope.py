"""Economic observations stay within the requested series and actual cutoff.

No network or model is used. Retained real data is read without rewriting it;
synthetic anomalies exercise local exclusions and continued report delivery.
"""
from copy import deepcopy
import hashlib
import json
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pytest

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError,canonical,charge,fingerprint,now
from researchhelper.draft_sources import (VERSION,READER_POLICY,observations,
    acquisition_coverage, economic_frequencies, source_context,source_issues,writing_context)
from researchhelper.draft_analysis import analyze
from researchhelper.drafts import normalize_report
from researchhelper.engine import ResearchAgent
from researchhelper.economic_sources import DIRECT_ECONOMIC_POLICY, DRAFT_PERIOD_POLICY, DRAFT_PERIOD_VERSION
from researchhelper.evidence import _record_current_acquisition, snapshot_record
from researchhelper.planning import input_fingerprint

CODE='M002826730'
OTHER='M999999999'
SENTINEL=987654321
RETAINED=ROOT/'result/acceptance/economic-discovery-live-20261023-v1/task.json'


@pytest.mark.parametrize('scenario', ['complete', 'gap', 'supplement'])
def test_batched_economic_coverage_keeps_each_series_independent(tmp_path, scenario):
    from researchhelper.report_designer import design

    path = ROOT/'result/acceptance/inflation-knowledge-20261101/task.json'
    original_bytes = path.read_bytes()
    series, = [record for record in json.loads(original_bytes)['source_records']
               if record['operation'] == 'edb_service']
    full = series['parameters']
    food = 'M002826732'
    months = ('2026-04-30', '2026-08-31')
    april = dict(full, indicators=food, startdate='2026-04-01', enddate='2026-04-30')
    august = dict(full, indicators=food, startdate='2026-08-01', enddate='2026-08-31')
    expected = {cell['id'][0]: dict(zip(cell['time'], cell['value'], strict=True))
                for cell in series['snapshot']['tables']}
    calls, phases = [], []

    def transport(task, source, operation, parameters, credentials):
        assert (source, operation) == ('ifind', 'edb_service')
        calls.append(parameters)
        snapshot = deepcopy(series['snapshot'])
        if parameters == full:
            window = months if scenario != 'complete' else ()
        else:
            assert parameters in (april, august)
            window = ()
            snapshot['tables'] = [cell for cell in snapshot['tables'] if cell['id'] == [food]]
            snapshot['inputParams'].update(sdate=parameters['startdate'].replace('-', ''),
                                           edate=parameters['enddate'].replace('-', ''))
        for cell in snapshot['tables']:
            indices = [index for index, day in enumerate(cell['time'])
                       if parameters['startdate'] <= day <= parameters['enddate']
                       and (cell['id'] != [food] or day not in window)]
            for key in ('time', 'value', 'index_name', 'rtime'):
                cell[key] = [cell[key][index] for index in indices]
        charge(task['ledger'], 'data_requests')
        record = snapshot_record(task, source, operation, parameters, snapshot)
        event = dict(kind='data_requests', source=source, operation=operation, batch=task['batch'],
                     status='成功', fingerprint=record['request_hash'], time=now(), transport_isolated=True,
                     network_request_started=True, network_worker_reaped=True)
        task['ledger']['dispatches'].append(event)
        return _record_current_acquisition(task, record, event)

    def model(task, messages, *, phase, delivery=False, **options):
        phases.append(phase)
        charge(task['ledger'], 'model_calls', delivery=delivery)
        if phase == 'draft_report':
            return canonical(dict(title='CPI分项月度核对', report_scope='comparison', conclusion='逐项核对已取得的月度读数。',
                logics=[dict(title='月度读数', analysis='各序列保留原值与原始期间。',
                             data_refs=[row['id'] for row in observations(task)])]))
        if len(phases) == 1:
            windows = [('batch_edb', full)]
        else:
            coverage = json.loads(messages[-1]['content'])['acquisition_coverage']
            pending, = coverage['unresolved']
            assert pending['missing'] == [dict(code=food, field='value',
                 periods=['2026年4月', '2026年8月'], missing_period_count=2)]
            assert coverage['needs_followup']
            windows = [('april', april), ('august', august)] if scenario == 'supplement' else []
        return canonical(dict(data_requests=[dict(id=ident, source='ifind', operation='edb_service',
            parameters=parameters, question_ids=['q1'], selection_reason='核对原问题指定指标的实际月份。')
            for ident, parameters in windows]))

    archive = ResearchArchive(tmp_path)
    agent = ResearchAgent(archive, ROOT, offline=False, model=SimpleNamespace(reason=model),
                          require_argument_support=True, draft_first=True)
    agent.collector.external = transport
    try:
        question = f'逐月核对{full["indicators"].replace(",", "、")}在2026年1月至9月的读数，生成报告。'
        with patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline EDB replay')):
            task = agent.create(question, research_mode='current')
            done = agent.start(task['id'])
        assert done.get('delivery'), done.get('error')
        assert economic_frequencies(done) == dict.fromkeys(expected, 'M')
        assert phases == ['draft_plan'] * (1 if scenario == 'complete' else 2) + ['draft_report']
        assert calls == ([full, april, august] if scenario == 'supplement' else [full])
        if scenario == 'gap':
            for month in months:
                del expected[food][month]
        assert done['draft_report'].get('incomplete_economic_series', []) == ([food] if scenario == 'gap' else [])
        actual = {code: {} for code in expected}
        for row in done['draft_report']['facts']:
            actual[row['code']][row['period']] = row['raw_value']
        assert actual == expected
        saved = archive.report(done['id'], done['version'])
        for profile in ('standard', 'detailed'):
            report = design(saved['content'], ROOT, profile=profile)
            assert ('缺少2026年4月、2026年8月读数' in report['html']) == (scenario == 'gap')
            if scenario == 'gap':
                specs = [spec for spec in report['document']['visual_sources'].values() if spec.get('field') == 'value']
                sparse, = [spec for spec in specs if spec['auto_visual']['scheme'] == 'ranked_bar']
                continuous, = [spec for spec in specs if spec['auto_visual']['scheme'] == 'multi_line']
                by_ref = {row['id']: row['code'] for row in done['draft_report']['facts']}
                assert {by_ref[ref] for ref in sparse['data_refs']} == {food}
                assert {by_ref[ref] for ref in continuous['data_refs']} == expected.keys() - {food}
        if scenario == 'gap':
            assert not acquisition_coverage(dict(done, **{DRAFT_PERIOD_POLICY: 1}))['unresolved']
        assert path.read_bytes() == original_bytes
    finally:
        agent.close_runtime()


@pytest.mark.parametrize('case,planning_calls,count', [
    ('ready',1,9), ('discover',2,9), ('empty',2,0), ('partial',2,1),
    ('direct_gap',2,8), ('direct_recovered',2,9), ('split_ready',1,9),
    ('short_window',2,9), ('short_repaired',2,9), ('short_named_repaired',2,9),
    ('short_named_empty',2,9), ('short_named_ambiguous',2,6),
])
def test_default_draft_only_supplements_an_unfinished_economic_search(tmp_path, case, planning_calls, count):
    originals = {row['operation']:row for row in json.loads(RETAINED.read_bytes())['source_records']
                 if row['operation'] in ('get_limit_policy','search_edb','edb_service')}
    patch_params = dict(originals['edb_service']['parameters'],startdate='2026-04-01',enddate='2026-04-30')
    short_window = case.startswith('short_')
    if short_window:
        full = originals.pop('edb_service')
        originals['late'] = dict(full, parameters=dict(full['parameters'], startdate='2026-04-01'))
        originals['early'] = dict(full, parameters=dict(full['parameters'], enddate='2026-03-31'))
    with_gap = case in ('direct_gap','direct_recovered','split_ready')
    phases, operations = [], []

    def model(task, messages, *, phase, delivery=False, **options):
        phases.append(phase)
        charge(task['ledger'],'model_calls',delivery=delivery)
        if phase == 'draft_report':
            refs = [row['id'] for row in observations(task)]
            return canonical(dict(title='宏观序列核对',conclusion='核对实际取得的指标资料。',
                logics=[dict(title='原值核对',analysis='仅使用取得的同口径读数。',data_refs=refs)]))
        if phases.count(phase) == 1:
            selected = {'discover': ('search_edb',), 'direct_gap': ('edb_service',),
                        'direct_recovered': ('edb_service',), 'split_ready': ('search_edb','edb_service','april'),
                        'short_window': ('late',), 'short_repaired': ('late',),
                        'short_named_repaired': ('search_edb','late'),
                        'short_named_empty': ('search_edb','late'),
                        'short_named_ambiguous': ('search_edb','late'),}.get(case, ('search_edb','edb_service'))
        else:
            selected = {'discover': ('edb_service',), 'direct_recovered': ('april',),
                        'short_repaired': ('early',), 'short_named_repaired': ('early',)}.get(case, ())
            if with_gap:
                payload = json.loads(messages[1]['content'])
                assert payload['acquisition_coverage']['unresolved'][0]['missing'][0]['periods'] == ['2026年4月']
            if short_window:
                payload = json.loads(messages[1]['content'])
                coverage = payload['acquisition_coverage']
                assert coverage['unresolved'] == []
                stated, = coverage['stated_economic_periods']
                assert stated['periods'] == stated['unqueried_periods'] == ['2026年1月','2026年2月','2026年3月']
                assert stated['expected_period_count'] == 9 and coverage['needs_followup']
        requests = [dict(id=key,source='ifind',operation='edb_service' if key in ('april','early','late') else key,
                         parameters=patch_params if key=='april' else originals[key]['parameters'],
                         question_ids=['q1'],selection_reason='核对原问题的指标口径及期间。') for key in selected]
        return canonical(dict(data_requests=requests))

    def transport(task, source, operation, params, credentials):
        operations.append(operation)
        original = next(row for row in originals.values() if row['parameters'] == params) if short_window else originals[operation]
        assert source == 'ifind' and (params == original['parameters'] or operation=='edb_service' and params==patch_params)
        snapshot = deepcopy(original['snapshot'])
        if case == 'short_named_empty' and operation == 'search_edb':
            text = snapshot['text']
            start, end = text.index('```json\n')+len('```json\n'), text.rfind('\n```')
            choices = json.loads(text[start:end])
            choices = [row for row in choices if row['displayId'] == CODE]
            snapshot['text'] = text[:start] + json.dumps(choices, ensure_ascii=False) + text[end:]
        if short_window and operation == 'edb_service':
            cells, = snapshot['tables']
            indices = [i for i, day in enumerate(cells['time']) if params['startdate'] <= day <= params['enddate']]
            for key in ('time','value','index_name','rtime'):
                cells[key] = [cells[key][i] for i in indices]
            snapshot['inputParams'].update(sdate=params['startdate'].replace('-',''),edate=params['enddate'].replace('-',''))
        if operation == 'edb_service' and case == 'empty':
            snapshot = dict(errorcode=0,tables=[])
        if operation == 'edb_service' and case == 'partial':
            cells = snapshot['tables'][0]
            for key in ('time','value','index_name','rtime'):
                cells[key] = cells[key][-1:]
        if operation == 'edb_service' and with_gap:
            cells = snapshot['tables'][0]
            indices = [i for i, day in enumerate(cells['time']) if (day == '2026-04-30') == (params == patch_params)]
            for key in ('time','value','index_name','rtime'):
                cells[key] = [cells[key][i] for i in indices]
            if params == patch_params:
                snapshot['inputParams'].update(sdate='20260401',edate='20260430')
        charge(task['ledger'],'data_requests')
        record = snapshot_record(task,source,operation,params,snapshot)
        event = dict(kind='data_requests',source=source,operation=operation,batch=task['batch'],
            status='成功',fingerprint=record['request_hash'],time=now(),transport_isolated=True,
            network_request_started=True,network_worker_reaped=True)
        task['ledger']['dispatches'].append(event)
        return _record_current_acquisition(task,record,event)

    archive = ResearchArchive(tmp_path)
    agent = ResearchAgent(archive,ROOT,offline=False,model=SimpleNamespace(reason=model),
                          require_argument_support=True,draft_first=True)
    agent.collector.external = transport
    try:
        named = '中国CPI当月同比' if case == 'discover' or case.startswith('short_named_') else '中国CPI当月同比M002826730'
        with patch.object(socket.socket,'connect',side_effect=AssertionError('Offline EDB replay')):
            frequency = '的逐月读数' if with_gap or short_window else '的读数'
            original = agent.create('比较'+named+'在2026年1月至9月'+frequency+'，生成报告。',research_mode='current')
            # This test exercises resuming a version created under the model-planning policy.
            original.pop(DIRECT_ECONOMIC_POLICY)
            original['input_hash'] = input_fingerprint(original)
            archive.save(original)
            done = agent.start(original['id'])
        assert done.get('delivery'), done.get('error')
        expected_operations = ['edb_service'] if case.startswith(('direct_','short_')) else ['get_limit_policy','search_edb','edb_service']
        if case.startswith('short_named_'):
            expected_operations = ['get_limit_policy','search_edb','edb_service']
        if case in ('direct_recovered','split_ready') or short_window and case != 'short_named_ambiguous':
            expected_operations.append('edb_service')
        assert operations == expected_operations
        assert len(done['draft_report']['facts']) == count
        assert done['draft_report']['origin'] == 'model'
        assert archive.report(done['id'],done['version'])['html_integrity_verified']
        assert phases == ['draft_plan']*planning_calls+['draft_report']
        if short_window:
            saved = archive.report(done['id'],done['version'])
            missing = case == 'short_named_ambiguous'
            assert ('缺少2026年1月、2026年2月、2026年3月读数' in saved['html']) == missing
            assert done['draft_report'].get('incomplete_economic_series', []) == ([CODE] if missing else [])
        if with_gap:
            saved = archive.report(done['id'],done['version'])
            assert ('缺少2026年4月' in saved['html']) == (case=='direct_gap')
            from researchhelper.report_designer import design
            for profile in ('standard','detailed'):
                specs = design(saved['content'], ROOT, profile=profile)['document']['visual_sources'].values()
                trends = {spec['auto_visual']['scheme'] for spec in specs if spec.get('field')=='value'}
                assert ('line' in trends) == (case!='direct_gap')
                if case=='direct_gap':
                    assert 'ranked_bar' in trends
    finally:
        agent.close_runtime()


@pytest.mark.parametrize('case', ['same_window', 'revised_date', 'null', 'conflict'])
def test_current_economic_update_replaces_only_returned_periods(tmp_path, case):
    from researchhelper.report_designer import design

    original = next(row for row in json.loads(RETAINED.read_bytes())['source_records']
                    if row['operation'] == 'edb_service')
    full = original['parameters']
    september = dict(full, startdate='2026-09-01')
    overlap = dict(full, startdate='2026-08-01')
    updates = [full] if case == 'same_window' else [september, overlap] if case == 'conflict' else [september]
    calls = []

    def transport(task, source, operation, params, credentials):
        assert (source, operation) == ('ifind', 'edb_service')
        calls.append((task['version'], params))
        snapshot = deepcopy(original['snapshot'])
        if task['version'] == 2:
            cells, = snapshot['tables']
            position = cells['time'].index('2026-09-30')
            for key in ('time', 'value', 'rtime', 'index_name'):
                cells[key] = [cells[key][position]]
            if case == 'revised_date':
                cells['time'] = ['2026-09-29']
            cells['value'] = [None if case == 'null' else 8.125 if params != overlap else 7.125]
            snapshot['inputParams']['sdate'] = params['startdate'].replace('-', '')
        charge(task['ledger'], 'data_requests')
        record = snapshot_record(task, source, operation, params, snapshot)
        event = dict(kind='data_requests', source=source, operation=operation, batch=task['batch'],
            status='成功', fingerprint=record['request_hash'], time=now(), transport_isolated=True,
            network_request_started=True, network_worker_reaped=True)
        task['ledger']['dispatches'].append(event)
        return _record_current_acquisition(task, record, event)

    def submit(windows):
        requests = [dict(id=f'edb_{index}', source='ifind', operation='edb_service', parameters=params,
            question_ids=['q1'], selection_reason='按原问题核对逐月读数。') for index, params in enumerate(windows)]
        return dict(data_requests=requests, draft_report=dict(title='宏观读数核对', conclusion='按统计期间列示读数。',
            logics=[dict(title='读数复核', analysis='对照实际取得的指标序列。',
                         data_refs=[f'D{index}' for index in range(1, 10)])]))

    archive = ResearchArchive(tmp_path)
    agent = ResearchAgent(archive, ROOT, offline=False, require_argument_support=True, draft_first=True)
    agent.collector.external = transport
    try:
        with patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline EDB replay')):
            task = agent.create(f'逐月核对中国CPI当月同比{CODE}在2026年1月至9月的读数。', research_mode='current')
            first = agent.start(task['id'], submit([full]))
            saved_first = archive.report(first['id'], 1)
            second = agent.update(first['id'], host_result=submit(updates))
            assert second.get('delivery'), second.get('error')
            saved_second = archive.report(first['id'], 2)
            third = agent.update(first['id'], host_result=submit([]))
        assert third.get('delivery'), third.get('error')
        assert calls == [(1, full), *((2, params) for params in updates)]
        assert archive.report(first['id'], 1) == saved_first
        assert archive.report(first['id'], 2) == saved_second
        old_september, = [row for row in saved_first['content']['draft']['facts'] if row['period'] == '2026-09-30']
        assert old_september['raw_value'] == '0.9'
        initial = {row['period']: row['raw_value'] for row in first['draft_report']['facts']}
        expected = {period: value for period, value in initial.items() if period < '2026-09-01'}
        if case != 'conflict':
            expected['2026-09-29' if case == 'revised_date' else '2026-09-30'] = None if case == 'null' else 8.125
        for done in (second, third):
            assert {row['period']: row['raw_value'] for row in done['draft_report']['facts']} == expected
            assert len(done['draft_report']['facts']) == len(expected)
            records = {row['id']: row for row in done['source_records']}
            assert records[first['source_records'][0]['id']]['snapshot'] == original['snapshot']
            assert done['draft_report'].get('incomplete_economic_series', []) == ([CODE] if case in ('null', 'conflict') else [])
            saved = archive.report(done['id'], done['version'])
            for profile in ('standard', 'detailed'):
                report = design(saved['content'], ROOT, profile=profile)
                schemes = {spec['auto_visual']['scheme'] for spec in report['document']['visual_sources'].values()
                           if spec.get('field') == 'value'}
                assert ('line' in schemes) == (case not in ('null', 'conflict'))
                assert ('缺少2026年9月' in report['html']) == (case in ('null', 'conflict'))
        changed = second['changes']['draft_observations']['rows']
        assert [(row['period'], row['kind']) for row in changed] == [
            ('2026-09', 'missing' if case in ('null', 'conflict') else 'changed')]
    finally:
        agent.close_runtime()


def table(values=None,dates=None,code=CODE,**extra):
    return dict(id=[code],time=dates or ['2026-08-31','2026-09-30'],
        value=[.8,.9] if values is None else values,**extra)


def fixture(*tables,reader=VERSION):
    task=dict(id='economic-scope-fixture',version=1,input_hash='scope',cutoff='2026-10-30T16:00:00+08:00',
        objects=['中国CPI'],source_records=[],materials=[],entry='宏观',intent='综合研究',
        question='比较中国CPI读数',synthetic=True,**{READER_POLICY:reader})
    parameters=dict(indicators=CODE,startdate='2026-01-01',enddate='2026-09-30')
    response=dict(errorcode=0,tables=deepcopy(list(tables) or [table()]))
    record=dict(id='macro-source',kind='api',source='ifind',operation='edb_service',
        task_id=task['id'],research_version=task['version'],status='成功待核验',
        parameters=parameters,snapshot=response,hash=fingerprint(response),
        request_hash=fingerprint(dict(source='ifind',operation='edb_service',parameters=parameters)))
    task['source_records']=[record]
    return task


def rehash(task):
    record=task['source_records'][0];record['hash']=fingerprint(record['snapshot'])
    record['request_hash']=fingerprint({key:record[key] for key in ('source','operation','parameters')})


def test_batched_economic_periods_use_each_bound_question_and_cadence():
    task = fixture(table([1, 2], ['2026-08-31', '2026-09-30']),
                   table([4, 5], ['2026-03-31', '2026-09-30'], OTHER))
    monthly = f'逐月核对{CODE}在2026年1月至9月的读数'
    quarterly = f'逐季核对{OTHER}在2026年第一至第三季度的读数'
    task.update(research_mode='current', question=monthly+'。'+quarterly,
                questions=[dict(id='q1', question=monthly), dict(id='q2', question=quarterly)],
                economic_discovery_version=1, **{DRAFT_PERIOD_POLICY: DRAFT_PERIOD_VERSION})
    source, = task['source_records']
    source['parameters']['indicators'] = CODE+','+OTHER
    source.update(data_need_ids=['batch'], question_ids=['q1','q2'])
    rehash(task)
    supplement = deepcopy(source)
    supplement.update(id='supplement', data_need_ids=['extra'], question_ids=['q2'])
    supplement['parameters'].update(indicators=OTHER, startdate='2026-04-01', enddate='2026-06-30')
    supplement['snapshot']['tables'] = [table([6], ['2026-06-30'], OTHER)]
    supplement['hash'] = fingerprint(supplement['snapshot'])
    supplement['request_hash'] = fingerprint({key:supplement[key] for key in ('source','operation','parameters')})
    request = dict(id='batch', question_ids=['q1','q2'],
                   **{key:source[key] for key in ('source','operation','parameters')})
    assert economic_frequencies(task) == {CODE:'M', OTHER:'Q'}
    coverage = acquisition_coverage(task, [request])
    assert [(row['code'], row['missing_period_count']) for row in coverage['unresolved'][0]['missing']] == [(CODE,7),(OTHER,1)]
    task['source_records'].append(supplement)
    coverage = acquisition_coverage(task, [request])
    assert [row['code'] for row in coverage['unresolved'][0]['missing']] == [CODE]
    supplement['question_ids'] = ['q1']
    coverage = acquisition_coverage(task, [request])
    assert [row['code'] for row in coverage['unresolved'][0]['missing']] == [CODE,OTHER]


@pytest.mark.parametrize('question,frequency,expected_periods', [
    ('逐月核对{}在2026年1月至3月和2026年7月至9月的读数', 'M',
     ['2026年1月','2026年2月','2026年3月','2026年7月','2026年8月','2026年9月']),
    ('逐月核对{}在2025年12月至2026年2月的读数', 'M', ['2025年12月','2026年1月','2026年2月']),
    ('逐月核对{}在2026-08至2026-12的读数', 'M', ['2026年8月','2026年9月']),
    ('逐季核对{}在2026年第一至第三季度的读数', 'Q', ['2026年第1季度','2026年第2季度','2026年第3季度']),
    ('逐年核对{}在2023年至2025年的读数', 'Y', ['2023年','2024年','2025年']),
])
def test_stated_periods_are_disclosed_even_without_a_query(question, frequency, expected_periods):
    task = fixture()
    task['source_records'] = []
    question = question.format(CODE)
    task.update(research_mode='current', question=question, questions=[dict(id='q1', question=question)],
                economic_discovery_version=1, **{DRAFT_PERIOD_POLICY: DRAFT_PERIOD_VERSION})
    coverage = acquisition_coverage(task)
    stated, = coverage['stated_economic_periods']
    assert stated['frequency'] == frequency
    assert stated['periods'] == stated['unqueried_periods'] == expected_periods
    assert stated['missing_period_count'] == len(expected_periods)
    assert coverage['needs_followup'] and coverage['blocks_report'] is False
    task['draft_acquisition_coverage'] = coverage
    report = normalize_report({'title':'宏观读数核对'}, task, [])
    assert report['incomplete_economic_series'] == [CODE]
    assert report['reader_notes'] == [CODE+'缺少'+'、'.join(expected_periods)+'读数。']
    legacy = acquisition_coverage(dict(task, **{DRAFT_PERIOD_POLICY: 2}))
    assert 'stated_economic_periods' not in legacy and not legacy['needs_followup']


def test_stated_period_is_not_covered_by_a_failed_admission_or_a_day_query():
    task = fixture()
    task['source_records'] = []
    question = f'逐月核对{CODE}在2026年1月至2月的读数'
    task.update(research_mode='current', question=question, questions=[dict(id='q1', question=question)],
                economic_discovery_version=1, **{DRAFT_PERIOD_POLICY: DRAFT_PERIOD_VERSION})
    binding = dict(task_id=task['id'], research_version=task['version'], input_hash=task['input_hash'])
    def attempt(ident, status, start, end):
        return dict(binding=binding,status=status,encoded=dict(id=ident,source='ifind',operation='edb_service',
            question_ids=['q1'],parameters=dict(indicators=CODE,startdate=start,enddate=end)))
    task['draft_source_attempts'] = [
        attempt('jan_invalid','参数未通过','2026-01-01','2026-01-31'),
        attempt('feb_day','已提交','2026-02-28','2026-02-28'),
    ]
    stated, = acquisition_coverage(task)['stated_economic_periods']
    assert stated['unqueried_periods'] == ['2026年1月','2026年2月']
    stated, = acquisition_coverage(task, include_economic_query_windows=True)['stated_economic_periods']
    assert stated['unqueried_windows'] == [dict(startdate='2026-01-01', enddate='2026-02-27')]
    task['draft_source_attempts'].append(attempt('feb_first','未取得','2026-02-01','2026-02-27'))
    stated, = acquisition_coverage(task)['stated_economic_periods']
    assert stated['unqueried_periods'] == ['2026年1月']
    assert stated['periods'] == ['2026年1月','2026年2月']
    from researchhelper.draft_sources import stated_window_requests
    request, = stated_window_requests(task)
    assert request['question_ids'] == ['q1']
    assert request['parameters'] == dict(indicators=CODE, startdate='2026-01-01', enddate='2026-01-31')
    task['questions'][0].update(evidence_needs=['核对查询记录'], evidence_need_scopes={'核对查询记录':'query_provenance'})
    assert 'stated_economic_periods' not in acquisition_coverage(task)
    assert not stated_window_requests(task)


class EconomicScopeTests(unittest.TestCase):
    def setUp(self):
        network=patch.object(socket.socket,'connect',side_effect=AssertionError('No network'))
        network.start();self.addCleanup(network.stop)

    def assert_no_model_value(self,task,*values):
        body=canonical(dict(planning=source_context(task),writing=writing_context(task,observations(task))))
        for value in values:self.assertNotIn(str(value),body)

    def test_outside_request_or_cutoff_never_enters_model_context(self):
        task=fixture(table([.8,SENTINEL],['2026-08-31','2026-12-31']))
        before=canonical(task)
        self.assertEqual([r['raw_value'] for r in observations(task)],[.8])
        self.assert_no_model_value(task,SENTINEL,'2026-12-31')
        self.assertTrue(source_issues(task)[0]['partial']);self.assertEqual(canonical(task),before)

    def test_task_cutoff_also_limits_a_retained_request_with_a_later_end(self):
        task=fixture();task['cutoff']='2026-09-01T00:00:00+08:00'
        self.assertEqual([r['period'] for r in observations(task)],['2026-08-31'])

    def test_additional_indicator_is_not_adopted_even_if_numeric(self):
        task=fixture(table(),table([SENTINEL,SENTINEL],code=OTHER))
        self.assertEqual(len(observations(task)),2);self.assert_no_model_value(task,SENTINEL)
        context=writing_context(task,observations(task))
        self.assertNotIn(OTHER,canonical(context['observations']))
        self.assertEqual(context['request_context']['notices'][0]['excluded_fields'],[OTHER])

    def test_unambiguous_requested_indicators_remain_separate(self):
        task=fixture(table(),table([1,2],code=OTHER))
        task['source_records'][0]['parameters']['indicators']=CODE+','+OTHER;rehash(task)
        self.assertEqual({r['code'] for r in observations(task)},{CODE,OTHER})
        self.assertEqual(source_issues(task),[])

    def test_an_ambiguous_or_malformed_indicator_does_not_supply_an_id(self):
        for code in ([CODE,OTHER],{CODE:1},False,[],[[CODE]]):
            bad=table([SENTINEL,SENTINEL]);bad['id']=code
            task=fixture(bad,table())
            with self.subTest(code=code):
                self.assertEqual(len(observations(task)),2);self.assert_no_model_value(task,SENTINEL)

    def test_literal_scalar_indicator_is_read_without_changing_it(self):
        raw=table();raw['id']=CODE;task=fixture(raw);before=canonical(task)
        self.assertEqual(len(observations(task)),2);self.assertEqual(canonical(task),before)

    def test_misaligned_series_does_not_discard_an_independent_good_table(self):
        task=fixture(table([SENTINEL],['2026-08-31','2026-09-30']),table())
        self.assertEqual(len(observations(task)),2);self.assert_no_model_value(task,SENTINEL)

    def test_invalid_dates_are_not_guessed_or_replaced_by_retrieval_time(self):
        task=fixture(table([SENTINEL,.9],['2026-02-30','2026-09-30']))
        self.assertEqual([r['period'] for r in observations(task)],['2026-09-30'])
        self.assert_no_model_value(task,SENTINEL)

    def test_same_day_duplicates_are_excluded_locally_not_averaged(self):
        task=fixture(table([.1,SENTINEL,.9],['2026-08-31','2026-08-31','2026-09-30']))
        self.assertEqual([r['raw_value'] for r in observations(task)],[.9]);self.assert_no_model_value(task,SENTINEL)

    def test_overlapping_tables_do_not_make_ambiguous_values_look_unique(self):
        task=fixture(table(),table([SENTINEL],['2026-08-31']))
        self.assertEqual([r['period'] for r in observations(task)],['2026-09-30'])
        self.assert_no_model_value(task,SENTINEL)

    def directory(self,task,frequency='M',ident='directory'):
        definition=dict(country='中国',dataSource='合成目录',displayId=CODE,id=CODE[1:],
            freq=frequency,indexName='合成统计序列',node='价格',startDate='20260101',endDate='20260930',unit='%')
        response=dict(text='```json\n'+json.dumps([definition],ensure_ascii=False)+'\n```')
        record=dict(id=ident,source='ifind',operation='search_edb',kind='api',task_id=task['id'],
            research_version=task['version'],status='成功待核验',snapshot=response,parameters=dict(query='合成目录'),hash=fingerprint(response))
        task['source_records'].append(record)

    def test_two_dates_in_the_same_known_statistical_period_are_ambiguous(self):
        task=fixture(table([.1,SENTINEL,.9],['2026-08-01','2026-08-31','2026-09-30']))
        self.directory(task)
        self.assertEqual([r['period'] for r in observations(task)],['2026-09-30'])
        self.assert_no_model_value(task,SENTINEL)

    def test_conflicting_frequency_does_not_supply_a_guessed_statistical_period(self):
        task=fixture(table([.1,.2,.9],['2026-08-01','2026-08-31','2026-09-30']))
        self.directory(task,'M','monthly');self.directory(task,'D','daily')
        self.assertEqual(len(observations(task)),3)

    def test_nonoverlapping_fragments_keep_all_observed_dates(self):
        task=fixture(table([.8],['2026-08-31']),table([.9],['2026-09-30']))
        self.assertEqual([r['raw_value'] for r in observations(task)],[.8,.9])
        self.assertEqual(source_issues(task),[])

    def test_explicit_future_revision_cannot_supply_a_value(self):
        for revision in ('2027-01-01','2027-01-01 09:00:00','2027-01-01T00:00:00Z'):
            task=fixture(table([SENTINEL,.9],rtime=[revision,'2026-10-01 09:00:00']))
            with self.subTest(revision=revision):
                self.assertEqual([r['raw_value'] for r in observations(task)],[.9])
                self.assert_no_model_value(task,SENTINEL,revision)

    def test_unknown_revision_information_does_not_block_current_observations(self):
        task=fixture(table(rtime=['未提供',None]));before=canonical(task)
        self.assertEqual(len(observations(task)),2)
        self.assertTrue(source_issues(task)[0]['partial'])
        self.assertNotIn('rtime',source_context(task)[0]['content'])
        self.assertEqual(canonical(task),before)

    def test_misaligned_optional_names_and_revisions_are_not_zipped_into_values(self):
        task=fixture(table(index_name=['错误的单个名称'],rtime=['2027-01-01 09:00:00']))
        self.assertEqual(len(observations(task)),2)
        self.assertEqual({r['label'] for r in observations(task)},{CODE})
        self.assert_no_model_value(task,'错误的单个名称','2027-01-01')

    def test_zero_null_and_literal_decimal_precision_survive(self):
        task=fixture(table([0,None,'0.9000'],['2026-07-31','2026-08-31','2026-09-30']))
        self.assertEqual([r['raw_value'] for r in observations(task)],[0,None,'0.9000'])
        self.assertEqual(source_issues(task),[])

    def test_malformed_numeric_cells_are_not_business_text_for_the_model(self):
        task=fixture(table([{'value':SENTINEL},.9]));self.assertEqual(len(observations(task)),1)
        self.assert_no_model_value(task,SENTINEL)

    def test_saved_reader_ten_keeps_its_original_reading(self):
        task=fixture(table([.8,SENTINEL],['2026-08-31','2026-12-31']),reader=10)
        self.assertEqual(len(observations(task)),2)

    def test_unknown_reader_cannot_silently_enable_a_future_policy(self):
        task=fixture(reader=VERSION+1)
        with self.assertRaises(ContractError):observations(task)

    def test_old_observation_objects_cannot_reintroduce_an_excluded_cell_into_writing(self):
        task=fixture(table([.8,SENTINEL],['2026-08-31','2026-12-31']),reader=10)
        stale=observations(task);task[READER_POLICY]=VERSION
        self.assertNotIn(str(SENTINEL),canonical(writing_context(task,stale)))

    def test_retained_actual_cpi_values_and_arithmetic_are_unchanged(self):
        digest=hashlib.sha256(RETAINED.read_bytes()).hexdigest();task=json.loads(RETAINED.read_text())
        before=observations(task);task[READER_POLICY]=VERSION;after=observations(task)
        self.assertEqual([(r['code'],r['period'],r['raw_value']) for r in after],[(r['code'],r['period'],r['raw_value']) for r in before])
        self.assertEqual([r['display'] for r in analyze(task,after)['results']],['0.70','0.10'])
        self.assertEqual(hashlib.sha256(RETAINED.read_bytes()).hexdigest(),digest)

    def test_retained_cpi_levels_and_change_link_to_different_basis(self):
        from lxml import html
        from researchhelper.drafts import compose, source_binding
        from researchhelper.report_designer import render_html

        raw = RETAINED.read_bytes()
        task = json.loads(raw)
        task.update(draft_client_copy_version=4, draft_source_reader_version=VERSION)
        facts = observations(task)
        task['draft_analysis'] = analyze(task, facts)
        levels = '2026年1月至9月中国CPI同比读数由0.2%升至0.9%。'
        delta = '2026年1月至9月中国CPI同比读数上升0.70个百分点。'
        forecast = '预计2026年9月中国CPI同比读数为0.9%。'
        proposal = dict(title='CPI同比观察', conclusion=levels+delta+forecast,
            logics=[dict(title='统计期读数', analysis='2026年9月中国CPI同比读数为0.9%。'),
                    dict(title='另一地区', analysis='2026年9月美国CPI同比读数为0.9%。')])
        draft = normalize_report(proposal, task, facts)
        self.assertEqual(draft['conclusion'], proposal['conclusion'])
        self.assertEqual(draft['logics'][0]['data_refs'], ['D1'])
        self.assertNotIn('quantity_links', draft['logics'][1])
        task['draft_report'] = dict(draft, facts=facts, calculations=task['draft_analysis']['results'],
                                    source_binding=source_binding(task))
        content = compose(task)
        for profile in ('standard', 'detailed'):
            tree = html.document_fromstring(render_html(content, ROOT, profile=profile))
            summary = tree.get_element_by_id('summary')
            links = summary.xpath('.//a[@class="quantity-link"]')
            self.assertEqual([link.text_content() for link in links], ['0.2%', '0.9%', '0.70个百分点'])
            for link, refs, day in zip(links, (['D9'], ['D1'], ['D9','D1']), ('2026-01-31', '2026-09-30', '2026-09-30')):
                detail = tree.get_element_by_id(link.get('href')[1:])
                self.assertEqual(detail.xpath('.//tr/@data-observation-id'), refs)
                self.assertIn(day, detail.text_content())
                self.assertEqual(bool(detail.xpath('.//*[local-name()="math"]')), len(refs)==2)
        self.assertEqual(RETAINED.read_bytes(), raw)

    def test_failed_writer_uses_economic_endpoints_without_inventing_a_change_for_missing_end(self):
        periods = ['2026-01-31', '2026-02-28', '2026-03-31']
        for values, expected, refs in (([.2, .7, .9], '0.70个百分点', 2),
                                       ([.2, .7, None], '0.70%', 1)):
            with self.subTest(values=values):
                task = fixture(table(values, periods))
                self.directory(task)
                task['draft_analysis'] = analyze(task, facts := observations(task))
                draft = normalize_report({}, task, facts)
                link, *other = draft['quantity_links']['conclusion']['links']
                self.assertIn(expected, draft['conclusion'])
                self.assertTrue(expected.startswith(link['original']))
                self.assertEqual(len(link['data_refs']), refs)
                self.assertEqual([item['original'] for item in other], ['0.20个百分点'] if refs == 2 else [])
                self.assertEqual('2026-03-31' in draft['conclusion'], refs == 2)
                self.assertNotIn('原因', draft['conclusion'])

    def test_retained_cpi_without_a_model_shows_both_windows_and_links_each_result(self):
        from lxml import html
        from researchhelper.drafts import compose, source_binding
        from researchhelper.report_designer import render_html

        path = ROOT/'result/acceptance/one-sentence-research-lfu94vrd/task.json'
        raw = path.read_bytes()
        task = json.loads(raw)
        for field in ('draft_report', 'draft_composition_binding', 'draft_submissions'):
            task.pop(field, None)
        task['delivery'] = None
        facts = observations(task)
        results = analyze(task, facts)
        task['draft_analysis'] = results
        draft = normalize_report({}, task, facts, retained_data_only=True)
        self.assertEqual(draft['title'], '中国CPI当月同比观察')
        links = draft['quantity_links']['conclusion']['links']
        self.assertEqual(len(links), 6)
        by_id = {item['id']: item for item in facts}
        expected = (('2026-01-31', '2026-09-30', ['0.70', '1.00', '0.70']),
                    ('2026-08-31', '2026-09-30', ['0.10', '0.30', '0.20']))
        for index, (first, last, numbers) in enumerate(expected):
            self.assertIn(f'{first}至{last}', draft['conclusion'])
            section = links[3*index:3*index+3]
            self.assertEqual([link['original'] for link in section], [v+'个百分点' for v in numbers])
            for link in section:
                self.assertEqual([by_id[ref]['period'] for ref in link['data_refs']], [first, last])
        task['draft_report'] = dict(draft, facts=facts, calculations=results['results'], source_binding=source_binding(task))
        content = compose(task)
        for profile in ('standard','detailed'):
            with self.subTest(profile=profile):
                page = html.document_fromstring(render_html(content, ROOT, profile=profile))
                summary = page.get_element_by_id('summary')
                anchors = summary.xpath('.//a[@class="quantity-link"]')
                self.assertEqual(len(anchors), 6)
                for anchor, link in zip(anchors, links):
                    detail = page.get_element_by_id(anchor.get('href')[1:])
                    self.assertEqual(detail.xpath('.//tr/@data-observation-id'), link['data_refs'])
        self.assertEqual(path.read_bytes(), raw)

    def test_explicit_macro_comparison_never_substitutes_adjacent_periods(self):
        from lxml import html
        from researchhelper.drafts import compose, source_binding
        from researchhelper.report_designer import render_html

        days = ['2026-01-31', '2026-02-28', '2026-03-31']
        question = f'逐月核对{CODE}在2026年1月至3月的读数，比较首末变化。'
        cases = [([0, .7, .9], None, '0.90个百分点', 2),
                 ([.2, None, .9], '2', '0.70个百分点', 2),
                 ([None, .7, .9], '1', '0.90%', 1),
                 ([.2, .7, None], '3', '0.70%', 1)]
        for values, month, expected, references in cases:
            for omit_empty in (False, True):
                with self.subTest(values=values, omit_empty=omit_empty):
                    cells = [(day, value) for day, value in zip(days, values) if not omit_empty or value is not None]
                    dates, readings = map(list, zip(*cells))
                    task = fixture(table(readings, dates))
                    task.update(question=question, questions=[dict(id='q1', question=question)], research_mode='current',
                                economic_discovery_version=1, evidence=[], calculations=[], **{DRAFT_PERIOD_POLICY: DRAFT_PERIOD_VERSION})
                    task['source_records'][0]['question_ids'] = ['q1']
                    self.directory(task)
                    facts = observations(task)
                    task['draft_acquisition_coverage'] = acquisition_coverage(task, facts=facts)
                    task['draft_analysis'] = analyze(task, facts)
                    results = task['draft_analysis']['results']
                    self.assertEqual([row['period'] for row in results], [days[0]+'至'+days[-1]] if references == 2 else [])
                    draft = normalize_report({}, task, facts)
                    link, = draft['quantity_links']['conclusion']['links']
                    self.assertIn(expected, draft['conclusion'])
                    self.assertEqual(len(link['data_refs']), references)
                    if month:
                        gap, = draft['readout_gaps']
                        self.assertIn(f'缺少2026年{month}月读数', gap)
                    else:
                        self.assertFalse(draft.get('readout_gaps'))
                    task['draft_report'] = dict(draft, facts=facts, calculations=results, source_binding=source_binding(task))
                    content = compose(task)
                    for profile in ('standard', 'detailed'):
                        tree = html.document_fromstring(render_html(content, ROOT, profile=profile))
                        anchor, = tree.get_element_by_id('summary').xpath('.//a[@class="quantity-link"]')
                        detail = tree.get_element_by_id(anchor.get('href')[1:])
                        self.assertEqual(len(detail.xpath('.//tr[@data-observation-id]')), references)
                        self.assertEqual(len(detail.xpath('.//*[local-name()="math"]')), references-1)

    def test_calendar_month_and_market_date_keep_distinct_scopes(self):
        from lxml import html
        from researchhelper.draft_sources import stated_window_requests
        from researchhelper.drafts import compose, source_binding
        from researchhelper.report_designer import render_html

        for september in (.9, None):
            with self.subTest(september=september):
                task = fixture(table([7.31, september], ['2026-08-31', '2026-09-30']))
                question = (f'核对中国CPI当月同比{CODE}在2026年9月的读数，'
                            '另核对600519.SH在2026-10-27的收盘价。')
                task.update(question=question, questions=[dict(id='q1', question=question)], research_mode='current',
                            economic_discovery_version=1, evidence=[], calculations=[], **{DRAFT_PERIOD_POLICY: DRAFT_PERIOD_VERSION})
                record, = task['source_records']
                record.update(question_ids=['q1'], data_need_ids=['macro'])
                self.directory(task)
                before = deepcopy(record)
                facts = observations(task)
                stated, = acquisition_coverage(task, facts=facts)['stated_economic_periods']
                window = dict(startdate='2026-09-01', enddate='2026-09-30')
                self.assertEqual(stated['windows'], [window])
                self.assertEqual(stated['periods'], ['2026年9月'] if september is None else [])
                self.assertEqual([row['period'] for row in facts], ['2026-09-30'])
                self.assert_no_model_value(task, 7.31)
                task['draft_acquisition_coverage'] = acquisition_coverage(task, facts=facts)
                task['draft_analysis'] = analyze(task, facts)
                self.assertEqual(task['draft_analysis']['results'], [])
                draft = normalize_report({}, task, facts)
                task['draft_report'] = dict(draft, facts=facts, calculations=[], source_binding=source_binding(task))
                content = compose(task)
                for profile in ('standard', 'detailed'):
                    tree = html.document_fromstring(render_html(content, ROOT, profile=profile))
                    main = tree.find('.//main')
                    summary = tree.get_element_by_id('summary')
                    links = summary.xpath('.//a[@class="quantity-link"]')
                    if september is None:
                        self.assertFalse(links)
                        self.assertIn('缺少2026年9月读数', main.text_content())
                    else:
                        link, = links
                        self.assertIn('2026年9月', summary.text_content())
                        self.assertEqual(link.text_content(), '0.90')
                        panel = tree.get_element_by_id(link.get('href')[1:])
                        self.assertIn('2026-09-30', panel.text_content())
                    self.assertNotIn('7.31', main.text_content())
                self.assertEqual(record, before)
                task['source_records'].remove(record)
                request, = [r for r in stated_window_requests(task) if r['operation'] == 'edb_service']
                self.assertEqual(request['parameters'], dict(window, indicators=CODE))
                legacy = dict(task, **{DRAFT_PERIOD_POLICY:5})
                self.assertNotIn('stated_economic_periods', acquisition_coverage(legacy))

    def test_listed_months_get_distinct_linked_readings_without_an_implicit_comparison(self):
        from lxml import html
        from researchhelper.drafts import compose, source_binding
        from researchhelper.report_designer import design

        task = fixture(table([.8, .9, 1.1], ['2026-07-31', '2026-08-31', '2026-09-30']))
        question = f'核对2026年8月和9月的中国CPI当月同比{CODE}。'
        task.update(question=question, questions=[dict(id='q1', question=question)], research_mode='current',
                    economic_discovery_version=1, evidence=[], calculations=[], **{DRAFT_PERIOD_POLICY: DRAFT_PERIOD_VERSION})
        task['source_records'][0]['question_ids'] = ['q1']
        self.directory(task)
        facts = observations(task)
        self.assertEqual(sorted(row['period'] for row in facts), ['2026-08-31', '2026-09-30'])
        task['draft_acquisition_coverage'] = acquisition_coverage(task, facts=facts)
        task['draft_analysis'] = analyze(task, facts)
        self.assertEqual(task['draft_analysis']['results'], [])
        report = normalize_report({}, task, facts)
        self.assertIn('2026年8月，合成统计序列为0.90%', report['conclusion'])
        self.assertIn('2026年9月，合成统计序列为1.10%', report['conclusion'])
        self.assertEqual(len(report['quantity_links']['conclusion']['links']), 2)
        task['draft_report'] = dict(report, facts=facts, calculations=[], source_binding=source_binding(task))
        content = compose(task)
        for profile in ('standard', 'detailed'):
            with self.subTest(profile=profile):
                output = design(content, ROOT, profile=profile)
                self.assertFalse(any(block['kind'] == 'chart' for block in output['document']['blocks']))
                tree = html.document_fromstring(output['html'])
                links = tree.get_element_by_id('summary').xpath('.//a[@class="quantity-link"]')
                self.assertEqual([link.getparent().text_content() for link in links], ['0.90%', '1.10%'])
                for link, day in zip(links, ('2026-08-31', '2026-09-30'), strict=True):
                    target = tree.get_element_by_id(link.get('href')[1:])
                    self.assertEqual(len(target.xpath('.//tr[@data-observation-id]')), 1)
                    self.assertIn(day, target.text_content())
                self.assertEqual(sorted(row[2] for row in output['document']['dataset']['rows']),
                                 ['2026-08-31', '2026-09-30'])

    def test_named_period_keeps_a_gap_without_guessing_the_indicator(self):
        from researchhelper.draft_sources import stated_window_requests

        task = fixture()
        question = '核对2026年9月中国CPI当月同比。'
        task.update(question=question, questions=[dict(id='q1', question=question)], research_mode='current',
                    economic_discovery_version=1, source_records=[], evidence=[], calculations=[],
                    **{DRAFT_PERIOD_POLICY: DRAFT_PERIOD_VERSION})
        task['draft_acquisition_coverage'] = acquisition_coverage(task, facts=[])
        self.assertFalse(stated_window_requests(task))
        report = normalize_report({}, task, [])
        self.assertIn('中国CPI同比缺少2026年9月读数，指标口径待核对。', report['reader_notes'])

    def test_single_statistical_period_is_distinct_from_an_open_window(self):
        from researchhelper.economic_sources import _stated_period_request

        closed = {
            '2026-09': ('M', '2026-09-01', '2026-09-30'),
            '2026年第3季度': ('Q', '2026-07-01', '2026-09-30'),
            '2025年': ('Y', '2025-01-01', '2025-12-31'),
        }
        for text, (cadence, first, last) in closed.items():
            with self.subTest(text=text):
                frequency, windows = _stated_period_request(text, explicit_points=True)
                self.assertEqual(frequency, cadence)
                self.assertEqual([(a.isoformat(), b.isoformat()) for a, b in windows], [(first,last)])
        frequency, scopes = _stated_period_request(
            f'逐月核对{CODE}的2026年9月份，另核对600519.SH在2026-10-27至30日的收盘价。',
            explicit_points=True)
        self.assertEqual(frequency, 'M')
        self.assertEqual([(a.isoformat(), b.isoformat()) for a, b in scopes], [('2026-09-01','2026-09-30')])
        for text in ('2026年9月以来', '截至2026年9月', '2026年9月份以来', '2026-09-30'):
            with self.subTest(text=text):
                self.assertIsNone(_stated_period_request(text, explicit_points=True))

    def test_separate_requested_periods_reach_the_summary_and_chart_without_extra_months(self):
        from lxml import html
        from researchhelper.drafts import compose, source_binding
        from researchhelper.draft_visuals import planned_figures
        from researchhelper.report_designer import render_html

        dates = ['2026-01-31', '2026-02-28', '2026-03-31',
                 '2026-04-30', '2026-05-31', '2026-06-30']
        question = f'逐月核对{CODE}在2026年1月至3月及2026年5月至6月的读数，分别比较区间首末变化。'
        for march in (.9, None):
            with self.subTest(march=march):
                task = fixture(table([.2, .7, march, 5.61, 1.2, 1.0], dates))
                task.update(question=question, questions=[dict(id='q1', question=question)],
                            research_mode='current', economic_discovery_version=1, evidence=[],
                            calculations=[], **{DRAFT_PERIOD_POLICY: DRAFT_PERIOD_VERSION})
                source, = task['source_records']
                source.update(question_ids=['q1'], data_need_ids=['macro'])
                self.directory(task)
                request = dict(id='macro', question_ids=['q1'],
                               **{key:source[key] for key in ('source','operation','parameters')})
                request['parameters'] = dict(request['parameters'], enddate='2026-06-30')
                facts = observations(task)
                self.assertEqual([row['period'] for row in facts], dates[:3]+dates[4:])
                self.assertEqual(source['snapshot']['tables'][0]['time'], dates)
                packet = next(row for row in source_context(task) if row['id']==source['id'])
                self.assertEqual(json.loads(packet['content'])['tables'][0]['time'], dates[:3]+dates[4:])
                coverage = task['draft_acquisition_coverage'] = acquisition_coverage(task, requests=[request], facts=facts)
                period, = coverage['stated_economic_periods']
                self.assertEqual(period['expected_period_count'], 5)
                self.assertEqual(period['periods'], [] if march is not None else ['2026年3月'])
                self.assertNotIn('2026年4月', json.dumps(coverage['unresolved'],ensure_ascii=False))
                task['draft_analysis'] = analyze(task, facts)
                results = task['draft_analysis']['results']
                draft = normalize_report({}, task, facts)
                task['draft_report'] = dict(draft, facts=facts, calculations=results, source_binding=source_binding(task))
                content = compose(task)
                wanted = ([('0.70个百分点', dates[0], dates[2])] if march is not None else [])
                wanted.append(('0.20个百分点', dates[4], dates[5]))
                for profile in ('standard','detailed'):
                    page = html.document_fromstring(render_html(content, ROOT, profile=profile))
                    summary = page.get_element_by_id('summary')
                    anchors = summary.xpath('.//a[@class="quantity-link"]')
                    self.assertEqual([row.text_content() for row in anchors], [value for value, _, _ in wanted])
                    for anchor, (_, first, last) in zip(anchors, wanted, strict=True):
                        detail = page.get_element_by_id(anchor.get('href')[1:])
                        self.assertIn(first, detail.text_content())
                        self.assertIn(last, detail.text_content())
                    self.assertNotIn('5.61', summary.text_content())
                    figures = [spec for spec in planned_figures(content,report_profile=profile)
                               if spec['field']=='value']
                    self.assertTrue(figures)
                    self.assertTrue(all(spec['type']=='bar' and dates[3] not in spec['dates'] for spec in figures))
                if march is None:
                    self.assertIn('缺少2026年3月读数', '\n'.join(draft['reader_notes']))

    def test_equal_macro_values_do_not_select_their_own_series_or_period(self):
        labels = {
            CODE: 'CPI:当月同比', OTHER: '中国:PPI:当月同比', 'M999999998': '美国:CPI:当月同比',
            'M999999997': '中国:CPI:当月环比', 'M999999996': '中国:核心CPI:当月同比',
        }
        dates = ['2026-01-31', '2026-08-31', '2026-09-30']
        task = fixture(*(table([.8, .8, .9], dates, code, index_name=[label]*3)
                         for code, label in labels.items()))
        task.update(draft_client_copy_version=2)
        task['source_records'][0]['parameters']['indicators'] = ','.join(labels)
        rehash(task)
        directory = [dict(country='美国' if '美国' in label else '中国', dataSource='测试统计源', displayId=code,
                          endDate='20260930', freq='M', id=code, indexName=label, node='价格指数',
                          startDate='20260101', unit='%') for code,label in labels.items()]
        snapshot = dict(text='```json\n'+json.dumps(directory,ensure_ascii=False)+'\n```')
        task['source_records'].append(dict(id='directory',kind='api',source='ifind',operation='search_edb',
            task_id=task['id'],research_version=task['version'],status='成功待核验',snapshot=snapshot,hash=fingerprint(snapshot)))
        facts = observations(task)
        self.assertEqual(len(facts), 15)
        task['draft_analysis'] = analyze(task, facts)
        good = '最近两期中国CPI同比读数上升0.10个百分点。'
        wide = '2026年1月至9月中国CPI同比读数上升0.10个百分点。'
        cases = [
            (good, ['D2','D3'], True),
            (wide, ['D1','D3'], True),
            (good, ['D1','D3'], False),
            (good.replace('CPI同比','PPI同比'), ['D2','D3'], False),
            (good.replace('中国','美国'), ['D2','D3'], False),
            ('美国方面，最近两期CPI同比读数上升0.10个百分点。', ['D2','D3'], False),
            (good.replace('同比','环比'), ['D2','D3'], False),
            (good.replace('CPI','核心CPI'), ['D2','D3'], False),
            (good.replace('CPI','年度CPI'), ['D2','D3'], False),
            (good.replace('中国','全国').replace('CPI','居民消费价格指数'), ['D2','D3'], True),
            ('2026年9月中国CPI同比读数为0.90%。', ['D1','D2','D3'], True),
            ('2026年8月中国CPI同比读数为0.90%。', ['D1','D2','D3'], False),
            ('2026年9月CPI读数为0.90%。', ['D3','D6','D9','D12'], False),
            ('2026年9月较2026年8月，中国CPI同比读数上升0.10个百分点。', ['D1','D2','D3'], True),
            ('2026年9月较2026年6月，中国CPI同比读数上升0.10个百分点。', ['D1','D2','D3'], False),
            ('2026年8月至9月中国CPI同比读数由0.80%上升至0.90%。', ['D1','D2','D3'], True),
            ('2026年8月中国CPI同比为0.80%，9月为0.90%。', ['D2','D3'], True),
            ('中国CPI同比读数由0.80%升至0.90%。', ['D2','D3'], True),
            ('2026年8月至9月中国CPI同比读数由0.90%降至0.80%。', ['D1','D2','D3'], False),
            ('2026年8月至9月中国CPI同比读数由0.80%降至0.90%。', ['D1','D2','D3'], False),
            ('2026年8月至9月中国CPI同比读数由0.80%升至0.90%。', ['D2','D9'], False),
        ]
        report = normalize_report(dict(title='宏观读数',conclusion='按指标及日期核对。',logics=[
            dict(title='读数观察',analysis=claim,data_refs=refs) for claim,refs,_ in cases]), task, facts)
        for (claim,refs,expected),logic in zip(cases,report['logics']):
            with self.subTest(claim=claim,refs=refs):
                self.assertEqual(logic['analysis'] == claim, expected)
                self.assertEqual(logic['data_refs'],refs)
                if expected:
                    self.assertNotIn('D3',logic.get('off_scope_data_refs',[]))
                    self.assertIn('D6',logic.get('off_scope_data_refs',[]))
                else:
                    self.assertEqual(set(logic['off_scope_data_refs']), {row['id'] for row in facts})
                    self.assertNotIn('quantity_links',logic)

    def test_statistical_frequency_is_distinct_from_a_period_end_observation(self):
        monthly, quarterly, annual, cumulative = CODE, OTHER, 'M999999998', 'M999999997'
        specs = [
            (monthly, '中国:CPI:当月同比', 'M', ['2026-08-31','2026-09-30'], [.8,.9]),
            (quarterly, '中国:CPI:季度同比', 'Q', ['2026-04-01','2026-07-01'], [.8,.9]),
            (annual, '中国:CPI:年度同比', 'Y', ['2026-01-01'], [.9]),
            (cumulative, '中国:CPI:累计同比', 'M', ['2026-09-30'], [.9]),
        ]
        task = fixture(*(table(values, dates, code, index_name=[label]*len(dates))
                         for code,label,_,dates,values in specs))
        task['source_records'][0]['parameters']['indicators'] = ','.join(item[0] for item in specs)
        rehash(task)
        directory = [dict(country='中国', dataSource='测试统计源', displayId=code, endDate='20260930', freq=frequency,
                          id=code, indexName=label, node='价格指数', startDate='20260101', unit='%')
                     for code,label,frequency,_,_ in specs]
        snapshot = dict(text='```json\n'+json.dumps(directory,ensure_ascii=False)+'\n```')
        task['source_records'].append(dict(id='directory', kind='api', source='ifind', operation='search_edb',
            task_id=task['id'], research_version=task['version'], status='成功待核验', snapshot=snapshot, hash=fingerprint(snapshot)))
        task.update(draft_client_copy_version=2)
        facts = observations(task)
        self.assertEqual(len(facts), 6)
        task['draft_analysis'] = analyze(task,facts)
        cases = [
            ('2026年第三季度中国CPI同比读数为0.90%。', ['D1','D2'], False),
            ('2026年第三季度中国CPI同比读数为0.90%。', ['D3','D4'], True),
            ('2026年第三季度末中国CPI同比读数为0.90%。', ['D1','D2'], True),
            ('2026年9月中国CPI同比读数为0.90%。', ['D3','D4'], False),
            ('2026年9月中国CPI同比读数为0.90%。', ['D1','D2'], True),
            ('2026年9月30日中国CPI同比读数为0.90%。', ['D1','D2'], True),
            ('2026年中国CPI同比读数为0.90%。', ['D1','D2','D3','D4'], False),
            ('2026年中国CPI同比读数为0.90%。', ['D5'], True),
            ('2026年前三季度中国CPI累计同比读数为0.90%。', ['D1','D2'], False),
            ('2026年前三季度中国CPI累计同比读数为0.90%。', ['D6'], True),
            ('2026年第三季度较第二季度中国CPI同比读数上升0.10个百分点。', ['D3','D4'], True),
            ('2026年第三季度较第二季度中国CPI同比读数上升0.10个百分点。', ['D1','D2'], False),
            ('2026年9月中国CPI同比读数为0.90%，第三季度为0.90%。', ['D1','D2'], False),
        ]
        report = normalize_report(dict(title='CPI期间核对', conclusion='按实际统计期核对。', logics=[
            dict(title='季度末读数' if '季度末' in claim else '观察', analysis=claim, data_refs=refs)
            for claim,refs,_ in cases]),task,facts)
        for (claim,refs,expected), logic in zip(cases,report['logics']):
            with self.subTest(claim=claim, refs=refs):
                self.assertEqual(logic['analysis'] == claim, expected)
                if expected:
                    self.assertFalse(set(refs) <= set(logic.get('off_scope_data_refs', [])))
                elif '，' not in claim:
                    self.assertEqual(set(logic['off_scope_data_refs']), {row['id'] for row in facts})
                    self.assertNotIn('quantity_links', logic)
        self.assertTrue(report['logics'][-1]['analysis'].startswith('2026年9月中国CPI同比读数为0.90%'))


    def test_qualitative_attribution_preserves_independent_readings_and_conditions(self):
        task = json.loads(RETAINED.read_text())
        task.update(draft_client_copy_version=3)
        task[READER_POLICY] = VERSION
        facts = observations(task)
        task['draft_analysis'] = analyze(task, facts)
        reading = '2026年9月中国CPI同比读数为0.90%'
        unsupported = '猪肉涨价直接导致中国CPI回升'
        qualified = '猪肉涨价可能推动CPI回升，仍需核对权重。'
        texts = [reading + '，上行完全由猪肉涨价导致。', '由于猪肉价格上涨，' + reading + '。',
                 unsupported + '。', qualified, '若猪肉涨价，猪肉涨价直接推动CPI回升。',
                 '数据不足以证明猪肉涨价导致CPI回升。',
                 unsupported + '但仍需跟踪后续数据。']
        refs = [row['id'] for row in facts]
        proposal = dict(title='中国CPI读数', conclusion=texts[0], logics=[
            dict(title='宏观观察', analysis=text, data_refs=refs) for text in texts])
        original = deepcopy(proposal)
        report = normalize_report(proposal, task, facts)
        logics = report['logics']
        self.assertIn(reading, report['conclusion'])
        self.assertNotIn('完全由', report['conclusion'])
        for logic in logics[:2]:
            self.assertIn(reading, logic['analysis'])
            self.assertIn('原因仍需验证', logic['analysis'])
            self.assertNotIn('猪肉', logic['analysis'])
            self.assertFalse(logic.get('off_scope_data_refs'))
        for logic in (logics[2], logics[-1]):
            self.assertEqual(set(logic['off_scope_data_refs']), set(refs))
            self.assertNotIn(unsupported, logic['analysis'])
        for logic, original_text in zip(logics[3:6], texts[3:6]):
            self.assertEqual(logic['analysis'], original_text)
        self.assertTrue(any(edit['original'] == unsupported and
                            edit['reason'] == 'draft_causal_attribution_unsubstantiated'
                            for edit in report['copy_adjustments']))
        self.assertEqual(proposal, original)
        task['draft_client_copy_version'] = 2
        self.assertEqual(normalize_report(proposal, task, facts)['logics'][2]['analysis'], texts[2])

    def test_cited_exact_quotation_and_event_chronology_are_preserved(self):
        from researchhelper.evidence import extract_material, material_record
        task = fixture()
        task.update(entry='事件', objects=['合成指数'], question='核对公告前后市场表现',
                    draft_client_copy_version=3)
        task['source_records'] = []
        quote = '该政策推动市场表现改善'
        raw = ('合成说明。' + quote + '。').encode()
        record = material_record(task, extract_material('合成说明.txt', raw), raw)
        task['materials'] = [record]
        good = '公告原文指出“' + quote + '”。'
        wrong = '公告原文指出“' + quote.replace('改善', '转弱') + '”。'
        chronology = '公告发布后，观察窗口内市场表现改善。'
        causal = '这份公告直接导致市场表现改善。'
        negative = ('合成反证。并非' + quote + '。').encode()
        negative_record = material_record(task, extract_material('合成反证.txt', negative), negative)
        task['materials'].append(negative_record)
        cases = [(good, [record['id']], True), (good, [], False), (wrong, [record['id']], False),
                 (good, [negative_record['id']], False), (chronology, [], True), (causal, [], False)]
        result = normalize_report(dict(title='事件影响', conclusion=chronology, logics=[
            dict(title='公告和市场表现', analysis=text, source_refs=refs)
            for text, refs, _ in cases]), task, [])
        self.assertEqual(result['conclusion'], chronology)
        for logic, (text, _, allowed) in zip(result['logics'], cases):
            with self.subTest(text=text, allowed=allowed):
                self.assertEqual(logic['analysis'] == text, allowed)
        self.assertEqual(task['materials'], [record, negative_record])

    def test_new_draft_reviews_industry_and_event_attribution_across_text_and_figures(self):
        from lxml import html
        from tempfile import TemporaryDirectory
        from core.session import Session
        from researchhelper.draft_sources import CLIENT_COPY_POLICY, CLIENT_COPY_VERSION

        quote = '该政策推动市场表现改善'
        reported = f'公告原文指出“{quote}”。'
        conditional = '若交易费用下降，可能推动成交增长，仍需核对成交数据。'
        chronology = '新规发布后，观察窗口内券商股价上涨。'
        conditional_node = '若费率下调，可能推动成交增长'
        cases = [
            ('混合','研究新规发布后对券商行业的影响。','新规落地直接导致券商股价上涨。'),
            ('事件','核对新规发布后券商的市场表现。','新规是券商股价上涨的主要原因。'),
            ('行业','研究券商行业经营与风险。','手续费调整直接导致券商行业营业收入增长。'),
        ]
        with TemporaryDirectory() as folder, patch.object(socket.socket,'connect',side_effect=AssertionError('No network')):
            session = Session(folder)
            try:
                for entry, question, attribution in cases:
                    with self.subTest(entry=entry):
                        task = session.create(question, entry=entry, research_mode='current')
                        ident = task['id']
                        material = session.material(ident,'合成原文.txt',('仅用于离线验收。\n\n'+quote+'。\n\n未引用附页。').encode())
                        proposal = dict(title='市场和经营影响观察', conclusion=chronology+attribution,
                            logics=[dict(title='传导条件', analysis=attribution, implication=conditional,
                                mechanism=dict(steps=[dict(label=conditional_node, kind='hypothesis'),
                                                      dict(label=attribution, kind='hypothesis')])),
                                dict(title='原文判断', analysis=reported, source_refs=[material['id']]),
                                dict(title='观察边界', analysis='不能说新规是券商股价上涨的唯一原因。')])
                        saved = session.draft(ident, report=proposal)
                        self.assertTrue(saved.get('delivery'), saved.get('error'))
                        self.assertEqual(saved[CLIENT_COPY_POLICY], CLIENT_COPY_VERSION)
                        self.assertEqual(saved['ledger']['used']['model_calls'], 0)
                        self.assertEqual(saved['ledger']['used']['data_requests'], 0)
                        draft = saved['draft_report']
                        self.assertIn(chronology, draft['conclusion'])
                        self.assertNotIn(attribution, draft['conclusion'])
                        self.assertEqual(draft['logics'][0]['implication'], conditional)
                        self.assertEqual(draft['logics'][0]['mechanism']['steps'][0]['label'], conditional_node)
                        self.assertEqual(draft['logics'][0]['mechanism']['steps'][1]['kind'], 'condition')
                        self.assertEqual(draft['logics'][2]['analysis'], proposal['logics'][2]['analysis'])
                        edits = [edit for edit in draft['copy_adjustments'] if edit['reason']=='draft_causal_attribution_unsubstantiated']
                        self.assertIn('logics.0.mechanism.steps.1.label', {edit['path'] for edit in edits})
                        self.assertTrue(all(not edit['financial_approval'] for edit in edits))
                        for profile in ('standard','detailed'):
                            paper = html.document_fromstring(session.export(ident, profile=profile).read_bytes())
                            self.assertNotIn(attribution, paper.find('.//main').text_content())
                            self.assertNotIn('未引用附页', paper.text_content())
                            section = paper.get_element_by_id('logic-2')
                            link, = section.xpath('.//a[@class="quantity-link"]')
                            self.assertEqual(link.text_content(), quote)
                            detail = paper.get_element_by_id(link.get('href')[1:]).text_content()
                            self.assertIn(quote, detail)
                            self.assertIn('段落2', detail)
                            document = json.loads(paper.get_element_by_id('rh-publication-document').text)
                            path, = (row for row in document['visual_sources'].values() if row['field']=='mechanism')
                            self.assertNotIn(attribution, json.dumps(path, ensure_ascii=False))
            finally:
                session.agent.close_runtime()


if __name__=='__main__':unittest.main()
