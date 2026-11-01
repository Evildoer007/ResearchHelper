"""Bounded supplier diagnostic before registering an App tool.

The official HTTP example names p03425 but requests p03291 columns. Its SDK
example names p03291 for the same block. Query both explicit shapes once,
for the documented SSE50 block only. No automatic fallback, model, SDK login,
source adoption, strategy, report, installed App or completeness approval.
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import urllib.request

from tests import ROOT
from tests.accept_one_sentence_research import PRODUCTION, production_digest
from tests.accept_source_question_bound_pmi_chat import source_inventory
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, charge, fingerprint, now
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import business_payload
from researchhelper.settings import DataSettings
from researchhelper.source_transport import IsolatedEvidenceCollector

CASE = 'ifind-index-pool-shapes-20261018-v2'
DOCUMENTATION = 'https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/example.html'
INDEX_DOCUMENTATION = 'https://www.sse.com.cn/market/sseindex/indexlist/index.shtml?classid=010202'
REPORTS = ('p03425', 'p03291')


def parameters(report):
    if report not in REPORTS:
        raise ContractError('仅核对本次两个已声明的官方示例报表名称')
    return dict(reportname=report, functionpara=dict(date='20260930', blockname='001005260', iv_type='allcontract'),
        outputpara='p03291_f001,p03291_f002,p03291_f003,p03291_f004')


def inventory(payload):
    """Describe literal response layout, never assign meanings to unknown fields."""
    if not isinstance(payload, dict):
        return dict(shape='not_object')
    tables = payload.get('tables')
    return dict(errorcode=payload.get('errorcode'), top_fields=list(payload),
        tables_type=type(tables).__name__, tables=[dict(keys=list(table),
            table_type=type(table.get('table')).__name__,
            field_lengths={key:len(value) if isinstance(value,list) else None
                for key,value in table.get('table',{}).items()} if isinstance(table.get('table'),dict) else {},
            row_count=len(table['table']) if isinstance(table.get('table'),list) else None)
            for table in tables if isinstance(table,dict)] if isinstance(tables,list) else [])


def retained_response(public):
    """An API snapshot is JSON, not a parsed material original or evidence."""
    content = json.dumps(public,ensure_ascii=False,sort_keys=True).encode()
    return dict(status='response_retained',inventory=inventory(public),business_snapshot=public,
        business_sha256=hashlib.sha256(content).hexdigest(),research_evidence=False)


def run():
    descriptor = os.open(ROOT/'result/acceptance'/(CASE+'.guard'), os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    os.close(descriptor)
    folder = Path(tempfile.mkdtemp(prefix='ifind-index-pool-',dir=ROOT/'result/acceptance'))
    folder.chmod(0o700)
    target = folder/'receipt.json'
    before, sources_before = production_digest(), source_inventory()
    receipt = dict(status='running', scope='explicit_http_shape_diagnostics_only',
        documentation=DOCUMENTATION, index_documentation=INDEX_DOCUMENTATION,
        autonomous_research_accepted=False, installed_app_accepted=False, numeric_evidence_accepted=False,
        complete_constituents_accepted=False, historical_availability_accepted=False,
        model_calls=0, auto_retry=False, auto_fallback=False, production_registry_changed=False, results=[])
    agent = collector = task = None
    refresh = access = ''
    def save():
        target.write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
    save()
    try:
        settings = DataSettings(PRODUCTION)
        config = settings._configuration()
        reference = config['ifind']['refresh_token']
        refresh = (settings.secrets.resolve(reference,'ifind-refresh_token') if 'schema' in config and reference
                   else settings._credential(reference or ''))
        if not refresh:
            raise ContractError('ResearchHelper未保存独立iFinD凭据')
        archive = ResearchArchive(folder/'store')
        agent = ResearchAgent(archive,ROOT,offline=False,require_argument_support=True)
        task = agent.create('仅核对上证50指数000016.SH在2026年9月30日的成分证券原始记录；'
            '两个报表名是供应商示例形状诊断，不进行研究分析或推荐。',entry='指数',objects=['000016.SH'],
            research_mode='current', limits=dict(data_requests=3,model_calls=1,seconds=120))
        collector = IsolatedEvidenceCollector(offline=False,journal=archive.save)
        access = collector._resolve_ifind_access(task,refresh)
        receipt['task_id'] = task['id']
        for report in REPORTS:
            params = parameters(report)
            charge(task['ledger'],'data_requests')
            event = dict(kind='data_requests',source='ifind',operation='data_pool',batch=task['batch'],
                fingerprint=fingerprint(dict(source='ifind',operation='data_pool',parameters=params)),
                attempt=1,status='已派发',time=now(),diagnostic_only=True)
            task['ledger']['dispatches'].append(event)
            archive.save(task)
            row = dict(parameters=params,status='dispatching')
            receipt['results'].append(row)
            save()
            request = urllib.request.Request('https://quantapi.51ifind.com/api/v1/data_pool',
                data=json.dumps(params).encode(),headers={'Content-Type':'application/json','ifindlang':'cn'},method='POST')
            request.add_unredirected_header('access_token',access)
            started = time.monotonic()
            try:
                payload = collector._http_json(task,request,30,event)
                public = business_payload(payload,(access,refresh))
                row.update(retained_response(public))
                event['status'] = '成功' if isinstance(public,dict) and public.get('errorcode') in (0,'0') else '业务失败'
            except ContractError as error:
                row.update(status='failed',error=str(error))
                event['status'] = '失败'
            finally:
                task['ledger']['used']['seconds'] += max(0,time.monotonic()-started)
                archive.save(task)
                save()
        receipt['status'] = 'diagnostics_complete_not_app_acceptance'
    except ContractError as error:
        receipt.update(status='diagnostics_failed',error=str(error))
    except Exception as error:
        receipt.update(status='diagnostics_failed',error_type=type(error).__name__)
        raise
    finally:
        if collector is not None:
            collector._ifind_access_cache = None
        if agent is not None:
            for worker in agent.close_runtime():
                worker.join(timeout=5)
                assert not worker.is_alive()
        refresh = access = ''
        if task is not None:
            receipt['ledger'] = task['ledger']
        closed = task is None or all(not event.get('network_request_started')
            or event.get('network_worker_reaped') is True for event in task['ledger']['dispatches'])
        receipt.update(source_unchanged=sources_before==source_inventory(),
            production_unchanged=before==production_digest(),owned_runtime_closed=closed)
        save()
    assert receipt['source_unchanged'] and receipt['production_unchanged'] and receipt['owned_runtime_closed']
    print(json.dumps(dict(receipt=str(target),status=receipt['status'],results=[dict(
        report=item['parameters']['reportname'],status=item['status'],inventory=item.get('inventory'))
        for item in receipt['results']]),ensure_ascii=False),flush=True)
    return target
