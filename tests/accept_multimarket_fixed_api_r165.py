"""One bounded live fixed-API matrix, without model calls or PDF downloads.

This certifies returned cells, not autonomous planning, financial units,
issuer identity, historical publication or investment conclusions.
"""
import gzip
import hashlib
import json
import math
from pathlib import Path
import tempfile

from tests import ROOT
from tests.accept_one_sentence_research import PRODUCTION, production_digest
from tests.probe_multitype_acquisition_current import inspect_series
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import validate_source_request
from researchhelper.market_series import SERIES_FAMILIES, series_settings
from researchhelper.settings import DataSettings
from researchhelper.source_transport import IsolatedEvidenceCollector

CASE = 'multimarket-fixed-api-r165-20261024-v3'
CODES = ['600519.SH', '300750.SZ', '0700.HK', '510300.SH', '159919.SZ', '000300.SH', '000016.SH']
FIELDS = ['ths_revenue_stock', 'ths_np_stock', 'ths_ncf_from_oa_stock']


def create_diagnostic_task(archive):
    agent = ResearchAgent(archive, ROOT, offline=False, require_argument_support=True)
    task = agent.create('独立检查多市场行情、ETF净值及两家公司的固定财务API原值，不分析或生成报告。',
        entry='混合', objects=CODES, research_mode='current',
        limits=dict(data_requests=8, model_calls=1, seconds=240))
    validate_source_request(task, 'ifind', 'get_limit_policy', {})
    return agent, task


def inspect_financial(source, parameters):
    tables = source['snapshot'].get('tables', [])
    expected = set(parameters['codes'].split(','))
    actual = [table.get('thscode') for table in tables]
    rows = []
    for table in tables:
        values = {field: table.get('table', {}).get(field) for field in FIELDS}
        usable = all(isinstance(column, list) and len(column) == 1
            and type(column[0]) in (int, float) and math.isfinite(column[0])
            for column in values.values())
        rows.append(dict(code=table.get('thscode'), usable=usable, values=values))
    return dict(usable=bool(rows) and len(actual) == len(set(actual))
        and set(actual) == expected and all(row['usable'] for row in rows), tables=rows,
        supplied_report_period='20260630', supplied_report_type='1',
        unit_approved=False, financial_context_approved=False)


def remaining_requests(task):
    requests = []
    for code in ('510300.SH', '159919.SZ'):
        requests.append((code+'净值及份额', 'date_sequence',
            dict(codes=code, startdate='2026-10-12', enddate='2026-10-16',
                functionpara=series_settings(task, code),
                indipara=[dict(indicator=field, indiparams=value)
                    for field, value in SERIES_FAMILIES['ETF'].items()]), inspect_series))
    requests.append(('两家公司固定财务原值', 'basic_data_service',
        dict(codes='600519.SH,300750.SZ', indipara=[dict(indicator=field,
            indiparams=['20260630', '1']) for field in FIELDS]), inspect_financial))
    return requests


def run():
    import os
    guard = ROOT/'result/acceptance'/f'{CASE}.guard'
    descriptor = os.open(guard, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    output = Path(tempfile.mkdtemp(prefix='multimarket-fixed-api-', dir=ROOT/'result/acceptance'))
    output.chmod(0o700)
    before = production_digest()
    record = dict(case=CASE, status='running', model_calls=0, retries=0, pdf_downloads=0,
        supplied_diagnostic_requests=True, autonomous_chat_accepted=False,
        financial_research_accepted=False, current_issuer_identity_approved=False,
        historical_publication_approved=False, results=[], production_unchanged=False,
        scope='remaining_etf_and_financial_requests_only',
        prior_price_receipt='result/acceptance/multimarket-fixed-api-6qk1u24x/receipt.json',
        prior_task_reopened=False, prices_repeated=False)
    credentials = {}
    task = None

    def save():
        (output/'receipt.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))

    try:
        with tempfile.TemporaryDirectory(prefix='private-', dir=output) as temporary:
            archive = ResearchArchive(Path(temporary)/'store')
            agent, task = create_diagnostic_task(archive)
            collector = IsolatedEvidenceCollector(offline=False, journal=archive.save)
            collector.original_keeper = archive.retain_original
            try:
                credentials['refresh_token'] = DataSettings(PRODUCTION).read(private=True)['ifind']['refresh_token']

                def dispatch(label, operation, parameters, inspect):
                    validate_source_request(task, 'ifind', operation, parameters)
                    source = collector.external(task, 'ifind', operation, parameters, credentials)
                    task['source_records'].append(source)
                    archive.save(task)
                    assert source['hash'] == fingerprint(source['snapshot'])
                    event = task['ledger']['dispatches'][source['acquisition']['dispatch_index']]
                    assert event['status'] == '成功' and event['network_request_started']
                    assert event['response_body_complete'] and not event.get('synthetic_test_transport', False)
                    assert event['response_snapshot_hash'] == source['hash']
                    assert event['fingerprint'] == source['request_hash'] == source['acquisition']['request_hash']
                    result = inspect(source, parameters)
                    record['results'].append(dict(label=label, operation=operation,
                        artifact_id=source['id'], snapshot_hash=source['hash'], inspection=result))
                    save()

                dispatch('本任务账号策略', 'get_limit_policy', {},
                         lambda source, params: dict(usable=bool(source['snapshot'])))
                for request in remaining_requests(task):
                    dispatch(*request)
                record['status'] = ('raw_matrix_passed_not_research_accepted'
                    if all(row['inspection']['usable'] for row in record['results']) else 'raw_matrix_has_gaps')
            finally:
                credentials.clear()
                record['owned_workers_stopped'] = not agent.close_runtime()
                task['status'] = '已取消'
                task['gaps'].append('仅固定API取数诊断，未执行自主规划、分析或报告。')
                archive.save(task)
                raw = json.dumps(task, ensure_ascii=False).encode()
                with gzip.open(output/'task.json.gz', 'wb') as stream:
                    stream.write(raw)
                record.update(task_id=task['id'], task_sha256=hashlib.sha256(raw).hexdigest(),
                    used=task['ledger']['used'], temporary_credentials_cleared=not credentials)
                assert task['ledger']['used']['model_calls'] == 0
                assert not any(source['operation'] in ('filing_original', 'release_original')
                    for source in task['source_records'])
        record['temporary_store_removed'] = not any(output.glob('private-*'))
    except Exception as error:
        record.update(status='failed', error_type=type(error).__name__, error=str(error)[:240])
    finally:
        credentials.clear()
        record['production_unchanged'] = production_digest() == before
        if not record['production_unchanged']:
            record['status'] = 'failed'
        save()
        print(json.dumps(dict(status=record['status'], receipt=str(output/'receipt.json'),
            usable=[row['label'] for row in record['results'] if row['inspection']['usable']],
            used=record.get('used'), error=record.get('error')), ensure_ascii=False))
    return output/'receipt.json'


if __name__ == '__main__':
    run()
