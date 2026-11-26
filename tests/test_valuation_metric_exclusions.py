"""Individual valuation exclusions agree across planning, dispatch, and reporting."""
from unittest.mock import patch

from lxml import html
import pytest

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.draft_sources import acquisition_coverage, observations, stated_latest_requests
from researchhelper.draft_workflow import _serialize_request, direct_readout
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import validate_source_request
from researchhelper.market_series import (
    INDEX_PE_INDICATOR, INDEX_PB_INDICATOR, PB_MRQ_INDICATOR, PE_TTM_INDICATOR,
    SERIES_FAMILIES, STATED_LATEST_EXACT_NAV_VERSION, STATED_LATEST_POLICY,
    series_family, series_settings, stated_direct_latest_scopes, valuation_exclusion_scope,
)
from researchhelper.report_designer import render_html
from tests import ROOT
from tests.test_market_calendar_coverage import append_calendar_fixture_record

STOCK = '600519.SH'
INDEX = '000300.SH'
DAY = '2026-10-29'
PE = PE_TTM_INDICATOR
PB = PB_MRQ_INDICATOR


@pytest.fixture
def agent(tmp_path):
    with (patch('socket.socket.connect', side_effect=AssertionError('No network')),
          patch('researchhelper.engine.now', return_value=DAY+'T10:00:00+00:00')):
        value = ResearchAgent(ResearchArchive(tmp_path), ROOT, offline=True, draft_first=True)
        try:
            yield value
        finally:
            value.close_runtime()


def latest_requests(task):
    result = []
    for row in stated_latest_requests(task, acquisition_coverage(task)):
        wire = _serialize_request(row, task)
        fields = (wire['parameters']['indicators'].split(',') if wire['operation'] == 'cmd_history_quotation'
                  else [column['indicator'] for column in wire['parameters']['indipara']])
        result.extend(fields)
    return result


@pytest.mark.parametrize('question, expected, direct', [
    ('核对600519.SH最近一个交易日的PE(TTM)，不查PB(MRQ)。', [PE], True),
    ('核对600519.SH最新PB(MRQ)。请不要查滚动市盈率。', [PB], True),
    ('核对000300.SH最近一个交易日的指数市净率。不要查指数市盈率。', [INDEX_PB_INDICATOR], True),
    ('核对600519.SH最新收盘价、PE(TTM)和PB(MRQ)，不查市净率。', ['close', PE], True),
    ('核对600519.SH最新PE(TTM)。不查PB(MRQ)。分析是否适合增持。', [PE], False),
    ('核对600519.SH最新PE(TTM)，不要查PE(TTM)。', [], False),
    ('核对600519.SH最新PE(TTM)，不查PB(MRQ)。不用iFinD。', [], False),
    ('核对600519.SH最新PE(TTM)，不查PB(MRQ)。本轮不联网。', [], False),
    ('核对600519.SH最新PE(TTM)。不查指数市盈率。', [PE], True),
])
def test_only_the_specified_valuation_measure_is_removed(agent, question, expected, direct):
    task = agent.create(question, research_mode='current')
    assert latest_requests(task) == expected
    assert bool(direct_readout(task)) is direct


def test_quoted_text_and_frozen_research_do_not_apply_the_new_exclusion(agent):
    task = agent.create('核对600519.SH最新PE(TTM)。“不查PE”只作备注。', research_mode='current')
    assert valuation_exclusion_scope(task) == (task['question'], frozenset())
    task = agent.create('核对600519.SH最新PE(TTM)，不查PB(MRQ)。', research_mode='current')
    assert [row['indicator'] for row in stated_direct_latest_scopes(task)] == [PE]
    old = dict(task, **{STATED_LATEST_POLICY: STATED_LATEST_EXACT_NAV_VERSION})
    assert valuation_exclusion_scope(old) == (old['question'], frozenset())
    assert stated_direct_latest_scopes(old) == []


def series_parameters(task, code, *fields):
    family = SERIES_FAMILIES[series_family(code)]
    return dict(codes=code, startdate=DAY, enddate=DAY, functionpara=series_settings(task, code),
        indipara=[dict(indicator=field, indiparams=family[field]) for field in fields])


def test_mixed_plan_cannot_dispatch_excluded_fields(agent):
    question = '比较600519.SH最新估值并分析盈利是否匹配当前价格。不取PB。'
    task = agent.create(question, research_mode='current')
    valid = series_parameters(task, STOCK, PE)
    assert validate_source_request(task, 'ifind', 'date_sequence', valid) == valid
    with pytest.raises(ContractError, match='排除对应估值字段'):
        validate_source_request(task, 'ifind', 'date_sequence', series_parameters(task, STOCK, PE, PB))


@pytest.mark.parametrize('code, expected, omitted, query, raw_value', [
    (STOCK, PE, PB, '核对600519.SH最新PE(TTM)，不查PB(MRQ)。', 21.73),
    (INDEX, INDEX_PB_INDICATOR, INDEX_PE_INDICATOR, '核对000300.SH最新指数市净率，不查指数市盈率。', 1.44),
])
def test_retained_response_keeps_raw_cells_but_delivers_only_the_requested_metric(agent, code, expected, omitted, query, raw_value):
    task = agent.create('本轮不联网。'+query, research_mode='current')
    parameters = series_parameters(task, code, expected, omitted)
    snapshot = dict(errorcode=0, tables=[dict(thscode=code, time=[DAY],
        table={expected:[raw_value], omitted:[99.8765]})])
    append_calendar_fixture_record(task, 'date_sequence', parameters, snapshot)
    agent.archive.save(task)
    assert [row['field'] for row in observations(task)] == [expected]
    final = agent.start(task['id'])
    assert final['delivery'] and final['source_records'][0]['snapshot'] == snapshot
    draft = final['draft_report']
    assert draft['organization'] == 'fact'
    assert [(row['field'], row['period'], row['raw_value']) for row in draft['facts']] == [(expected, DAY, raw_value)]
    assert final['ledger']['used']['model_calls'] == 0
    assert final['ledger']['dispatches'] == task['ledger']['dispatches']
    content = agent.export(task['id'])['content']
    for profile in ('standard', 'detailed'):
        tree = html.document_fromstring(render_html(content, ROOT, profile=profile))
        summary = tree.get_element_by_id('summary')
        link, = summary.xpath('.//a[@class="quantity-link"]')
        inspector = tree.get_element_by_id(link.get('href')[1:])
        assert len(inspector.xpath('.//tr[@data-observation-id]')) == 1
        assert DAY in inspector.text_content()
        assert '99.8765' not in tree.find('.//main').text_content()
        assert '策略逻辑' not in tree.find('.//main').text_content()
