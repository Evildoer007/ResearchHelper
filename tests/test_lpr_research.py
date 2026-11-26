from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from unittest.mock import patch

from lxml import html
import pytest

from researchhelper.archive import ResearchArchive
from researchhelper.draft_sources import observations
from researchhelper.draft_visuals import _calculation_dependencies, logic_figure_plan
from researchhelper.draft_workflow import direct_readout
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import data_request_contract
from researchhelper.lpr_rates import HISTORY_PAGE, OPERATION, source_url, study_scope
from researchhelper.lpr_readout import verified_lpr_answer
from researchhelper.report_designer import design

ROOT = Path(__file__).resolve().parents[1]
CUTOFF = '2026-11-23T14:00:00+08:00'
QUESTION = '最新一期LPR调了吗？对银行净息差和存量房贷有什么影响？按最新两期做份研究报告。'


@pytest.mark.parametrize('previous, expected_changes', [
    (('3.00', '3.50'), (0, 0)),
    (('3.05', '3.45'), (-5, 5)),
    (None, ()),
])
def test_one_prompt_uses_registered_monthly_quotes_for_a_client_report(tmp_path, previous, expected_changes):
    archive = ResearchArchive(tmp_path)
    agent = ResearchAgent(archive, ROOT, offline=False, draft_first=True, require_argument_support=True)

    def reply(task, parameters, started, seconds, event, **kwargs):
        event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True)
        current = {'showDateCN': '2026-11-20', '1Y': '3.00', '5Y': '3.50'}
        rows = [current]
        if previous:
            rows.append({'showDateCN': '2026-10-20', '1Y': previous[0], '5Y': previous[1]})
        original = json.dumps(dict(head=dict(rep_code='200'), records=rows,
            data=dict(startDateCN=parameters['start'], endDateCN=parameters['end'], baseCurveCfgList=['1Y', '5Y'])))
        return dict(url=source_url(parameters), original_text=original, original_sha256=sha256(original.encode()).hexdigest())

    try:
        with (patch('researchhelper.engine.now', return_value=CUTOFF),
              patch('researchhelper.source_transport.official_lpr_history', side_effect=reply) as dispatched,
              patch('researchhelper.nfra_bank_statistics.request_options', return_value={}),
              patch('socket.socket.connect', side_effect=AssertionError('No external calls'))):
            task = agent.create(QUESTION, research_mode='current')
            selected = [row for row in data_request_contract(task, first_report=True)['tools'] if row['operation'] == OPERATION]
            assert len(selected) == 1 and selected[0]['available_parameter_sets'] == [study_scope(task)['parameters']]
            assert direct_readout(task)[0] == 'lpr'
            result = agent.start(task['id'])
        assert dispatched.call_count == 1
        assert result['ledger']['used']['data_requests'] == 1
        assert result['ledger']['used']['model_calls'] == 0
        assert result['status'] == ('已完成' if previous else '部分完成'), (result['gaps'], result['error'])
        report = archive.report(task['id'], 1)
        content = report['content']
        assert logic_figure_plan(content) == []
        calculations = list(_calculation_dependencies(content).values())
        assert tuple(row['value'] for row in calculations if row['comparison_kind'] == 'lpr_move') == expected_changes
        page = html.document_fromstring(design(content, ROOT, profile='standard')['html'])
        shown = page.find('.//main').text_content()
        assert '1年期LPR3%' in shown and '5年期以上LPR3.5%' in shown
        assert '光大证券 金融创新业务总部' in shown
        assert '重定价日' in shown and '付息负债成本率' in shown
        assert shown.index('风险提示') > shown.index('策略逻辑二')
        assert HISTORY_PAGE in page.xpath('//footer//a/@href')
        assert '相邻月份的两期限报价未全部取得' not in shown
        if previous:
            assert result['questions'][0]['lpr_answer'] == verified_lpr_answer(result, result['draft_report'])
            if expected_changes == (0, 0):
                assert '两期限报价维持不变' in shown
            else:
                assert '下调5个基点' in shown and '上调5个基点' in shown
            corrupted = deepcopy(result)
            corrupted['source_records'][0]['snapshot']['original_text'] += ' '
            assert not observations(corrupted)
            assert verified_lpr_answer(corrupted, result['draft_report']) is None
        else:
            assert '两期限报价维持不变' not in shown
            assert 'lpr_answer' not in result['questions'][0]
    finally:
        agent.close_runtime()


def test_explicit_constraints_and_other_metrics_do_not_become_a_complete_lpr_readout(tmp_path):
    agent = ResearchAgent(ResearchArchive(tmp_path), ROOT, offline=True, draft_first=True)
    try:
        with patch('researchhelper.engine.now', return_value=CUTOFF):
            only_ifind = agent.create(QUESTION + ' 只用iFinD。', research_mode='current')
            bank = agent.create(QUESTION + ' 测算具体银行息差变化多少。', research_mode='current')
            future = agent.create('2026年12月LPR调了吗？', research_mode='current')
            no_source = agent.create(QUESTION + ' 请勿联网。', research_mode='current')
            alias = agent.create('国内贷款报价利率最近变了没，房贷和银行怎么理解？', research_mode='current')
        assert not study_scope(only_ifind) and not study_scope(future) and not study_scope(no_source)
        assert study_scope(bank) and not direct_readout(bank)
        assert direct_readout(alias)[0] == 'lpr'
    finally:
        agent.close_runtime()
