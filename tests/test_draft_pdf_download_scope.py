"""Explicit PDF download limits leave structured and HTML research usable."""
import socket
from unittest.mock import patch

from lxml import html

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import charge, fingerprint, now
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import _record_current_acquisition, data_request_contract, snapshot_record
from researchhelper.official_sources import selection_references
from tests import ROOT
from tests.test_announcement_partial_delivery import ifind_query, ifind_row
from tests.test_draft_account_policy import quote_request
from tests.test_independent_official_sources import CN_ISSUER, PBC_URL, cn_raw, params


def test_host_draft_keeps_catalogues_html_and_quotes_without_downloading_pdf(tmp_path):
    archive = ResearchArchive(tmp_path)
    agent = ResearchAgent(archive, ROOT, offline=False, draft_first=True)
    try:
        task = agent.create('核对600519.SH的收盘价和披露，查看央行政策，不下载PDF、不扩展标的。',
                            research_mode='current')
        filing = snapshot_record(task, 'ifind', 'report_query', ifind_query('600519.SH'),
                                 {'errorcode': 0, 'data': [ifind_row()]})
        task['source_records'].append(filing)
        pbc_parameters = dict(channel='pbc_news', page='0')
        for parameters, payload, issuer in (
            (params(), cn_raw(), CN_ISSUER),
            (pbc_parameters, f'<a href="{PBC_URL}">央行政策样例</a>'.encode(), None),
        ):
            digest = fingerprint(dict(source='official', operation='release_catalog', parameters=parameters))
            task['source_records'].append(agent.collector._official_catalogue_record(
                task, parameters, payload, issuer, now(), digest))
        archive.save(task)
        official_refs = {row['channel']: row['selection_ref']
                         for row in selection_references(task, with_details=True)}
        question_ids = [task['questions'][0]['id']]
        quote = quote_request()
        requests = [dict(id='ifind_pdf', source='ifind', operation='filing_original',
                         parameters=dict(catalog_id=filing['id'], source_id='selected'),
                         question_ids=question_ids, selection_reason='核对披露正文。')]
        for channel in ('cninfo', 'pbc_news'):
            requests.append(dict(id=channel, source='official', operation='release_original',
                                 parameters=dict(selection_ref=official_refs[channel]),
                                 question_ids=question_ids, selection_reason='核对原文。'))
        requests.append(quote)
        collector_external = agent.collector.external
        submitted = []

        def collect(task, source, operation, parameters, credentials):
            submitted.append((source, operation))
            if operation != 'cmd_history_quotation':
                return collector_external(task, source, operation, parameters, credentials)
            charge(task['ledger'], 'data_requests')
            record = snapshot_record(task, source, operation, parameters,
                dict(errorcode=0, tables=[dict(thscode='600519.SH', time=['2026-10-15'], table=dict(close=[112]))]))
            dispatch = dict(kind='data_requests', source=source, operation=operation, status='成功',
                time=now(), fingerprint=record['request_hash'], batch=task['batch'], transport_isolated=True,
                network_request_started=True, network_worker_reaped=True)
            task['ledger']['dispatches'].append(dispatch)
            return _record_current_acquisition(task, record, dispatch)

        body = b'<title>Policy</title><div id="zoom"><p>Retained policy text.</p></div>'
        with (patch.object(socket.socket, 'connect', side_effect=AssertionError('External network forbidden')),
              patch.object(agent.collector, 'external', side_effect=collect),
              patch('researchhelper.source_transport.official_html', return_value=body) as html_download,
              patch('researchhelper.source_transport.official_announcement',
                    side_effect=AssertionError('Official PDF dispatch')),
              patch.object(agent.collector, '_download_original',
                           side_effect=AssertionError('iFinD PDF dispatch'))):
            finished = agent.start(task['id'], dict(data_requests=requests, draft_report=dict(
                title='收盘价与政策核对', conclusion='保留实际取得的数据。',
                logics=[dict(title='实际读数', analysis='收盘价112元。', data_refs=['D1'])])))

        assert submitted == [('official', 'release_original'), ('ifind', 'cmd_history_quotation')]
        html_download.assert_called_once()
        assert html_download.call_args.args[1] == PBC_URL
        assert finished['ledger']['used']['data_requests'] == 2
        assert len(finished['ledger']['dispatches']) == 2
        assert [row['status'] for row in finished['draft_source_attempts']] == [
            '参数未通过', '参数未通过', '已提交', '已提交']
        assert all('不下载PDF' in row['error'] for row in finished['draft_source_attempts'][:2])
        assert finished['delivery']
        assert [row['value'] for row in finished['draft_report']['facts']] == [112]
        paper = html.document_fromstring(archive.report(task['id'], finished['version'])['html'])
        assert '112' in paper.xpath('//main')[0].text_content()
        assert '未派发PDF' not in paper.xpath('//main')[0].text_content()

        tools = {(row['source'], row['operation']): row
                 for row in data_request_contract(finished, first_report=True)['tools']}
        assert ('ifind', 'filing_original') not in tools
        assert ('ifind', 'report_query') in tools
        assert ('official', 'release_catalog') in tools
        assert tools['official', 'release_original']['available_parameter_sets'] == [
            dict(selection_ref=official_refs['pbc_news'])]
    finally:
        agent.close_runtime()
