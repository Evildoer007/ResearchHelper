"""Historical drafts quarantine late or unverified original versions."""
from copy import deepcopy
import socket
from unittest.mock import patch

from lxml import html
import pytest

from tests import ROOT
from tests.test_announcement_partial_delivery import ifind_query, ifind_row
from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.draft_sources import observations, source_context, source_issues, writing_context
from researchhelper.drafts import compose, normalize_report, source_binding
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import (candidate_evidence, data_request_contract, extract_material,
    material_record, snapshot_record)
from researchhelper.report_designer import design

CUTOFF = '2026-10-17T14:00:00+08:00'
UNAVAILABLE = '2025年营业收入同比增长19%'
BODY = '首次发布于2026年10月18日。\n\n合并报表'+UNAVAILABLE+'。'
QUESTION = '截至研究时点核对600519.SH收盘价及公告披露'
EXTRA = '600519.SH 2026-10-16收盘价为112元。'


def seed_historical_task(task):
    raw = BODY.encode()
    material = material_record(task, extract_material('后获材料.txt', raw), raw)
    material['obtained_at'] = '2026-10-18T10:00:00+08:00'
    task['materials'].append(material)
    catalogue = snapshot_record(task,'ifind','report_query',ifind_query('600519.SH'),
        {'errorcode':0,'data':[ifind_row(reportTitle='目录线索甲', ctime='2026-10-15T12:00:00+08:00')]})
    task['source_records'].append(catalogue)
    parameters = dict(codes='600519.SH',indicators='close',startdate='2026-10-16',enddate='2026-10-16',
        functionpara=dict(Currency='RMB',CPS='1',Fill='Omit'))
    quote = snapshot_record(task,'ifind','cmd_history_quotation',parameters,
        {'errorcode':0,'tables':[dict(thscode='600519.SH',time=['2026-10-16'],table={'close':[112]})]})
    task['source_records'].append(quote)
    return material, catalogue


def historic_proposal(material, catalogue):
    return dict(title='历史公告观察', conclusion='公告称“合并报表'+UNAVAILABLE+'”。',
        logics=[
            dict(title='公告收入',analysis='合并报表'+UNAVAILABLE+'。',source_refs=[material['id']]),
            dict(title='收盘原值',analysis=EXTRA,data_refs=['D1']),
            dict(title='目录说明',analysis='目录线索甲已确定。',source_refs=[catalogue['id']]),
        ])


def check_historical_draft(task, material, catalogue):
    draft = task['draft_report']
    assert UNAVAILABLE not in draft['conclusion']
    assert '待核对' in draft['logics'][0]['analysis']
    assert draft['logics'][1]['analysis']==EXTRA
    assert draft['logics'][1]['data_refs']==['D1']
    assert '待核对' in draft['logics'][2]['analysis']
    assert all(not {material['id'],catalogue['id']}.intersection(logic['source_refs'])
               for logic in draft['logics'])
    assert any(edit['reason']=='draft_historical_original_unavailable' for edit in draft['copy_adjustments'])
    assert any('历史可得性待核对' in note for note in draft['reader_notes'])
    assert task['materials'][0]['snapshot']==material['snapshot']


def check_historical_html(text):
    tree = html.document_fromstring(text)
    paper = tree.find('.//main').text_content()
    assert UNAVAILABLE not in paper and '目录线索甲' not in paper
    assert UNAVAILABLE not in text and '目录线索甲' not in text
    assert '112元' in paper and '历史可得性待核对' in paper


@pytest.fixture
def scenario(tmp_path):
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('No network')):
        agent=ResearchAgent(ResearchArchive(tmp_path),ROOT,offline=True,draft_first=True)
        try:
            task=agent.create(QUESTION,entry='事件',objects=['600519.SH'],cutoff=CUTOFF,research_mode='historical')
            yield task
        finally:
            agent.close_runtime()


def test_history_excludes_late_original_and_directory_body_without_losing_prices(scenario):
    task = scenario
    material, catalogue = seed_historical_task(task)
    before = fingerprint(task)
    facts = observations(task)
    assert [row['value'] for row in facts]==[112]
    planning = source_context(task)
    assert all(UNAVAILABLE not in row['content'] and row['id']!=catalogue['id'] for row in planning)
    writing = writing_context(task, facts)
    assert writing['documents']==[]
    assert [row['id'] for row in writing['sources']]==[facts[0]['artifact_id']]
    assert any(issue['artifact_id']==material['id'] and '截止时点后' in issue['reason'] for issue in source_issues(task))
    tool = next(row for row in data_request_contract(task)['tools'] if row['operation']=='filing_original')
    assert tool['available_selections'][0]['catalog_id']==catalogue['id']
    report = normalize_report(historic_proposal(material,catalogue),task,facts)
    assert fingerprint(task)==before
    task['draft_report']=dict(report,facts=facts,calculations=[],source_binding=source_binding(task))
    check_historical_draft(task,material,catalogue)
    for profile in ('standard','detailed'):
        check_historical_html(design(compose(task), ROOT, profile=profile)['html'])


def test_unlinked_historical_quote_does_not_claim_source(scenario):
    task=scenario
    material, _=seed_historical_task(task)
    result=normalize_report(dict(title='公告观察',conclusion='公告称“合并报表'+UNAVAILABLE+'”。',
        logics=[dict(title='观察',analysis='公告称“此前已撤回计划”。')]),task,observations(task))
    assert '历史原件待核对' in result['conclusion'] and UNAVAILABLE not in result['conclusion']
    assert '此前已撤回计划' not in result['logics'][0]['analysis']
    assert material['snapshot']['parts'][1]['text'].endswith('增长19%。')


def seed_proven_historical_original(task):
    raw='首次发布于2026年10月15日。\n\n原件载明该项目仍处观察期。'.encode()
    record=material_record(task,extract_material('已留存原件.txt',raw),raw)
    record['obtained_at']='2026-10-16T09:00:00+08:00'
    task['materials']=[record]
    ref=dict(kind='material',artifact_id=record['id'],snapshot_hash=record['snapshot_hash'],
             part='段落1',quote='首次发布于2026年10月15日。')
    question=task['questions'][0]['id']
    spec=dict(id='prior_exhibit',object='600519.SH',period='公告',nature='已实现事实',unit='不适用',
        text='原件载明该项目仍处观察期。',record_locator=dict(part='段落2'),value=None,question_ids=[question],
        published_at='2026-10-15',publication=dict(at='2026-10-15',precision='日',timezone='Asia/Shanghai',
            certainty='已确认',basis='原件明确首次发布日期',basis_ref=ref))
    item=candidate_evidence(task,record,spec)
    item=ResearchAnalyzer().assess(task,item,dict(question_ids=[question],allowed_natures=['已实现事实'],
                                                conditions=['仅核对该原件内容'],reason='披露发生在截止前'))
    task['evidence']=[item]
    return record


def test_approved_publication_and_pre_cutoff_capture_admit_the_original(scenario):
    task=scenario
    record=seed_proven_historical_original(task)
    assert writing_context(task,[])['documents'][0]['source_ref']==record['id']
    valid=normalize_report(dict(title='历史披露',conclusion='按留存原件核对。',
        logics=[dict(title='已留存原件',analysis='公告称“原件载明该项目仍处观察期”。',source_refs=[record['id']])]),task,[])
    assert '仍处观察期' in valid['logics'][0]['analysis']
    for altered in (dict(record,obtained_at='2026-10-17T14:01:00+08:00'),
                    dict(record,obtained_at='2026-10-15T12:00:00+08:00'),
                    dict(record,obtained_at='2026-10-16')):
        changed=deepcopy(task);changed['materials']=[altered]
        assert writing_context(changed,[])['documents']==[]
    forged=deepcopy(task)
    forged['evidence'][0]['publication']['basis_ref']['quote']='改动了首发依据'
    assert writing_context(forged,[])['documents']==[]
    legacy=dict(task,draft_historical_original_version=None)
    legacy['materials']=[dict(record,obtained_at='2026-11-01T09:00:00+08:00')]
    assert writing_context(legacy,[])['documents']
