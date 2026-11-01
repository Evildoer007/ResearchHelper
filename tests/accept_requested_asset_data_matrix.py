"""Fresh bounded source checks, followed by genuinely autonomous questions.

No production edits, supplied model plans, report generation or silent data
repair. Explicit adapter checks and question-to-data checks remain separate.
Run as an independent acceptance module; there are no command-line options.
"""
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from tests import ROOT
from tests.accept_one_sentence_research import PRODUCTION, production_digest
from tests.accept_source_question_bound_pmi_chat import source_inventory
from tests.probe_multitype_acquisition_current import inspect_series
from tests.probe_data_acquisition_current import material_inventory
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.settings import DataSettings
from researchhelper.source_transport import IsolatedEvidenceCollector
from researchhelper.object_discovery import discovery_response
from researchhelper.index_sources import OUTPUT, PROFILES, index_response
from researchhelper.market_series import SERIES_FAMILIES, series_settings
from researchhelper.evidence import validate_source_request

EXPLICIT_QUESTION = '独立检查个股、港股、ETF、指数、证券行业、算力产业链、宏观和事件的原始数据，不分析或生成报告。'


def write_receipt(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def explicit_checks(output, *, topics_only=False):
    before, source_before = production_digest(), source_inventory()
    record = dict(scope='fresh_explicit_adapter_checks', model_calls=0,
        autonomous_chat_accepted=False, financial_approval=False, results=[])
    receipt = output/'explicit-receipt.json'
    archive = ResearchArchive(output/'store')
    agent = ResearchAgent(archive, ROOT, offline=False, require_argument_support=True)
    collector = IsolatedEvidenceCollector(offline=False, journal=archive.save)
    collector.original_keeper = archive.retain_original
    task = agent.create(EXPLICIT_QUESTION,
        entry='混合', research_mode='current',
        objects=['600519.SH','300750.SZ','0700.HK','510300.SH','159919.SZ','000300.SH','000016.SH'],
        limits=dict(data_requests=24, model_calls=1, seconds=300))
    credential = {}

    def dispatch(label, source, operation, parameters, inspect):
        row = dict(label=label, source=source, operation=operation, parameters=parameters)
        record['results'].append(row)
        try:
            validate_source_request(task,source,operation,parameters)
            row['local_request_validated']=True
            item = collector.external(task, source, operation, parameters, credential if source=='ifind' else {})
            if not any(s['id']==item['id'] for s in task['source_records']):
                task['source_records'].append(item)
            archive.save(task)
            row.update(status='returned', artifact_id=item['id'], snapshot_hash=item['hash'],
                       inspection=inspect(item))
            return item
        except Exception as error:
            row.update(status='failed', error_type=type(error).__name__,
                failures=[dict(operation=e.get('operation'), failure_code=e.get('failure_code'))
                    for e in task['ledger']['dispatches'][-2:] if e.get('failure_code')])
            return None
        finally:
            write_receipt(receipt, record)

    try:
        saved = DataSettings(PRODUCTION).read(private=True)
        credential['refresh_token'] = saved['ifind']['refresh_token']
        del saved
        prices=[] if topics_only else [('A股行情','600519.SH,300750.SZ'), ('港股行情','0700.HK'),
                ('沪深ETF行情','510300.SH,159919.SZ'), ('指数点位','000300.SH,000016.SH')]
        for label, codes in prices:
            params=dict(codes=codes, indicators='close', startdate='2026-10-12', enddate='2026-10-16',
                        functionpara=dict(Currency='RMB', CPS='1', Fill='Omit'))
            dispatch(label,'ifind','cmd_history_quotation',params,lambda s,p=params:inspect_series(s,p))
            if any(e.get('operation')=='get_access_token' and e.get('status')!='成功'
                   for e in task['ledger']['dispatches']):
                raise RuntimeError('iFinD鉴权失败，停止后续调用，不反复刷新')
        for code in (() if topics_only else ('510300.SH','159919.SZ')):
            params=dict(codes=code, startdate='2026-10-12', enddate='2026-10-16',
                functionpara=series_settings(task,code),
                indipara=[dict(indicator=field,indiparams=value)
                    for field,value in SERIES_FAMILIES['ETF'].items()])
            dispatch(code+'净值与份额','ifind','date_sequence',params,lambda s,p=params:inspect_series(s,p))
        params=dict(reportname='p03291', functionpara=dict(date='20261016',
            blockname=PROFILES['000016.SH']['blockname'],iv_type='allcontract'),outputpara=OUTPUT)
        if not topics_only:
            dispatch('上证50成分名单','ifind','data_pool',params,
                lambda s:dict(contract=index_response(task,params,s['snapshot']),weights_approved=False))
        for topic in ('证券行业股票','算力产业链股票'):
            params=dict(searchstring=topic,searchtype='stock')
            dispatch(topic,'ifind','smart_stock_picking',params,
                lambda s,p=params:dict(contract=discovery_response(task,p,s['snapshot']),
                    complete_universe_approved=False,financial_fields_approved=False))
        for channel in (() if topics_only else ('nbs_release','fed_fomc')):
            catalog=dispatch(channel+'目录','official','release_catalog',dict(channel=channel,page='0'),
                lambda s:dict(entries=len(s['snapshot'].get('entries',[]))))
            choices=catalog['snapshot'].get('entries',[]) if catalog else []
            if choices:
                selected=max(choices,key=lambda e:e.get('date',''))
                dispatch(channel+'实际目录原文','official','release_original',
                    dict(catalog_id=catalog['id'],release_id=selected['id']),material_inventory)
        record['status']='closed_with_explicit_scope'
    finally:
        for worker in agent.close_runtime():
            worker.join(timeout=5)
            assert not worker.is_alive()
        collector._ifind_access_cache=None
        collector._ifind_original_cache.clear()
        credential.clear()
        record.update(task_id=task['id'],ledger=task['ledger'],owned_runtime_closed=True,
            production_unchanged=before==production_digest(),source_unchanged=source_before==source_inventory())
        with gzip.open(output/'explicit-task.json.gz','wt',encoding='utf-8') as stream:
            json.dump(task,stream,ensure_ascii=False)
        record['retained_task_sha256']=hashlib.sha256((output/'explicit-task.json.gz').read_bytes()).hexdigest()
        write_receipt(receipt,record)
    assert record['production_unchanged'] and record['source_unchanged']
    return receipt


QUESTIONS = (
    ('broker', '核对中信证券2026年半年报营业收入、归属于母公司股东的净利润及五项主要业务营业收入，保留金额、单位、完整报告期和原件位置，不做计算、估值或投资判断。'),
    ('index_weights', '获取沪深300在2026年10月19日的完整成分及各自权重，保留权重日期、单位和来源；缺失的成分或权重不能补零，不做收益或归因分析。'),
    ('industry', '获取证券行业2026年半年报营业收入与归母净利润，用明确的行业分类和完整公司名单核对覆盖情况，不用业务分部收入代替公司收入，不做估值或投资判断。'),
)


def run():
    parent=ROOT/'result/acceptance'
    parent.mkdir(parents=True,exist_ok=True)
    output=Path(tempfile.mkdtemp(prefix='requested-asset-data-matrix-',dir=parent))
    output.chmod(0o700)
    receipt=output/'receipt.json'
    record=dict(status='running',scope='data_only_not_reports',all_asset_types_accepted=False,
                native_window_opened=False,autonomous_cases=[])
    write_receipt(receipt,record)
    record['explicit_receipt']=str(explicit_checks(output))
    write_receipt(receipt,record)
    from tests import accept_autonomous_data_phase as driver
    for name, question in QUESTIONS:
        # Change test inputs only; no service, response, evidence or verifier is mocked.
        with patch.object(driver,'CASE',output.name+'-'+name),patch.object(driver,'QUESTION',question):
            path=driver.run()
        child=json.loads(path.read_text())
        record['autonomous_cases'].append(dict(name=name,question=question,receipt=str(path),
            driver_status=child['status'],adopted_evidence=child.get('adopted_evidence'),
            gaps=child.get('gaps'),ledger=child.get('ledger',{}).get('used')))
        write_receipt(receipt,record)
    record['status']='closed_pending_independent_required_field_review'
    write_receipt(receipt,record)
    print(json.dumps(dict(receipt=str(receipt),status=record['status']),ensure_ascii=False),flush=True)
    return receipt


if __name__=='__main__':
    run()
