"""Two synthetic fund disclosures read and bound by the regular source path."""
from copy import deepcopy
from functools import lru_cache
from io import BytesIO
import json
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import EvidenceCollector, IFIND_REPORT_FIELDS
from researchhelper.source_reader import binding, read

FUNDS = ('510300.SH', '510500.SH')
METHOD = 'management_custody_fee_comparison_v1'
PERIOD = '2026-01-01/2026-06-30'


def _quarterly_pdf(code, name, custody, *, wrapped, has_trading_code):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas

    stream = BytesIO()
    font = 'STSong-Light'
    pdfmetrics.registerFont(UnicodeCIDFont(font))
    paper = canvas.Canvas(stream, pagesize=(595, 842), invariant=1)
    period_lines = (['报告期自2026年1月1日起', '至2026年6月30日止'] if wrapped
                    else ['报告期2026年1月1日至2026年6月30日'])
    code = code.split('.')[0]
    pages = [
        ['合成基金半年度报告，仅作离线测试', *period_lines, '送出日期：2026年7月21日'],
        [f'基金简称 {name}', f'基金主代码 {code}', '基金运作方式 交易型开放式',
         '基金份额上市的证券交易所 上海证券交易所', '上市日期 2024年1月2日'],
        ['管理费按前一日基金资产净值的0.15%年费率计提。',
         f'托管费按前一日基金资产净值的{custody}%年费率计提。'],
    ]
    if has_trading_code:
        pages[1].insert(2, f'交易代码 {code}')
    for lines in pages:
        paper.setFont(font, 13)
        for index, text in enumerate(lines):
            paper.drawString(42, 776 - 28 * index, text)
        paper.showPage()
    paper.save()
    return stream.getvalue()


@lru_cache(maxsize=4)
def _source_task(has_trading_code, multiple_catalogue_rows):
    with TemporaryDirectory(prefix='rh-fee-disclosure-') as folder, patch(
            'socket.socket.connect', side_effect=AssertionError('No actual data or model calls')):
        agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
        try:
            question = '比较合成基金510300.SH和510500.SH在2026年上半年的管理费与托管费。'
            task = agent.create(question, entry='ETF', objects=list(FUNDS), research_mode='current',
                owner_account='admin', questions=[dict(id='q1', kind='comparison', question=question,
                    evidence_needs=['两只基金同一期间的管理费和托管费年费率'], method_needs=[METHOD])])
            task['synthetic'] = True
            if multiple_catalogue_rows:
                agent._adopt_method_knowledge(task)
            collector = EvidenceCollector(offline=False)
            urls = {code:f'https://static.cninfo.com.cn/finalpage/synthetic-fee-{code[:6]}.pdf' for code in FUNDS}
            names = dict(zip(FUNDS, ('合成样本甲ETF', '合成样本乙ETF'), strict=True))
            rows = [dict(reportDate='2026-07-21', thscode=code, secName=names[code], ctime=None,
                reportTitle=f'{names[code]}2026年半年度报告', pdfURL=urls[code], seq='fee_'+code[:6])
                for code in FUNDS]
            selected = rows[:]
            if multiple_catalogue_rows:
                selected[1]['reportDate'] = '2026-07-22'
                rows = [*selected, *(dict(row, reportDate='2026-07-28', seq='notice_'+row['thscode'][:6],
                    reportTitle=names[row['thscode']]+'补充公告', pdfURL=row['pdfURL'].replace('.pdf', '-extra.pdf'))
                    for row in selected)]
            contents = {urls[code]:_quarterly_pdf(code, names[code], custody, wrapped=code==FUNDS[1],
                                               has_trading_code=has_trading_code)
                        for code,custody in zip(FUNDS, ('0.05','0.10'), strict=True)}
            params = dict(functionpara={}, beginrDate='2026-07-01', endrDate='2026-07-31',
                          outputpara=','.join(field+':Y' for field in IFIND_REPORT_FIELDS))

            def catalogue(current, request, timeout, event):
                event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True)
                code = json.loads(request.data)['codes']
                return dict(errorcode=0, tables=[deepcopy(row) for row in rows if row['thscode']==code])

            def download(current, url, timeout, event, source_id=None):
                event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True)
                return contents[url]

            with (patch.object(collector, '_http_json', side_effect=catalogue),
                  patch.object(collector, '_download_original', side_effect=download)):
                for row in selected:
                    directory = collector.external(task, 'ifind', 'report_query', dict(params,codes=row['thscode']),
                                                   {'access_token':'OFFLINE_TEST'})
                    task['source_records'].append(dict(directory, question_ids=['q1']))
                    report = collector.external(task, 'ifind', 'filing_original',
                        dict(catalog_id=directory['id'], source_id=row['seq']), {})
                    task['source_records'].append(dict(report, question_ids=['q1']))
            return task
        finally:
            agent.close_runtime()


def fee_task(*, has_trading_code=True, multiple_catalogue_rows=False):
    return deepcopy(_source_task(has_trading_code, multiple_catalogue_rows))


def read_fee_task(task):
    reports = [row for row in task['source_records'] if row['kind']=='filing']
    requests = [dict(artifact_id=row['id'], snapshot_hash=row['hash'], queries=[],
        parts=[dict(part=part['locator'],start=0,end=len(part['text']))
               for part in row['snapshot']['material']['parts']],
        question_ids=['q1'], reason='逐段核对期间、份额与费率') for row in reports]
    return read(task, requests, binding(task), [row['id'] for row in reports])
