"""Bounded real acquisition matrix, separate from research/analysis acceptance.

Uses the App's current source collector and RH's own saved iFinD credential.
Requests are explicit diagnostic inputs, not a model-generated research plan.
Only compact receipts and a compressed source archive survive the private Store.
"""
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile

from tests import ROOT
from tests.accept_one_sentence_research import PRODUCTION, production_digest
from tests.probe_data_acquisition_current import material_inventory
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import IFIND_REPORT_FIELDS, identity_resolutions
from researchhelper.financial_sources import financial_query_response
from researchhelper.index_sources import OUTPUT, PROFILES, index_response
from researchhelper.market_series import SERIES_FAMILIES, series_family, series_settings
from researchhelper.object_discovery import discovery_response
from researchhelper.settings import DataSettings
from researchhelper.source_transport import IsolatedEvidenceCollector

BEGIN, END = '2026-10-12', '2026-10-16'
CODES = ('600519.SH', '300750.SZ', '00700.HK', '510300.SH', '159919.SZ', '000300.SH', '000016.SH')
OUTPUT_DIR = ROOT/'result/acceptance/data-acquisition-matrix-current'


def diagnostic_agent(archive):
    """Match current App task policies rather than the legacy kernel default."""
    return ResearchAgent(archive,ROOT,offline=False,require_argument_support=True,draft_first=True)


def inspect_series(source, parameters):
    expected = set(parameters['codes'].split(','))
    fields = (parameters['indicators'].split(',') if 'indicators' in parameters
              else [item['indicator'] for item in parameters['indipara']])
    rows = []
    for table in source.get('snapshot', {}).get('tables', []):
        dates, columns = table.get('time'), table.get('table')
        if not isinstance(dates, list) or not isinstance(columns, dict):
            rows.append(dict(code=table.get('thscode'), usable=False, issue='缺少日期或列'))
            continue
        cells = {key:columns.get(key) for key in fields}
        valid_dates = bool(dates) and all(isinstance(day, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}',day)
            and parameters['startdate'] <= day <= parameters['enddate'] for day in dates)
        valid_dates = valid_dates and len(dates) == len(set(dates))
        complete = all(isinstance(values, list) and len(values) == len(dates)
            and all(type(value) in (int,float) and math.isfinite(value) for value in values)
            for values in cells.values())
        rows.append(dict(code=table.get('thscode'), usable=valid_dates and complete,
            dates=dates, fields=fields, observed_rows=len(dates), values=cells,
            missing_cells=sum(value is None for values in cells.values() if isinstance(values,list) for value in values)))
    actual = [row['code'] for row in rows]
    return dict(usable=bool(rows) and len(actual)==len(set(actual)) and set(actual)==expected
        and all(row['usable'] for row in rows), tables=rows,
        unit_approved=False, continuous_trading_calendar_approved=False)


def inspect_financial(source, task, parameters):
    contract = financial_query_response(task, parameters, source['snapshot'])
    return dict(usable=contract.get('row_count')==1 and not contract.get('unmatched_fields')
        and not contract.get('missing_values'), contract=contract)


def run():
    output = OUTPUT_DIR
    output.mkdir(parents=True, exist_ok=False)
    output.chmod(0o700)
    before = production_digest()
    record = dict(status='running', scope='live_current_source_adapter_matrix',
        supplied_diagnostic_requests=True, autonomous_chat_accepted=False,
        model_calls=0, analysis_started=False, financial_approval=False,
        installed_app_accepted=False, production_modified=False, results=[], gaps=[])
    receipt = output/'receipt.json'
    credentials = {}
    agent = collector = task = None
    source_files = sorted(path for parent in ('core/src','modules','products/app')
        for path in (ROOT/parent).rglob('*') if path.is_file() and path.suffix in ('.py','.js','.css','.html','.swift'))
    source_hashes = {str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest() for path in source_files}

    def save():
        receipt.write_text(json.dumps(record,ensure_ascii=False,indent=2))

    with tempfile.TemporaryDirectory(prefix='rh-acquisition-matrix-') as temporary:
        try:
            settings = DataSettings(PRODUCTION)
            saved = settings._configuration()
            reference = saved['ifind']['refresh_token']
            refresh = (settings.secrets.resolve(reference,'ifind-refresh_token')
                if 'schema' in saved and reference else settings._credential(reference or ''))
            if not refresh:
                raise ValueError('ResearchHelper独立iFinD凭据缺失')
            credentials['refresh_token'] = refresh
            del refresh
            archive = ResearchArchive(Path(temporary)/'store')
            agent = diagnostic_agent(archive)
            collector = IsolatedEvidenceCollector(offline=False,journal=archive.save)
            collector.original_keeper = archive.retain_original
            task = agent.create('仅检查贵州茅台、宁德时代、腾讯控股、股票ETF、沪深300、上证50、证券行业、'
                '算力产业链、统计局宏观发布及FOMC声明的数据获取能力，不计算、不分析、不生成报告。',
                entry='混合',objects=list(CODES),research_mode='current',
                limits=dict(data_requests=48,model_calls=1,seconds=600))
            record.update(task_id=task['id'], cutoff=task['cutoff'], source_hashes=source_hashes,
                task_policy={name:task.get(name) for name in ('object_discovery_version','index_constituent_source_version')})
            save()

            def dispatch(label, family, operation, parameters, *, source='ifind', inspector=None):
                row = dict(label=label,family=family,source=source,operation=operation,
                    parameters=parameters,status='dispatching')
                record['results'].append(row)
                save()
                try:
                    item = collector.external(task,source,operation,parameters,credentials if source=='ifind' else {})
                    if not any(old['id']==item['id'] for old in task['source_records']):
                        task['source_records'].append(item)
                    archive.save(task)
                    row.update(status='returned',artifact_id=item['id'],snapshot_hash=item['hash'])
                    if inspector:
                        row['inspection']=inspector(item)
                        row['status']='usable_raw_sample' if row['inspection']['usable'] else 'returned_but_incomplete'
                    return item
                except Exception as error:
                    # Vendor exception text is not forwarded: it can carry a token.
                    row.update(status='failed',error_type=type(error).__name__,
                        failure_codes=[e.get('failure_code') for e in task['ledger']['dispatches'][-2:] if e.get('failure_code')])
                    return None
                finally:
                    save()

            for name in ('贵州茅台','宁德时代','腾讯控股'):
                dispatch(name+'名称映射','身份','get_thscode',dict(secname=name,
                    functionpara=dict(mode='secname',sectype='',market='',tradestatus='0',isexact='1')))
            record['identity_resolutions']=identity_resolutions(task)
            auth_failed=any(event.get('operation')=='get_access_token' and event.get('status')!='成功'
                for event in task['ledger']['dispatches'])
            if auth_failed:
                raise ValueError('iFinD鉴权未通过，不重复刷新')

            def quotes(label,family,codes):
                params=dict(codes=','.join(codes),indicators='open,high,low,close',startdate=BEGIN,enddate=END,
                    functionpara=dict(Currency='RMB',CPS='1',Fill='Omit'))
                return dispatch(label,family,'cmd_history_quotation',params,
                    inspector=lambda item:inspect_series(item,params))

            for label,family,codes in (('A股行情','个股',CODES[:2]),('港股行情','港股',[CODES[2]]),
                    ('沪深ETF行情','ETF',CODES[3:5]),('指数点位','指数',CODES[5:])):
                quotes(label,family,codes)
            for code in [*CODES[:2],*CODES[3:]]:
                family=series_family(code)
                params=dict(codes=code,startdate=BEGIN,enddate=END,functionpara=series_settings(task,code),
                    indipara=[dict(indicator=field,indiparams=params) for field,params in SERIES_FAMILIES[family].items()])
                dispatch(code+('净值与份额' if family=='ETF' else '估值序列'),family,'date_sequence',params,
                    inspector=lambda item,p=params:inspect_series(item,p))
            for code in CODES[:2]:
                params=dict(searchstring=code+'2026年半年报营业收入、净利润、经营活动产生的现金流量净额',searchtype='stock')
                dispatch(code+'财务结构化查询','个股','smart_stock_picking',params,
                    inspector=lambda item,p=params:inspect_financial(item,task,p))

            for code,family,title_pattern,keywords in (
                    ('600519.SH','个股',r'2026.*半年度报告$',('营业收入','净利润','经营活动产生的现金流量净额')),
                    ('510300.SH','ETF',r'2026.*(?:中期报告|半年度报告)$',('管理费','托管费','投资组合')),
                    ('00700.HK','港股',r'2026.*(?:中期|INTERIM)',('收入','收益','盈利','Revenue'))):
                params=dict(codes=code,functionpara={},beginrDate='2026-08-01',endrDate='2026-09-30',
                    outputpara=','.join(field+':Y' for field in IFIND_REPORT_FIELDS))
                catalog=dispatch(code+'公告目录',family,'report_query',params,
                    inspector=lambda item:dict(usable=bool(item.get('catalog_entries')),entries=len(item.get('catalog_entries',[])),
                        titles=[row.get('title') for row in item.get('catalog_entries',[])]))
                choices=[row for row in catalog.get('catalog_entries',[]) if re.search(title_pattern,row.get('title',''),re.I)] if catalog else []
                if not choices:
                    record['gaps'].append(dict(family=family,item=code+'指定半年报原件',reason='当前目录未找到匹配标题，未猜下载地址'))
                    continue
                selected=choices[0]
                def inspect_material(item, words=keywords):
                    inventory=material_inventory(item)
                    text=''.join(part.get('text','') for part in item['snapshot'].get('material',item['snapshot']).get('parts',[]))
                    found=[word for word in words if word in re.sub(r'\s+','',text)]
                    return dict(usable=inventory['readable_parts']>0 and bool(found),**inventory,requested_keywords=list(words),found_keywords=found,
                        financial_extraction_approved=False)
                dispatch(code+'半年报下载和解析',family,'filing_original',
                    dict(catalog_id=catalog['id'],source_id=selected['source_id']),inspector=inspect_material)

            for topic,family in (('证券','行业'),('算力','产业链')):
                params=dict(searchstring=topic+family+'股票',searchtype='stock')
                discovered=dispatch(topic+family+'候选',family,'smart_stock_picking',params,
                    inspector=lambda item,p=params:dict(usable=bool(discovery_response(task,p,item['snapshot'])['candidates']),
                        contract=discovery_response(task,p,item['snapshot'])))
                if discovered:
                    choices=discovery_response(task,params,discovered['snapshot'])['candidates']
                    if choices:
                        quotes(topic+family+'实际候选后续行情',family,[choices[0]['code']])
                record['gaps'].append(dict(family=family,item='分类、权重、业务暴露及经营指标',
                    reason='候选与行情不等于完整行业样本或产业链供需、产能、库存和利润数据'))

            params=dict(reportname='p03291',functionpara=dict(date='20261016',
                blockname=PROFILES['000016.SH']['blockname'],iv_type='allcontract'),outputpara=OUTPUT)
            dispatch('上证50成分','指数','data_pool',params,
                inspector=lambda item:dict(usable=bool(index_response(task,params,item['snapshot'])['members']),
                    contract=index_response(task,params,item['snapshot'])))
            record['gaps'].append(dict(family='指数',item='通用指数成分与权重',
                reason='当前成分适配仅登记上证50，没有通用指数映射或权重接口'))

            for channel,family,pattern in (('nbs_release','宏观',r'采购经理|消费价格|工业|国民经济'),
                    ('fed_fomc','事件',r'FOMC声明')):
                catalog=dispatch(channel+'目录',family,'release_catalog',dict(channel=channel,page='0'),source='official',
                    inspector=lambda item:dict(usable=bool(item['snapshot'].get('entries')),
                        entries=[dict(title=row['title'],id=row['id']) for row in item['snapshot'].get('entries',[])]))
                choices=[row for row in catalog['snapshot'].get('entries',[]) if re.search(pattern,row['title'])] if catalog else []
                if choices:
                    selected=sorted(choices,key=lambda row:row.get('date',''),reverse=True)[0]
                    dispatch(selected['title']+'原文',family,'release_original',
                        dict(catalog_id=catalog['id'],release_id=selected['id']),source='official',
                        inspector=lambda item:dict(usable=material_inventory(item)['readable_parts']>0,**material_inventory(item)))
                else:
                    record['gaps'].append(dict(family=family,item=channel+'原文',reason='登记目录本页未找到对应标题，未猜地址或改用其他网站'))
            record['gaps'].append(dict(family='宏观',item='自然语言指标到EDB及连续序列',
                reason='仍需用户明确EDB指标ID；当前没有自动指标字典，官方单篇发布不能代替连续序列'))
            record['gaps'].append(dict(family='港股',item='估值和结构化财务',
                reason='date_sequence与财务语义查询当前只登记沪深，不静默把A股字段套到港股'))
            record['status']='diagnostic_closed_with_gaps'
        except Exception as error:
            record.update(status='diagnostic_incomplete',error_type=type(error).__name__)
        finally:
            if task is not None:
                record['ledger']=task['ledger']
                record['model_calls']=task['ledger']['used']['model_calls']
                with gzip.open(output/'sources.json.gz','wt',encoding='utf-8') as stream:
                    json.dump(task['source_records'],stream,ensure_ascii=False)
                record['sources_sha256']=hashlib.sha256((output/'sources.json.gz').read_bytes()).hexdigest()
                assert record['model_calls']==0
            if agent:
                for worker in agent.close_runtime():
                    worker.join(timeout=5)
                    assert not worker.is_alive()
            if collector:
                collector._ifind_access_cache=None
                collector._ifind_original_cache.clear()
            credentials.clear()
            record['production_unchanged']=before==production_digest()
            record['source_unchanged']=all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==value
                for name,value in source_hashes.items())
            save()
    record['private_store_removed']=not Path(temporary).exists()
    save()
    print(json.dumps(dict(status=record['status'],receipt=str(receipt),requests=record.get('ledger',{}).get('used'),
        failed=[row['label'] for row in record['results'] if row['status'] not in ('usable_raw_sample','returned')]),ensure_ascii=False),flush=True)
    assert record['production_unchanged'] and record['source_unchanged'] and record['private_store_removed']
    return receipt
