"""Bounded data-only diagnostic, never a one-sentence research acceptance.

Requests below are explicit diagnostic inputs, not model-selected plans.
Use only RH's saved iFinD credential, retain raw responses and originals,
and leave analysis, financial approval, App installation and reports untouched.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

from tests import ROOT
from tests.accept_one_sentence_research import PRODUCTION, production_digest
from tests.accept_source_question_bound_pmi_chat import source_inventory
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import (IFIND_REPORT_FIELDS, identity_resolutions,
    validate_filing_original_request)
from researchhelper.market_series import SERIES_FAMILIES, series_family, series_settings
from researchhelper.settings import DataSettings
from researchhelper.source_transport import IsolatedEvidenceCollector

CASE = 'data-acquisition-current-20261017-v2'
CODES = ('600519.SH', '510500.SH', '000300.SH')
BEGIN, END = '2026-09-28', '2026-09-30'


def material_inventory(source):
    """Filing parts live inside material, official HTML parts at snapshot root."""
    snapshot = source.get('snapshot', {})
    material = snapshot.get('material', snapshot)
    parts = material.get('parts', [])
    readable = [part for part in parts if isinstance(part, dict)
                and isinstance(part.get('text'), str) and part['text'].strip()]
    return dict(parsed_parts=len(parts), readable_parts=len(readable),
        characters=sum(len(part['text']) for part in readable),
        located_readable_parts=sum(bool(part.get('locator')) for part in readable),
        extract_version=material.get('extract_version'), status=material.get('status'))


def table_inventory(source):
    """Require dated, code-bound aligned columns; never infer units or fill gaps."""
    rows = []
    for table in source.get('snapshot', {}).get('tables', []):
        if not isinstance(table, dict):
            continue
        dates, fields = table.get('time'), table.get('table')
        if not isinstance(dates, list) or not isinstance(fields, dict):
            continue
        columns = []
        for field, values in fields.items():
            if not isinstance(values, list):
                continue
            numbers = [value for value in values if type(value) in (int, float)
                       and math.isfinite(value)]
            columns.append(dict(field=field, count=len(values), numeric_count=len(numbers),
                missing_count=sum(value is None for value in values),
                aligned=len(dates) == len(values)))
        rows.append(dict(code=table.get('thscode'), dates=dates, columns=columns))
    return rows


def run():
    guard = ROOT / 'result/acceptance' / (CASE + '.guard')
    descriptor = os.open(guard, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    output = Path(tempfile.mkdtemp(prefix='data-acquisition-current-', dir=guard.parent))
    output.chmod(0o700)
    target = output / 'receipt.json'
    before, source_before = production_digest(), source_inventory()
    record = dict(status='running', scope='explicit_adapter_diagnostics_only',
        model_calls_authorized=0, supplied_diagnostic_requests=True,
        autonomous_question_to_data_accepted=False, all_task_types_accepted=False,
        analysis_started=False, strategy_accepted=False, financial_approval=False,
        native_window_opened=False, installed_app_modified=False, results=[])
    agent, task, collector, credentials = None, None, None, {}

    def save():
        target.write_text(json.dumps(record, ensure_ascii=False, indent=2))

    def dispatch(label, operation, parameters):
        row = dict(label=label, operation=operation, status='dispatching')
        record['results'].append(row)
        save()
        try:
            source = collector.external(task, 'ifind', operation, parameters,
                                        credentials['ifind'])
            if not any(item['id'] == source['id'] for item in task['source_records']):
                task['source_records'].append(source)
            agent.archive.save(task)
            row.update(status=source['status'], artifact_id=source['id'],
                       snapshot_sha256=source['hash'])
            if operation in ('cmd_history_quotation', 'date_sequence'):
                inventory = table_inventory(source)
                expected = parameters['codes'].split(',')
                returned = {item['code'] for item in inventory}
                row.update(table_inventory=inventory, requested_codes_present=set(expected) <= returned,
                    dated_numeric_columns_available=bool(inventory) and set(expected) <= returned
                        and all(item['dates'] and item['columns']
                            and all(column['aligned'] and column['numeric_count']
                                    for column in item['columns']) for item in inventory),
                    unit_approved=False, historical_vintage_approved=False)
            if operation == 'report_query':
                row['catalog_entries'] = len(source.get('catalog_entries', []))
            if operation == 'filing_original':
                material = source.get('snapshot', {}).get('material', {})
                row.update(stages=source.get('stages', {}),
                    material_inventory=material_inventory(source),
                    retained_originals=len(agent.archive.retained_originals(task)))
            return source
        except ContractError as error:
            # Transport errors are already sanitized; never include vendor errmsg.
            row.update(status='failed', error=str(error)[:500])
            return None
        finally:
            save()

    save()
    try:
        settings = DataSettings(PRODUCTION)
        saved = settings._configuration()
        reference = saved['ifind']['refresh_token']
        refresh = (settings.secrets.resolve(reference, 'ifind-refresh_token')
                   if 'schema' in saved and reference else settings._credential(reference or ''))
        if not refresh:
            raise RuntimeError('missing_researchhelper_ifind_refresh')
        credentials = {'ifind': {'refresh_token': refresh}}
        refresh = ''
        archive = ResearchArchive(output / 'store')
        agent = ResearchAgent(archive, ROOT, offline=False, source_credentials=credentials,
            isolate_sources=True, isolate_materials=True, require_argument_support=True)
        # This explicitly scoped adapter probe is not a running research task.
        # The engine journal correctly refuses an unstarted study. Use the
        # collector's standalone diagnostic contract, not a disabled engine
        # cancellation guard or a forged running status.
        collector = IsolatedEvidenceCollector(offline=False, journal=archive.save)
        collector.original_keeper = archive.retain_original
        task = agent.create('仅核对600519.SH、510500.SH、000300.SH在2026年9月28日至30日的行情、'
            '估值或净值，以及同花顺名称映射和600519.SH于2026年8月披露的公告原件。'
            '这是取数诊断，不生成分析、策略或报告。', entry='混合',
            objects=[*CODES, '同花顺'], research_mode='current',
            limits=dict(data_requests=9, model_calls=1, seconds=240))
        record['task_id'] = task['id']
        dispatch('名称映射', 'get_thscode', dict(secname='同花顺', functionpara=dict(
            mode='secname', sectype='', market='', tradestatus='0', isexact='1')))
        auth_failed = any(event.get('operation') == 'get_access_token' and event['status'] != '成功'
                          for event in task['ledger']['dispatches'])
        if not auth_failed:
            record['identity_resolutions'] = identity_resolutions(task)
            dispatch('个股、ETF、指数日行情', 'cmd_history_quotation', dict(codes=','.join(CODES),
                indicators='close', startdate=BEGIN, enddate=END,
                functionpara=dict(Currency='RMB', CPS='1', Fill='Omit')))
            for code in CODES:
                family = SERIES_FAMILIES[series_family(code)]
                field = next(iter(family))
                dispatch(series_family(code) + '日期序列', 'date_sequence', dict(codes=code,
                    startdate=BEGIN, enddate=END, functionpara=series_settings(task, code),
                    indipara=[dict(indicator=field, indiparams=family[field])]))
            directory = dispatch('个股公告目录', 'report_query', dict(codes=CODES[0],
                functionpara={}, beginrDate='2026-08-01', endrDate='2026-08-31',
                outputpara=','.join(field + ':Y' for field in IFIND_REPORT_FIELDS)))
            if directory:
                for row in directory.get('catalog_entries', []):
                    parameters = dict(catalog_id=directory['id'], source_id=row.get('source_id'))
                    try:
                        validate_filing_original_request(task, parameters)
                    except ContractError:
                        continue
                    dispatch('目录绑定原件下载与解析', 'filing_original', parameters)
                    break
        record['status'] = 'diagnostic_complete_not_all_types_accepted'
    except BaseException as error:
        record.update(status='diagnostic_failed', error_type=type(error).__name__)
        raise
    finally:
        if task is not None:
            record['ledger'] = task['ledger']
            record['model_calls'] = task['ledger']['used']['model_calls']
            agent.archive.save(task)
            assert record['model_calls'] == 0
        if agent is not None:
            for worker in agent.close_runtime():
                worker.join(timeout=5)
                assert not worker.is_alive()
            record['owned_runtime_closed'] = True
        if collector is not None:
            collector._ifind_access_cache = None
            collector._ifind_original_cache.clear()
        for values in credentials.values():
            values.clear()
        credentials.clear()
        record['production_unchanged'] = before == production_digest()
        record['source_unchanged'] = source_before == source_inventory()
        save()
        print(json.dumps(dict(status=record['status'], receipt=str(target),
                              model_calls=record.get('model_calls')), ensure_ascii=False), flush=True)
    assert record['production_unchanged'] and record['source_unchanged']
    return target
