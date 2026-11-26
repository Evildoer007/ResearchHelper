"""A historical first report can continue without an unusable fresh download."""
import json
import socket
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests import ROOT
from tests.test_announcement_partial_delivery import ifind_query, ifind_row
from tests.test_draft_historical_original import (
    BODY, CUTOFF, QUESTION, check_historical_draft, check_historical_html,
    historic_proposal, seed_historical_task,
)
from tests.test_official_release_channel import CATALOG
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.draft_sources import acquisition_coverage, historical_originals_local_only
from researchhelper.draft_workflow import _planning_scope, _source_stamps
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import (
    EvidenceCollector, data_request_contract, filing_record, snapshot_record,
    validate_filing_original_request, validate_source_request,
)


def seed_requests(task):
    material, catalogue = seed_historical_task(task)
    quote = next(row for row in task['source_records'] if row['operation'] == 'cmd_history_quotation')
    requests = [
        dict(id='original', source='ifind', operation='filing_original', question_ids=['q1'],
             parameters=dict(catalog_id=catalogue['id'], source_id='selected'),
             selection_reason='核对本次研究时点的所选公告。'),
        dict(id='price', source='ifind', operation=quote['operation'], question_ids=['q1'],
             parameters=quote['parameters']),
    ]
    return material, catalogue, quote, requests


def check_historical_acquisition(task, material, catalogue, quote):
    attempts = {row['encoded']['id']: row for row in task['draft_source_attempts']}
    assert attempts['original']['status'] == '时点不适用'
    assert '原件请求未派发' in attempts['original']['error']
    assert attempts['price']['status'] == '已提交'
    assert not any(row.get('operation') in ('filing_original', 'release_original')
                   for row in task.get('source_attempts', {}).values())
    assert 'price' in acquisition_coverage(task)['complete_request_ids']
    stored = next(row for row in task['source_records'] if row['id'] == quote['id'])
    assert stored['snapshot'] == quote['snapshot'] and 'price' in stored['data_need_ids']
    assert task['ledger']['used']['data_requests'] == task['ledger']['used']['model_calls'] == 0
    assert task['delivery']
    check_historical_draft(task, material, catalogue)
    return dict(original_skipped=True, quote_reused=True, report_saved=True, profiles=['standard', 'detailed'])


def original_tools(contract):
    return {tool['operation'] for tool in contract['tools']
            if tool['operation'] in ('filing_original', 'release_original')}


@pytest.fixture
def scenario(tmp_path):
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('No network')):
        archive = ResearchArchive(tmp_path)
        agent = ResearchAgent(archive, ROOT, offline=True, draft_first=True)
        try:
            task = agent.create(QUESTION, entry='事件', objects=['600519.SH'],
                                cutoff=CUTOFF, research_mode='historical')
            yield agent, archive, task
        finally:
            agent.close_runtime()


def test_first_report_tools_match_the_actual_clock_and_research_mode(scenario):
    _, _, task = scenario
    all_tools = {'filing_original', 'release_original'}
    assert original_tools(data_request_contract(task)) == all_tools
    with patch('researchhelper.draft_sources.now', return_value='2026-10-17T05:59:59Z'):
        assert not historical_originals_local_only(task)
        assert original_tools(data_request_contract(task, first_report=True)) == all_tools
    with patch('researchhelper.draft_sources.now', return_value='2026-10-17T06:00:00Z'):
        contract = data_request_contract(task, first_report=True)
        assert not original_tools(contract)
        assert {'cmd_history_quotation','date_sequence','report_query','release_catalog'} <= {
            item['operation'] for item in contract['tools']}
        assert original_tools(data_request_contract(dict(task, research_mode='current'), first_report=True)) == all_tools


def test_first_report_keeps_valid_quotation_and_skips_stale_original_plan(scenario):
    agent, archive, task = scenario
    material, catalogue, quote, requests = seed_requests(task)
    validate_source_request(task, 'ifind', 'filing_original', requests[0]['parameters'])
    archive.save(task)
    with patch.object(agent.collector, 'external', side_effect=AssertionError('Unexpected dispatch')):
        saved = agent.start(task['id'], dict(data_requests=requests, draft_report=historic_proposal(material,catalogue)))
    check_historical_acquisition(saved, material, catalogue, quote)
    check_historical_html(archive.report(task['id'], task['version'])['html'])


def test_official_html_original_is_subject_to_same_dispatch_boundary(scenario):
    agent, archive, _ = scenario
    task = agent.create('核对中国采购经理指数发布原文', entry='宏观', objects=['中国采购经理指数'],
                        cutoff=CUTOFF, research_mode='historical')
    collector = EvidenceCollector(offline=False)
    with patch('researchhelper.source_transport.official_html', return_value=CATALOG):
        catalogue = collector.external(task, 'official', 'release_catalog', {'channel':'nbs_release','page':'0'}, {})
    task['source_records'].append(catalogue)
    tool = next(row for row in data_request_contract(task)['tools'] if row['operation']=='release_original')
    choice = tool['selection_details'][0]['parameters']
    used = task['ledger']['used']['data_requests']
    validate_source_request(task, 'official', 'release_original', choice)
    archive.save(task)
    request = dict(id='official_original', source='official', operation='release_original',
                   parameters=choice, question_ids=['q1'], selection_reason='核对所选官方原件。')
    with patch.object(agent.collector, 'external', side_effect=AssertionError('Unexpected dispatch')):
        saved = agent.start(task['id'], dict(data_requests=[request], draft_report=None))
    assert saved['delivery']
    assert saved['draft_source_attempts'][0]['status']=='时点不适用'
    assert saved['ledger']['used']['data_requests']==used


def test_retained_original_can_still_add_a_question_binding_without_download(scenario):
    agent, archive, task = scenario
    _, _, _, requests = seed_requests(task)
    request = requests[0]
    directory = validate_filing_original_request(task, request['parameters'])
    record = filing_record(task, directory, '后获公告.txt', BODY.encode())
    record.update(operation=request['operation'], parameters=request['parameters'], status='成功待核验',
                  request_hash=fingerprint({key:request[key] for key in ('source','operation','parameters')}))
    task['source_records'].append(record)
    archive.save(task)
    with patch.object(agent.collector, 'external', side_effect=AssertionError('Unexpected dispatch')):
        saved = agent.start(task['id'], dict(data_requests=requests, draft_report=None))
    retained = next(row for row in saved['source_records'] if row['id']==record['id'])
    assert retained['hash'] == record['hash'] and retained['question_ids'] == ['q1']
    assert 'original' in retained['data_need_ids']
    assert any(row['artifact_id']==record['id'] for row in saved['draft_source_issues'])
    assert saved['delivery'] and saved['ledger']['used']['data_requests']==0


@pytest.mark.parametrize('resume', [False, True])
def test_model_stops_after_catalogue_and_structured_data_without_planning_a_download(tmp_path, resume):
    phases, transported = [], []
    directory_request = dict(id='browse_directory', source='ifind', operation='report_query',
                             parameters=dict(ifind_query('600519.SH'), beginrDate='2026-10-02'),
                             question_ids=['q1'])

    def reason(task, messages, *, phase, **options):
        phases.append(phase)
        if phase=='draft_report':
            return json.dumps(historic_proposal(material,catalogue))
        contract = json.loads(messages[-1]['content'])['data_request_contract']
        assert not original_tools(contract)
        assert '首稿复用已留存' in contract['historical_original_rule']
        return json.dumps(dict(data_requests=[directory_request,*requests]))

    def transport(task, source, operation, parameters, credentials):
        assert source=='ifind' and not credentials
        if operation=='get_limit_policy':
            raise ContractError('合成样例不加载账户策略')
        transported.append(operation)
        assert operation=='report_query'
        return snapshot_record(task, source, operation, parameters, dict(errorcode=0,data=[ifind_row(seq='another')]))

    with patch.object(socket.socket, 'connect', side_effect=AssertionError('No network')):
        archive = ResearchArchive(tmp_path)
        agent = ResearchAgent(archive, ROOT, offline=False, model=SimpleNamespace(reason=reason),
                              source_credentials={}, draft_first=True)
        try:
            task = agent.create(QUESTION, entry='事件', objects=['600519.SH'],
                                cutoff=CUTOFF, research_mode='historical')
            material, catalogue, quote, requests = seed_requests(task)
            task['synthetic'] = True
            if resume:
                saved_requests = [directory_request, *requests]
                task['draft_acquisition_checkpoint'] = dict(version=1, attempt=1,
                    scope=_planning_scope(task), sources_before=_source_stamps(task['source_records']),
                    requests=saved_requests, request_hash=fingerprint(saved_requests))
            archive.save(task)
            agent.collector.external = transport
            saved = agent.start(task['id'])
            check_historical_acquisition(saved, material, catalogue, quote)
            assert phases == (['draft_report'] if resume else ['draft_plan', 'draft_report'])
            assert transported == ['report_query']
            assert 'browse_directory' in saved['draft_acquisition_coverage']['complete_request_ids']
            assert saved['draft_acquisition_coverage']['navigation_request_ids'] == []
        finally:
            agent.close_runtime()
