"""Small, synthetic replacements for ephemeral acceptance task archives."""
from copy import deepcopy
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests import ROOT
from tests.fundamental_reasoning_chain_fixture import METHODS, prepare_chain
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import EvidenceCollector, IFIND_REPORT_FIELDS, candidate_evidence
from researchhelper.planning import input_fingerprint


@lru_cache(maxsize=2)
def _financial_fixture(both_ratios):
    with TemporaryDirectory(prefix='rh-financial-replay-') as folder, patch(
            'socket.socket.connect', side_effect=AssertionError('No network')):
        agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True)
        try:
            task, original = prepare_chain(agent)
            task = agent.archive.get(task['id'])
            if not both_ratios:
                task['questions'][0]['method_needs'] = [METHODS[0]]
                task['questions'][1]['method_needs'] = [METHODS[1]]
            for question in task['questions']:
                question['status'] = '未回答'
            task['input_hash'] = input_fingerprint(task)
            task['run_mode'] = 'model'
            task['reasoning_state'] = {}
            task['ledger']['used']['model_calls'] = 7
            agent._adopt_method_knowledge(task)
            materials = {record['id']: record for record in task['materials']}
            for spec in original['evidence_specs']:
                item = candidate_evidence(task, materials[spec['artifact_id']], spec)
                task['evidence'].append(agent.analyzer.assess(task, item, spec['review'], task['materials']))
            calculation_questions = ([task['questions'][0]] * len(METHODS)
                                     if both_ratios else task['questions'][:len(METHODS)])
            for spec, question in zip(original['calculations'], calculation_questions, strict=True):
                agent._calculate_spec(task, dict(spec, question_ids=[question['id']]))
            if len(task['calculations']) != 2:
                raise AssertionError(task['gaps'])
            task['host_result'] = {'answers': {}, 'theses': [], 'calculations': []}
            return task
        finally:
            agent.close_runtime()


def financial_task(*, both_ratios=False):
    return deepcopy(_financial_fixture(both_ratios))


def numeric_proposal(*, valid):
    text = '净利润率为12%。' if valid else '净利润率为91.23%。'
    return dict(answers={'financial': dict(answer=text, refs=['revenue', 'net_profit'],
                                           calculation_refs=['profit_margin'])}, theses=[])


ETF_ORIGINAL = Path(__file__).parent / 'fixtures/synthetic-etf-nav-span.pdf'
ETF_CODE = '510500.SH'
ETF_NAV = '9.1808'
ETF_CLOSE = 9.224
ETF_VALUATION_DATE = '2026-06-30'


def _listed_etf_pdf(has_trading_code):
    """Deterministic PDF exercising a literal NAV, a dated row and share identity."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas

    stream = BytesIO()
    font = 'STSong-Light'
    pdfmetrics.registerFont(UnicodeCIDFont(font))
    pages = [
        ['合成基金季度报告，仅作离线测试', '报告期末：2026年6月30日', '送出日期：2026年7月21日'],
        ['基金主代码 510500', '基金运作方式 交易型开放式', '单位：人民币元'],
        [f'2026年6月30日 基金份额净值为{ETF_NAV}元', '同期基金份额净值增长率为0.61%'],
    ]
    if has_trading_code:
        pages[1].insert(1, '交易代码 510500')
    paper = canvas.Canvas(stream, pagesize=(595, 842), invariant=1)
    for lines in pages:
        paper.setFont(font, 14)
        for index, line in enumerate(lines):
            paper.drawString(42, 776 - 28 * index, line)
        paper.showPage()
    paper.save()
    return stream.getvalue()


@lru_cache(maxsize=3)
def _etf_fixture(profile):
    with TemporaryDirectory(prefix='rh-etf-original-replay-') as folder, patch(
            'socket.socket.connect', side_effect=AssertionError('No network')):
        agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
        try:
            with patch('researchhelper.engine.now', return_value='2026-10-01T18:00:00+08:00'):
                needs = ['原件报告期末单位净值及金额单位']
                if profile != 'span':
                    needs.insert(0, '该基金同日未复权收盘价')
                task = agent.create('合成ETF原件定位验收' if profile == 'span' else
                    f'核对合成样本{ETF_CODE}在2026年6月30日的收盘折溢价', entry='ETF', objects=[ETF_CODE],
                    research_mode='current', questions=[dict(id='q1', kind='fact',
                        question='报告期末单位净值是多少' if profile == 'span' else '收盘价与同日单位净值及折溢价是多少',
                        evidence_needs=needs, method_needs=['etf_daily_nav_premium_v1'])])
            # This fixture exercises the original position protocol v1. The
            # newer selectors have their own dedicated regression suites.
            for flag in ('nav_original_choice_version', 'original_number_reference_version',
                         'original_period_selection_version', 'original_observation_selection_version',
                         'general_original_selection_version', 'literal_quantity_reference_version',
                         'original_prompt_projection_version', 'mixed_original_selection_version',
                         'independent_original_selections_version', 'original_context_selection_version',
                         'filing_filter_profile_version'):
                task.pop(flag, None)
            if profile != 'span':
                # Exercise the published v3 span and v4/v5 reference forms,
                # with the v2 API-cell shape used by those archived tasks.
                task.update(mixed_original_selection_version=1, original_context_selection_version=1,
                            listed_etf_unit_context_version=1, api_need_compatibility_version=1)
                for flag in ('missing_api_context_selection_version', 'listed_report_unit_context_version'):
                    task.pop(flag, None)
            task['synthetic'] = True
            task['input_hash'] = input_fingerprint(task)
            row = dict(reportDate='2026-07-21', thscode=ETF_CODE, secName='合成基金', ctime=None,
                reportTitle='合成基金2026年第2季度报告',
                pdfURL='https://static.cninfo.com.cn/finalpage/synthetic-etf-nav-span.pdf', seq='synthetic_nav')
            params = dict(codes=ETF_CODE, functionpara={}, beginrDate='2026-07-01', endrDate='2026-07-31',
                          outputpara=','.join(field+':Y' for field in IFIND_REPORT_FIELDS))
            task['data_need_specs'] = [dict(id='nav_directory', source='ifind', operation='report_query',
                                          parameters=params, question_ids=['q1'])]
            collector = EvidenceCollector(offline=False)

            def provider_response(current, request, timeout, event):
                event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True)
                operation = request.full_url.rsplit('/', 1)[-1]
                if operation == 'report_query':
                    return dict(errorcode=0, tables=[deepcopy(row)])
                if operation == 'cmd_history_quotation':
                    price = 7.42 if profile == 'span' else ETF_CLOSE
                    quote = dict(thscode=ETF_CODE, time=[ETF_VALUATION_DATE], table=dict(close=[price]))
                    return dict(errorcode=0, tables=[quote])
                raise AssertionError(f'Unexpected fixture request {operation}')

            def original(current, url, timeout, event, source_id=None):
                event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True)
                return ETF_ORIGINAL.read_bytes() if profile == 'span' else _listed_etf_pdf(profile == 'listed')

            with patch.object(collector, '_http_json', side_effect=provider_response), patch.object(
                    collector, '_download_original', side_effect=original):
                directory = collector.external(task, 'ifind', 'report_query', params, {'access_token': 'OFFLINE_TEST'})
                task['source_records'].append(dict(directory, question_ids=['q1']))
                report = collector.external(task, 'ifind', 'filing_original',
                    dict(catalog_id=directory['id'], source_id='synthetic_nav'), {})
                task['source_records'].append(dict(report, question_ids=['q1']))
                quote_parameters = dict(codes=ETF_CODE, indicators='close', startdate=ETF_VALUATION_DATE,
                    enddate=ETF_VALUATION_DATE, functionpara=dict(Currency='RMB', CPS='1', Fill='Omit'))
                quote = collector.external(task, 'ifind', 'cmd_history_quotation', quote_parameters,
                                           {'access_token': 'OFFLINE_TEST'})
                task['source_records'].append(dict(quote, question_ids=['q1']))
            return task
        finally:
            agent.close_runtime()


def etf_task():
    return deepcopy(_etf_fixture('span'))


def listed_etf_task(*, has_trading_code=True):
    """Replay the same contract with or without proof of the listed ETF unit."""
    return deepcopy(_etf_fixture('listed' if has_trading_code else 'main-code-only'))
