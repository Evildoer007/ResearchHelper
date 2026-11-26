"""Date-sequence arithmetic and figures stay within the original request basis."""
import socket
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests import ROOT
from tests.test_market_calendar_coverage import append_calendar_fixture_record
from tests.test_series_batch import SeriesBatchTests
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import fingerprint
from researchhelper.draft_sources import observations, market_observation_basis, VERSION
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import snapshot_record
from researchhelper.report_designer import design


def market_basis_fixture():
    fixture = SeriesBatchTests()
    fixture.setUp()
    try:
        table = fixture.payload['tables'][0]
        return dict(code=table['thscode'], days=table['time'], columns=table['table'],
                    parameters=dict(fixture.params,codes=table['thscode']))
    finally:
        fixture.tearDown()
        fixture.doCleanups()


def replay_market_basis(agent, submit, fixture, scenario, *, owner_account=None, historical=False):
    from unittest.mock import patch
    from researchhelper.planning import input_fingerprint

    code, days, columns, template = (fixture[key] for key in ('code','days','columns','parameters'))
    nav, shares = tuple(columns)
    field_binding = next(item for item in template['indipara'] if item['indicator']==nav)
    with patch('researchhelper.engine.now',return_value=days[1]+'T22:00:00+08:00'):
        task = agent.create('核对留存的ETF净值和份额。', entry='ETF', objects=[code],
                            research_mode='current', owner_account=owner_account)
    ident = task['id']
    task.update(synthetic=True,draft_source_reader_version=23)
    task['input_hash'] = input_fingerprint(task)
    old_request = dict(template,startdate=days[0],enddate=days[1])
    old_payload = dict(errorcode=0,tables=[dict(thscode=code,time=days[:2],
                       table={nav:columns[nav][:2],shares:columns[shares][:2]})])
    old_record = snapshot_record(task,'ifind','date_sequence',old_request,old_payload)
    if scenario != 'same':
        # Replay a detached old original. These alternate parameters are test
        # data, never requests submitted to the current provider contract.
        old_record.pop('series_contract',None)
        original_binding = next(item for item in old_record['parameters']['indipara'] if item['indicator']==nav)
        original_binding['indiparams'] = ['retained-test-argument'] if scenario=='different' else None
        old_record['request_hash'] = fingerprint({key:old_record[key] for key in ('source','operation','parameters')})
        old_record['id'] = 'rh_source_'+fingerprint(
            [ident,task['version'],old_record['request_hash'],old_record['hash']])[:24]
    task['source_records'].append(old_record)
    agent.archive.save(task)
    first = submit(ident,dict(title='首批原值',conclusion='核对留存净值和份额。'))
    assert first['delivery']
    first_report = agent.archive.report(ident,1)
    assert first_report['content']['draft_source_reader_version']==23

    task = agent.prepare_update(ident, days[-1] if historical else None)
    assert task['draft_source_reader_version']==VERSION
    request = dict(template,startdate=days[2],enddate=days[-1],indipara=[field_binding])
    payload = dict(errorcode=0,tables=[dict(thscode=code,time=days[2:],table={nav:columns[nav][2:]})])
    new_record = append_calendar_fixture_record(task,'date_sequence',request,payload)
    agent.archive.save(task)
    old_basis, new_basis = (market_observation_basis(record,nav) for record in task['source_records'])
    assert (old_basis==new_basis) is (scenario=='same')
    assert (old_basis is None) is (scenario=='missing')
    assert new_basis is not None
    facts = observations(task)
    assert len(facts)==len(days)+2
    assert [(row['period'],row['raw_value']) for row in facts if row['field']==nav]==list(zip(days,columns[nav]))
    by_origin = {record['id']:record for record in task['source_records']}
    for row in facts:
        record = by_origin[row['artifact_id']]
        loc = row['locator']
        table = record['snapshot']['tables'][loc['table']]
        assert table['time'][loc['row']]==row['period']
        assert table['table'][loc['field']][loc['row']]==row['raw_value']
        assert fingerprint(record['snapshot'])==record['hash']
    result = submit(ident,dict(title='净值与份额',conclusion='核对留存窗口的实际原值。',
        logics=[dict(title='净值观测',analysis='展示基金净值和份额原值。',
                     data_refs=[row['id'] for row in facts])]))
    assert result['delivery'] and result['ledger']['used']['model_calls']==0
    assert result['ledger']['used']['data_requests']==1
    assert agent.archive.report(ident,1)==first_report
    saved = agent.archive.report(ident,2)
    counts = saved['content']['changes']['draft_observations']['counts']
    assert counts['retained']==4 and counts['added']==len(days)-2
    assert not any(value for key,value in counts.items() if key not in ('retained','added'))
    return dict(task_id=ident,content=saved['content'],nav=nav,days=days,scenario=scenario,
                origins=[old_record['id'],new_record['id']])


def verify_market_basis_content(case, document, *, baseline=False):
    content, nav, days = (case[key] for key in ('content','nav','days'))
    draft = content['draft']
    facts = draft['facts']
    by_ref = {row['id']:row for row in facts}
    separate = not baseline and case['scenario']!='same'
    # Preserve both provider bases in the dataset. A two-point fragment
    # adds no trend chart and cannot be joined to the later, different basis.
    expected_dates = [days[2:]] if separate else [days]
    charts = sorted([spec for spec in document['visual_sources'].values() if spec.get('field')==nav],
                    key=lambda spec:spec['dates'][0])
    assert [spec['dates'] for spec in charts]==expected_dates
    assert [spec['auto_visual']['scheme'] for spec in charts]==['line']
    for spec in charts:
        assert spec['dates']==[by_ref[ref]['period'] for ref in spec['data_refs']]
        assert spec['values']==[by_ref[ref]['value'] for ref in spec['data_refs']]
        assert len({by_ref[ref]['artifact_id'] for ref in spec['data_refs']})==(1 if separate else 2)
    nav_facts = [row for row in facts if row['field']==nav]
    assert len({row['series_basis'] for row in nav_facts})==(2 if separate else 1)
    results = [item for item in draft['calculations'] if
               all(by_ref[ref]['field']==nav for ref in item['data_refs'])]
    assert len(results)==(4 if separate else 2)
    assert {item['method'] for item in results}=={'raw_change_v1','raw_range_v1'}
    if separate:
        assert all(len({by_ref[ref]['artifact_id'] for ref in item['data_refs']})==1 for item in results)
    else:
        change, = [item for item in results if item['method']=='raw_change_v1']
        assert len({by_ref[ref]['artifact_id'] for ref in change['data_refs']})==2
    assert [row[:4] for row in document['dataset']['rows']]==[
        [row['code'],row['label'],row['period'],row['raw_value']] for row in facts]
    return dict(charts=[dict(dates=spec['dates'],values=spec['values']) for spec in charts],
                calculations=len(results),rows=len(facts),cross_request_calculation=not separate)


def test_date_series_calculations_and_reports_preserve_query_basis():
    fixture = market_basis_fixture()
    with TemporaryDirectory() as folder, patch.object(socket.socket,'connect',side_effect=AssertionError('No network')):
        agent = ResearchAgent(ResearchArchive(folder),ROOT,offline=True,require_argument_support=True,draft_first=True)
        try:
            for scenario, historical in (('same',False),('different',False),('missing',False),('missing',True)):
                case = replay_market_basis(agent,lambda ident,proposal:agent.start(
                    ident,dict(data_requests=[],draft_report=proposal)),fixture,scenario,historical=historical)
                for profile in ('standard','detailed'):
                    document = design(case['content'],ROOT,profile=profile)['document']
                    verify_market_basis_content(case,document)
        finally:
            agent.close_runtime()
