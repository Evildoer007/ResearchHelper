"""Deliver public document text through the existing interrupted-draft action."""
from copy import deepcopy
import json
import socket
from unittest.mock import patch

from lxml import html
import pytest

from tests import ROOT
from tests.test_announcement_partial_delivery import ifind_query, ifind_row
from tests.test_draft_historical_original import CUTOFF
from tests.test_filing_acquisition_offline import synthetic_pdf
from tests.test_independent_official_sources import PBC_URL
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, charge, fingerprint
from researchhelper.draft_sources import observations, retained_public_excerpts
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import EvidenceCollector, extract_material, material_record, snapshot_record
from researchhelper.official_sources import release_choices
from researchhelper.artifact_exporter import export_report
from researchhelper.report_designer import read_presentation_document, render_html
from researchhelper.service import workspace_task_view


POLICY = '符合资格的科技企业可提出政策申请，须完成资格备案，预计分两批受理。'
POLICY_TITLE = '科技企业政策申请条件'
POLICY_QUESTION = '核对科技企业政策申请和资格备案要求，整理一份报告。'
FILING = 'Revenue guidance is conditional on the approval of the board.'
FILING_TITLE = '季度公告及经营指引'
FILING_QUESTION = '核对600519.SH公告中的revenue guidance和生效条件。'
NOT_RELEVANT = '编辑署名和网站模板。'


def seed_policy(task, *, body=POLICY, catalogue_only=False, collector=None):
    collector = collector or EvidenceCollector(offline=False)
    catalogue = f'<a href="{PBC_URL}">{POLICY_TITLE}</a>'.encode()
    payload = f'<div id="zoom"><p>{body}</p><p>{NOT_RELEVANT}</p></div>'.encode()
    with patch('researchhelper.source_transport.official_html', side_effect=[catalogue, payload]):
        task['source_records'].append(collector.external(
            task, 'official', 'release_catalog', dict(channel='pbc_news', page='0'), {}))
        if catalogue_only:
            return None
        choice = release_choices(task)[0]
        params = {key: choice[key] for key in ('catalog_id', 'release_id')}
        record = collector.external(task, 'official', 'release_original', params, {})
    task['source_records'].append(record)
    return record


def seed_filing(task, *, readable=True):
    collector = EvidenceCollector(offline=False)
    catalogue = snapshot_record(task, 'ifind', 'report_query', ifind_query('600519.SH'),
        {'errorcode': 0, 'data': [ifind_row(reportTitle=FILING_TITLE)]})
    task['source_records'].append(catalogue)
    params = dict(catalog_id=catalogue['id'], source_id='selected')
    with patch.object(collector, '_download_original', return_value=synthetic_pdf(readable, statement=FILING)):
        record = collector.external(task, 'ifind', 'filing_original', params, {})
    task['source_records'].append(record)
    return record


def interrupted_task(agent, kind):
    question, entry, objects = ((FILING_QUESTION, '事件', ['600519.SH']) if kind == 'filing'
                                else (POLICY_QUESTION, '宏观', []))
    task = agent.create(question, entry=entry, objects=objects, research_mode='current', owner_account='admin')
    task.update(status='运行中', run_mode='model', batch=1)
    return task


@pytest.fixture
def agent(tmp_path):
    research = ResearchAgent(ResearchArchive(tmp_path), ROOT, offline=True,
                             require_argument_support=True, draft_first=True)
    try:
        with patch.object(socket.socket, 'connect', side_effect=AssertionError('No remote requests')):
            yield research
    finally:
        research.close_runtime()


def assert_publication(rendered, expected, title, location):
    page = html.document_fromstring(rendered)
    original, = page.xpath('//main//q[text()=$text]', text=expected)
    link, = original.getparent().getnext().xpath('./a[@aria-haspopup="dialog"]')
    record = page.get_element_by_id(link.get('href')[1:])
    assert title in link.text_content() and location in link.text_content()
    assert record.get('data-inspection-title') == '披露原文' and expected in record.text_content()
    assert NOT_RELEVANT not in rendered and 'original_base64' not in rendered
    assert '首发时点及实际影响以独立证据为准' in page.find('.//main').text_content()
    document = json.loads(page.get_element_by_id('rh-publication-document').text)
    assert any(block.get('text') == expected for block in document['blocks'])


@pytest.mark.parametrize('kind,seed,expected,title,location,heading', [
    ('policy', seed_policy, POLICY, POLICY_TITLE, '段落', POLICY_TITLE + '原文核对'),
    ('filing', seed_filing, FILING, FILING_TITLE, '第1页', '合成发行人甲' + FILING_TITLE + '原文核对'),
])
def test_interrupted_public_original_is_cited_in_both_report_profiles(
        agent, tmp_path, kind, seed, expected, title, location, heading):
    task = interrupted_task(agent, kind)
    original = seed(task)
    agent.archive.save(task)
    agent.recover_interrupted()
    before = agent.archive.get(task['id'])
    assert before['status'] == '失败' and observations(before) == []
    assert workspace_task_view(before)['can_finish_from_data']
    with patch.object(agent.collector, 'external', side_effect=AssertionError('No source retry')):
        after = agent.start(task['id'], retained_data_only=True)
    assert after['status'] == '部分完成' and after['run_mode'] == 'local_draft'
    assert after['draft_report']['retained_data_only'] and after['draft_report']['facts'] == []
    assert after['draft_report']['logics'][0]['source_refs'] == [original['id']]
    assert all(after[key] == before[key] for key in ('id', 'version', 'cutoff', 'input_hash', 'source_records'))
    assert after['ledger']['dispatches'] == before['ledger']['dispatches']
    for key in ('data_requests', 'model_calls'):
        assert after['ledger']['used'][key] == before['ledger']['used'][key]
    report = agent.archive.report(after['id'], after['version'])
    assert report['html_integrity_verified']
    assert after['draft_report']['title'] == after['delivery']['title'] == report['content']['title'] == heading
    assert workspace_task_view(after)['delivery']['title'] == heading
    for profile in ('standard', 'detailed'):
        rendered = render_html(report['content'], ROOT, profile=profile)
        assert_publication(rendered, expected, title, location)
        paper, presentation = read_presentation_document(rendered)
        assert paper.findtext('.//title') == paper.findtext('.//main//h1') == presentation['title'] == heading
        exported = export_report(report['content'], ROOT, tmp_path / 'exports', profile=profile)
        label = '标准报告' if profile == 'standard' else '详细报告'
        assert exported['filename'] == f'{heading}_v1_{label}.html'
    assert agent.archive.report(after['id'], 1) == report


@pytest.mark.parametrize('kind', ['catalogue', 'blank', 'private', 'irrelevant', 'historical',
                                 'foreign', 'duplicate', 'changed', 'signed_text'])
def test_unusable_originals_do_not_enable_local_delivery(agent, kind):
    task = interrupted_task(agent, 'filing' if kind == 'blank' else 'policy')
    if kind == 'blank':
        seed_filing(task, readable=False)
    elif kind == 'catalogue':
        seed_policy(task, catalogue_only=True)
    elif kind == 'private':
        raw = POLICY.encode()
        task['materials'].append(material_record(task, extract_material('个人附件.txt', raw), raw))
    else:
        text = (NOT_RELEVANT if kind == 'irrelevant' else
                '科技企业政策申请请访问https://example.org/a?token=private-fixture' if kind == 'signed_text' else POLICY)
        original = seed_policy(task, body=text)
        if kind == 'historical':
            from researchhelper.planning import input_fingerprint
            task.update(research_mode='historical', cutoff='2026-10-17T18:00:00+08:00')
            task['input_hash'] = input_fingerprint(task)
        elif kind == 'foreign':
            original['research_version'] += 1
        elif kind == 'duplicate':
            task['source_records'].append(deepcopy(original))
        elif kind == 'changed':
            original['snapshot']['parts'][0]['text'] = POLICY + '修改了留存原文。'
    task['status'] = '失败'
    assert retained_public_excerpts(task) == []
    assert not workspace_task_view(task).get('can_finish_from_data')
    if kind in ('blank', 'catalogue', 'private', 'irrelevant'):
        agent.archive.save(task)
        with pytest.raises(ContractError, match='没有可用于出稿'):
            agent.start(task['id'], retained_data_only=True)


def test_rehashed_extraction_still_requires_original_bytes(agent):
    task = interrupted_task(agent, 'policy')
    original = seed_policy(task)
    original['snapshot']['parts'][0]['text'] = POLICY + '加入从未披露的内容。'
    original['snapshot_hash'] = fingerprint(original['snapshot'])
    assert retained_public_excerpts(task)
    assert retained_public_excerpts(task, verify_text=True) == []


def test_late_original_is_excluded_without_losing_cutoff_price(agent):
    task = agent.create(FILING_QUESTION + '同时核对2026年10月16日收盘价。', entry='事件',
                        objects=['600519.SH'], cutoff=CUTOFF, research_mode='historical')
    task.update(status='运行中', run_mode='model', batch=1)
    original = seed_filing(task)
    parameters = dict(codes='600519.SH', indicators='close', startdate='2026-10-16', enddate='2026-10-16',
                      functionpara=dict(Currency='RMB', CPS='1', Fill='Omit'))
    quote = snapshot_record(task, 'ifind', 'cmd_history_quotation', parameters,
        {'errorcode': 0, 'tables': [dict(thscode='600519.SH', time=['2026-10-16'], table={'close': [112]})]})
    task['source_records'].append(quote)
    agent.archive.save(task)
    agent.recover_interrupted()
    assert workspace_task_view(agent.archive.get(task['id']))['can_finish_from_data']
    result = agent.start(task['id'], retained_data_only=True)
    assert result['delivery'] and result['draft_report']['facts'][0]['value'] == 112
    assert all(original['id'] not in item.get('source_refs', []) for item in result['draft_report']['logics'])
    report = agent.archive.report(task['id'], task['version'])
    text = html.document_fromstring(render_html(report['content'], ROOT)).find('.//main').text_content()
    assert '112' in text and FILING not in text and '历史可得性待核对' in text


@pytest.mark.parametrize('reply', [None, '{"conclusion":"无效截断的文本', '{"title":"只有标题"}'])
def test_model_with_no_prose_uses_the_same_original_in_first_delivery(agent, reply):
    original = interrupted_task(agent, 'policy')
    seed_policy(original)
    original['status'] = '失败'
    agent.archive.save(original)
    calls = []

    class NoProseModel:
        def reason(self, task, messages, *, phase, delivery=False, **options):
            calls.append(phase)
            charge(task['ledger'], 'model_calls', delivery=delivery)
            if phase == 'draft_plan':
                return '{"data_requests":[]}'
            if reply is None:
                raise ContractError('测试响应没有可用正文')
            return reply

    agent.offline = False
    agent.model = NoProseModel()
    with patch.object(agent.collector, 'external', side_effect=AssertionError('No source retry')):
        result = agent.start(original['id'])
    assert calls == ['draft_plan', 'draft_report']
    assert result['delivery'] and result['run_mode'] == 'model'
    assert result['draft_report']['origin'] == 'data_outline'
    assert result['ledger']['used']['data_requests'] == original['ledger']['used']['data_requests']
    report = agent.archive.report(result['id'], result['version'])
    assert_publication(render_html(report['content'], ROOT), POLICY, POLICY_TITLE, '段落')


def test_restart_after_empty_reply_keeps_proof_and_does_not_reissue(agent):
    task = interrupted_task(agent, 'policy')
    seed_policy(task)
    task['status'] = '失败'
    agent.archive.save(task)
    agent.offline = False

    class Model:
        def reason(self, task, messages, *, phase, delivery=False, **options):
            charge(task['ledger'], 'model_calls', delivery=delivery)
            return '{"data_requests":[]}' if phase == 'draft_plan' else '{"conclusion":"无效截断'

    agent.model = Model()
    with patch('researchhelper.draft_workflow._apply_report', side_effect=ContractError('模拟本地保存中断')):
        failed = agent.start(task['id'])
    assert failed['status'] == '失败' and workspace_task_view(failed)['has_retained_submission']
    with patch.object(agent.model, 'reason', side_effect=AssertionError('No model retry')):
        result = agent.retry_save(task['id'])
    assert result['status'] == '部分完成' and result['draft_report']['origin'] == 'data_outline'
    for key in ('batch', 'version', 'cutoff', 'draft_submissions'):
        assert result[key] == failed[key]
    assert result['ledger']['dispatches'] == failed['ledger']['dispatches']
    report = agent.archive.report(result['id'], result['version'])
    assert_publication(report['html'], POLICY, POLICY_TITLE, '段落')
