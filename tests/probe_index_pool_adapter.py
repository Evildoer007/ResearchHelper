"""Actual shared adapter and one returned-member lookup, not autonomous research."""
import json
import os
from pathlib import Path
import tempfile

from tests import ROOT
from tests.accept_one_sentence_research import PRODUCTION, production_digest
from tests.accept_source_question_bound_pmi_chat import source_inventory
from tests.probe_data_acquisition_current import table_inventory
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import available_security_codes, current_query_reference
from researchhelper.index_sources import OUTPUT
from researchhelper.settings import DataSettings
from researchhelper.source_transport import IsolatedEvidenceCollector

CASE = 'index-pool-shared-adapter-20261018-v1'


def quote_inventory_matches(rows, selected):
    if not isinstance(rows,list) or len(rows)!=1:
        return False
    row=rows[0]
    return (row.get('code')==selected and row.get('dates')==['2026-09-30']
        and row.get('columns')==[dict(field='close',count=1,numeric_count=1,missing_count=0,aligned=True)])


def run():
    descriptor=os.open(ROOT/'result/acceptance'/(CASE+'.guard'),os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    os.close(descriptor)
    folder=Path(tempfile.mkdtemp(prefix='index-pool-adapter-',dir=ROOT/'result/acceptance'))
    folder.chmod(0o700)
    target=folder/'receipt.json'
    production_before, source_before=production_digest(),source_inventory()
    result=dict(status='running',scope='explicit_shared_adapter_diagnostics',supplied_parameters=True,
        member_selection='first_actual_response_row_for_one_explicit_lookup',model_calls=0,
        autonomous_question_to_data_accepted=False,all_indices_accepted=False,weights_accepted=False,
        complete_constituents_accepted=False,strategy_accepted=False,installed_app_accepted=False)
    agent=collector=task=None
    credentials={}
    def save():
        target.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    save()
    try:
        settings=DataSettings(PRODUCTION)
        config=settings._configuration()
        reference=config['ifind']['refresh_token']
        refresh=(settings.secrets.resolve(reference,'ifind-refresh_token') if 'schema' in config and reference
                 else settings._credential(reference or ''))
        if not refresh:
            raise ContractError('ResearchHelper未保存独立iFinD凭据')
        credentials={'refresh_token':refresh}
        refresh=''
        archive=ResearchArchive(folder/'store')
        agent=ResearchAgent(archive,ROOT,offline=False,require_argument_support=True)
        task=agent.create('仅核对上证50指数000016.SH在2026年9月30日的成分原始名单，'
            '再选一只实际返回成分查询当日收盘价原始列；独立接口诊断，不生成分析、策略或报告。',
            entry='指数',objects=['000016.SH'],research_mode='current',
            limits=dict(data_requests=3,model_calls=1,seconds=180))
        collector=IsolatedEvidenceCollector(offline=False,journal=archive.save)
        pool=collector.external(task,'ifind','data_pool',dict(reportname='p03291',
            functionpara=dict(date='20260930',blockname='001005260',iv_type='allcontract'),outputpara=OUTPUT),credentials)
        task['source_records'].append(pool)
        archive.save(task)
        contract=pool.get('index_contract',{})
        if pool.get('status')!='成功待核验' or not contract.get('members'):
            raise ContractError('共享适配器尚未取得可定位成分行')
        if current_query_reference(task,pool) is None:
            raise ContractError('成分响应未取得真实同任务查询留痕')
        selected=contract['members'][0]['code']
        if selected not in available_security_codes(task):
            raise ContractError('实际返回成分尚未开放同任务后续查询')
        quote=collector.external(task,'ifind','cmd_history_quotation',dict(codes=selected,indicators='close',
            startdate='2026-09-30',enddate='2026-09-30',functionpara=dict(Currency='RMB',CPS='1',Fill='Omit')),credentials)
        task['source_records'].append(quote)
        archive.save(task)
        tables=table_inventory(quote)
        if (quote.get('status')!='成功待核验' or current_query_reference(task,quote) is None
                or not quote_inventory_matches(tables,selected)):
            raise ContractError('实际成分行情未取得所选代码、日期及非缺失原始收盘价列')
        result.update(status='shared_adapter_sample_passed',task_id=task['id'],index_source=pool['id'],
            index_snapshot_hash=pool['hash'],index_contract=contract,selected_member=selected,
            quote_source=quote['id'],quote_status=quote['status'],quote_tables=tables,
            adopted_evidence=len(task['evidence']),financial_numeric_approval=False)
    except ContractError as error:
        result.update(status='shared_adapter_sample_failed',error=str(error))
    except Exception as error:
        result.update(status='shared_adapter_sample_failed',error_type=type(error).__name__)
        raise
    finally:
        if collector is not None:
            collector._ifind_access_cache=None
        if agent is not None:
            for worker in agent.close_runtime():
                worker.join(timeout=5)
                assert not worker.is_alive()
        credentials.clear()
        if task is not None:
            result['ledger']=task['ledger']
            (folder/'task.json').write_text(json.dumps(task,ensure_ascii=False,indent=2))
        closed=task is None or all(not event.get('network_request_started') or event.get('network_worker_reaped') is True
                                  for event in task['ledger']['dispatches'])
        result.update(source_unchanged=source_before==source_inventory(),production_unchanged=production_before==production_digest(),
            owned_runtime_closed=closed)
        save()
    assert result['source_unchanged'] and result['production_unchanged'] and result['owned_runtime_closed']
    print(json.dumps(dict(receipt=str(target),status=result['status'],selected_member=result.get('selected_member'),
        observed_rows=result.get('index_contract',{}).get('observed_rows'),quote_tables=result.get('quote_tables')),ensure_ascii=False),flush=True)
    return target
