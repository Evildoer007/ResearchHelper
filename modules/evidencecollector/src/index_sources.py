"""Registered index member retrieval; no weights or historical-vintage approval."""
from __future__ import annotations

import copy
import base64
import hashlib
import json
import math
from functools import lru_cache
from datetime import datetime
from decimal import Decimal
import re
import time
import urllib.error
from urllib.parse import urlsplit

from .contracts import ContractError, fingerprint, market_date

DOCUMENTATION = 'https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/example.html'
LIMITATION = ('成分行仅供同任务后续取数定位，不是已核验金融证据。日期是当前取得版本的名单观测日，'
    '不证明历史当时可得、修订版本、样本完整性或实时持仓；数量吻合不能证明完整，名单不能代替权重。')
FIELDS = {'p03291_f001': ('日期','DT_DATE'), 'p03291_f002': ('同花顺代码','DT_THSCODE'),
    'p03291_f003': ('证券名称','DT_STRING'), 'p03291_f004': ('当日行情端证券名称(仅股票节点有效)','DT_STRING')}
OUTPUT = ','.join(FIELDS)
# Add a mapping only after inspecting a provider definition and a real response.
# This is deliberately not a guessed mapping from index codes to block IDs.
PROFILES = {'000016.SH': dict(name='上证50', aliases=['上证50','SSE 50'], blockname='001005260',
    expected_size_hint=50, documentation=DOCUMENTATION,
    index_documentation='https://www.sse.com.cn/market/sseindex/indexlist/index.shtml?classid=010202')}
INDEX_REQUEST_REGISTRY = {'ifind.data_pool': dict(version=1,kind='登记指数成分原始名单',raw_only=True,
    parameters=['reportname','functionpara','outputpara'],fields={},documentation=DOCUMENTATION,limitations=LIMITATION)}
CSI_ROOT='https://www.csindex.com.cn'
CSI_FILES='https://oss-ch.csindex.com.cn'
CSI_PREFIX='/static/html/csindex/public/uploads/file/autofile/'
CSI_MAX_BYTES=5*1024*1024
CSI_PROFILES={
    '000300.SH':dict(code='000300',name='沪深300',aliases=['沪深300','CSI 300'],expected_rows=300),
    '000905.SH':dict(code='000905',name='中证500',aliases=['中证500','中证小盘500','CSI 500'],expected_rows=500,
        index_documentation=CSI_FILES+'/static/html/csindex/public/uploads/indices/detail/files/zh_CN/000905factsheet.pdf'),
    '000852.SH':dict(code='000852',name='中证1000',aliases=['中证1000','CSI 1000'],expected_rows=1000,
        index_documentation=CSI_FILES+'/static/html/csindex/public/uploads/indices/detail/files/zh_CN/000852factsheet.pdf')}
CSI_LEGACY_CODES=frozenset(('000300.SH',))
PROFILE_POLICY='official_index_profile_catalogue_version'
CSI_HEADERS=['日期Date','指数代码 Index Code','指数名称 Index Name','指数英文名称Index Name(Eng)',
    '成份券代码Constituent Code','成份券名称Constituent Name','成份券英文名称Constituent Name(Eng)',
    '交易所Exchange','交易所英文名称Exchange(Eng)']
CSI_EXCHANGES={'上海证券交易所':('Shanghai Stock Exchange','SH'),'深圳证券交易所':('Shenzhen Stock Exchange','SZ')}
WEIGHT_FIELD=dict(raw_unit='%',unit='%',factor=1,
    definition='中证指数官方XLS指定日期、指定指数内该成分券的原文百分比权重，不归一化，不代表基金持仓或区间收益贡献')
for _operation,_kind in (('index_catalog','官方指数文件目录'),('index_file','目录绑定的官方指数XLS原件')):
    INDEX_REQUEST_REGISTRY['official.'+_operation]=dict(version=1,kind=_kind,raw_only=True,fields={},
        documentation=CSI_ROOT+'/#/indices/family/detail?indexCode=000300',
        limitations='保留完整官方文件及逐行日期、代码和权重，不补值、不归一化；文件结构通过不批准历史可得、投资判断或计算。')


def enabled(task):
    return type(task.get('index_constituent_source_version')) is int and task['index_constituent_source_version'] == 1


def available_profiles(task):
    if not enabled(task):
        return {}
    from .evidence import declared_security_codes, identity_resolutions
    codes = set(declared_security_codes(task)) | {code for item in identity_resolutions(task)
        if item['status'] == '代码映射已核验' for code in item['codes']}
    texts = [task.get('question',''),*task.get('objects',[])]
    return {code:copy.deepcopy(profile) for code,profile in PROFILES.items()
        if code in codes or any(alias in text for alias in profile['aliases'] for text in texts if isinstance(text,str))}


def validate_index_request(task, params, *, require_identity=True):
    if not enabled(task) or not isinstance(params,dict) or set(params) != {'reportname','functionpara','outputpara'}:
        raise ContractError('指数成分请求须启用登记版本并完整声明报表、参数与字段')
    if params['reportname'] != 'p03291' or params['outputpara'] != OUTPUT:
        raise ContractError('指数成分只使用已实取的p03291报表和四项登记字段，不猜报表或权重列')
    options = params['functionpara']
    if (not isinstance(options,dict) or set(options) != {'date','blockname','iv_type'}
            or options['iv_type'] != 'allcontract'):
        raise ContractError('指数成分须保留明确日期、登记板块及原始合约口径')
    profiles = available_profiles(task) if require_identity else PROFILES
    matched = [(code,profile) for code,profile in profiles.items() if profile['blockname'] == options['blockname']]
    if len(matched) != 1:
        raise ContractError('指数板块未由本任务声明或登记名称关联，不猜映射、不扫描板块')
    value = options['date']
    try:
        if not isinstance(value,str) or not re.fullmatch(r'\d{8}',value):
            raise ValueError
        observed = datetime.strptime(value,'%Y%m%d').date().isoformat()
    except ValueError:
        raise ContractError('指数成分日期须为实际YYYYMMDD，不补交易日或改写日期') from None
    if observed > market_date(task['cutoff']):
        raise ContractError('指数成分观测日不能晚于研究截止日期')
    return matched[0][0], observed


def index_request_shapes(task):
    profiles = available_profiles(task)
    if not profiles:
        return official_index_shapes(task)
    return [dict(qualified_id_for_lookup_only='ifind.data_pool',source='ifind',operation='data_pool',
        parameter_keys=['reportname','functionpara','outputpara'],raw_only=True,numeric_fields=[],
        index_profiles=[dict(index_code=code,**profile) for code,profile in profiles.items()],
        fixed_parameters=dict(reportname='p03291',outputpara=OUTPUT),
        date_format='YYYYMMDD',date_timezone='Asia/Shanghai',limitations=LIMITATION,
        guidance='日期显式选定，不将截止日当作交易日；名单可用于后续逐项取数，不给未登记指数猜板块ID。')]+official_index_shapes(task)


def official_index_profiles(task):
    if type(task.get('official_index_sources_version')) is not int or task['official_index_sources_version']!=1:
        return {}
    from .evidence import declared_security_codes
    codes=set(declared_security_codes(task));texts=[task.get('question',''),*task.get('objects',[])]
    extended=extended_profiles_enabled(task)
    return {code:copy.deepcopy(profile) for code,profile in CSI_PROFILES.items()
        if (extended or code in CSI_LEGACY_CODES) and (code in codes
            or any(alias in text for alias in profile['aliases'] for text in texts if isinstance(text,str)))}


def extended_profiles_enabled(task):
    version=task.get(PROFILE_POLICY)
    if version is None:return False
    if type(version) is not int or version!=2:raise ContractError('官方指数资料目录版本未登记')
    return True


def csi_catalogue_url(index_code):
    if index_code not in CSI_PROFILES:raise ContractError('官方指数未登记，不猜指数代码')
    return CSI_ROOT+'/csindex-home/indexInfo/index-details-data?fileLang=2&indexCode='+CSI_PROFILES[index_code]['code']


def validate_csi_url(url,index_code,role):
    if index_code not in CSI_PROFILES or not isinstance(url,str):raise ContractError('官方指数文件范围未登记')
    if role=='catalog':
        if url!=csi_catalogue_url(index_code):raise ContractError('官方目录地址必须与登记端点完全一致')
        return
    parsed=urlsplit(url);code=CSI_PROFILES[index_code]['code']
    if (parsed.scheme!='https' or parsed.netloc!='oss-ch.csindex.com.cn' or parsed.fragment
            or parsed.username is not None or parsed.port is not None):
        raise ContractError('指数原件只使用登记官方主机，不接受跳转、凭据或端口')
    pattern=(re.escape(CSI_PREFIX)+r'closeweightlintiao/\d{8}/'+code+r'closeweight\.xls' if role=='weights'
        else re.escape(CSI_PREFIX)+'cons/'+code+r'cons\.xls' if role=='members' else None)
    if pattern is None or not re.fullmatch(pattern,parsed.path) or parsed.query and not re.fullmatch(r'\d{14}',parsed.query):
        raise ContractError('指数原件须使用实际目录提供的完整XLS地址，不构造历史路径或其他文件')


def csi_catalogue_contents(payload,index_code):
    """Treat each advertised file independently, without guessing a missing one.

    Directory success is transport evidence, not coverage or financial approval.
    A missing/ambiguous/unsafe file cannot invalidate the other valid role.
    """
    if index_code not in CSI_PROFILES:raise ContractError('官方指数未登记，不猜指数代码')
    try:data=json.loads(payload.decode('utf-8-sig'))
    except (ValueError,UnicodeError,AttributeError):raise ContractError('官方指数目录不是有效UTF-8 JSON') from None
    if (not isinstance(data,dict) or data.get('code')!='200' or data.get('success') is not True
            or not isinstance(data.get('data'),dict)):
        raise ContractError('官方指数目录未返回明确成功，不能当作空目录')
    result=[];issues=[]
    for label,role in (('样本权重','weights'),('样本列表','members')):
        rows=data['data'].get(label)
        if rows is None or rows==[]:
            issues.append(dict(role=role,status='not_advertised',reason='当前目录未提供此类文件，不推断资料不存在'))
            continue
        if not isinstance(rows,list) or len(rows)!=1:
            issues.append(dict(role=role,status='ambiguous',reason='文件条目格式异常或版本不唯一，未选择其中一个'))
            continue
        row=rows[0]
        if not isinstance(row,dict) or row.get('fileType')!='xls':
            issues.append(dict(role=role,status='unsupported_format',reason='文件格式未登记，未猜解析方式'))
            continue
        url=row.get('filePath')
        try:validate_csi_url(url,index_code,role)
        except (ContractError,ValueError):
            issues.append(dict(role=role,status='invalid_address',reason='文件地址不符合登记官方渠道，未请求'))
            continue
        result.append(dict(id='csi_file_'+fingerprint([index_code,role,url,hashlib.sha256(payload).hexdigest()])[:24],
            index_code=index_code,role=role,title=label,url=url,file_type='xls'))
    return dict(entries=result,issues=issues)


def parse_csi_catalogue(payload,index_code):
    return csi_catalogue_contents(payload,index_code)['entries']


def parse_csi_workbook(payload,index_code,role,*,version=1):
    """Read original BIFF cells, including leading-zero codes and literal units."""
    if (type(version) is not int or version not in (1,2)
            or index_code not in CSI_PROFILES or role not in ('weights','members') or not isinstance(payload,bytes)
            or not 1<=len(payload)<=CSI_MAX_BYTES or not payload.startswith(bytes.fromhex('D0CF11E0A1B11AE1'))):
        raise ContractError('官方指数原件不是登记范围的BIFF XLS，未执行或猜测文件格式')
    import xlrd
    book=xlrd.open_workbook(file_contents=payload,on_demand=True)
    try:
        if book.nsheets!=1:raise ContractError('指数原件工作表数量改变，未选第一张忽略其他表')
        sheet=book.sheet_by_index(0);profile=CSI_PROFILES[index_code]
        header=CSI_HEADERS+(['权重(%)weight'] if role=='weights' else [])
        rows_valid=(sheet.nrows==profile['expected_rows']+1 if version==1 else 2<=sheet.nrows<=2001)
        if sheet.ncols!=len(header) or not rows_valid or sheet.row_values(0)!=header:
            raise ContractError('指数文件表头、权重单位或完整行数不符，未补行、裁行或猜字段')
        raw_rows=[sheet.row_values(i) for i in range(1,sheet.nrows)]
        for i in range(1,sheet.nrows):
            if any(sheet.cell_type(i,col)!=xlrd.XL_CELL_TEXT for col in (0,1,4,5,7,8)):
                raise ContractError('指数日期、代码、名称和交易所须保留原始文本，不从数值或布尔补造')
            if role=='weights' and version==1 and sheet.cell_type(i,9)!=xlrd.XL_CELL_NUMBER:
                raise ContractError('指数权重须为原始数值单元格，不采用文本、空值或布尔')
        weight_types=[sheet.cell_type(i,9) for i in range(1,sheet.nrows)] if role=='weights' else []
    finally:book.release_resources()
    dates=set();seen=set();members=[];total=Decimal(0);missing=[]
    for position,row in enumerate(raw_rows,2):
        day,index,_,_,code,name,_,exchange,english=row[:9]
        if (not isinstance(day,str) or not re.fullmatch(r'\d{8}',day) or index!=profile['code']
                or not isinstance(code,str) or not re.fullmatch(r'\d{6}',code)
                or not isinstance(name,str) or not name.strip() or exchange not in CSI_EXCHANGES
                or english!=CSI_EXCHANGES[exchange][0]):
            raise ContractError('指数原件的日期、代码、名称或交易所不符，不补零或猜后缀')
        try:observed=datetime.strptime(day,'%Y%m%d').date().isoformat()
        except ValueError:raise ContractError('指数文件含无效日期') from None
        full_code=code+'.'+CSI_EXCHANGES[exchange][1]
        if full_code in seen:raise ContractError('指数文件成分重复，不去重后冒充完整')
        dates.add(observed);seen.add(full_code)
        member=dict(excel_row=position,code=full_code,raw_code=code,name=name,exchange=exchange,observation_date=observed)
        if role=='weights':
            weight=row[9]
            valid=(weight_types[position-2]==xlrd.XL_CELL_NUMBER
                and type(weight) in (int,float) and math.isfinite(weight) and 0<=weight<=100)
            if not valid:
                if version==1 or isinstance(weight,float) and not math.isfinite(weight):
                    raise ContractError('指数权重缺值、非有限或越界，不补零或归一化')
                member.update(weight=None,raw_weight=weight,raw_unit='%',unit='%',weight_column=9)
                missing.append(dict(excel_row=position,code=full_code,reason='原始权重缺失或不是有效百分比'))
            else:
                member.update(weight=weight,raw_unit='%',unit='%',weight_column=9);total+=Decimal(str(weight))
        members.append(member)
    if len(dates)!=1:raise ContractError('指数原件混有多个日期，不合并为同一时点')
    result=dict(version=version,index_code=index_code,role=role,observation_date=next(iter(dates)),
        sheet=sheet.name,headers=header,raw_rows=raw_rows,members=members,row_count=len(members),
        unique_members=len(seen),weight_sum=str(total) if role=='weights' else None,
        unit='%' if role=='weights' else None,publisher_file_rows_checked=True,
        historical_availability_verified=False,independent_membership_verified=False,
        numeric_evidence_registered=False,financial_approval=False)
    if version==2:
        result.update(expected_size_hint=profile['expected_rows'],row_count_matches_hint=len(members)==profile['expected_rows'],
            missing_weight_rows=missing,available_weight_count=len(members)-len(missing) if role=='weights' else 0,
            numeric_evidence_registered=role=='weights',weight_sum_is_complete=not missing and role=='weights')
    return result


def weight_evidence_enabled(task,record=None):
    if (type(task.get('index_weight_evidence_version')) is not int
            or task['index_weight_evidence_version']!=1 or task.get('research_mode')!='current'):
        return False
    return record is None or (record.get('source')=='official' and record.get('operation')=='index_file'
        and record.get('snapshot',{}).get('role')=='weights'
        and record['snapshot'].get('parsed',{}).get('version')==2)


@lru_cache(maxsize=8)
def _weight_binary_parse(raw,index_code):
    # Cached bytes are immutable. The caller compares the complete original
    # parsed representation every time, never trusts a mutable approval flag.
    return parse_csi_workbook(raw,index_code,'weights',version=2)


def verified_weight_source(task,record):
    """Recheck actual transport, original XLS, directory and observation date."""
    if not weight_evidence_enabled(task,record):
        raise ContractError('指数权重须为当前任务实际取得的已登记原件，不采用导入或历史快照')
    return _verified_index_file(task,record,'weights')


@lru_cache(maxsize=8)
def _member_binary_parse(raw,index_code):
    return parse_csi_workbook(raw,index_code,'members')


def _verified_index_file(task,record,role):
    from .evidence import current_query_reference
    if current_query_reference(task,record) is None:
        raise ContractError('指数原件须为当前任务实际取得，不采用导入或历史快照')
    snapshot=record['snapshot'];index_code=snapshot['index_code']
    selected=validate_official_index_request(task,'index_file',record['parameters'])
    if (selected['role']!=role or selected['index_code']!=index_code or snapshot.get('role')!=role
            or snapshot.get('url')!=selected['url'] or snapshot.get('catalogue_reference')!={
                key:selected[key] for key in ('catalog_id','catalog_hash','id')}):
        raise ContractError('指数权重原件与实际目录、指数或角色不符')
    try:raw=base64.b64decode(snapshot['original_base64'],validate=True)
    except (ValueError,KeyError):raise ContractError('指数权重原件字节缺失') from None
    if hashlib.sha256(raw).hexdigest()!=snapshot.get('original_sha256'):
        raise ContractError('指数权重原件字节哈希不符')
    parsed=(_weight_binary_parse if role=='weights' else _member_binary_parse)(raw,index_code)
    if parsed!=snapshot.get('parsed'):
        raise ContractError('指数权重数值、单位或名单与原始XLS不一致')
    observed=parsed['observation_date']
    if observed>market_date(task['cutoff']) or selected['as_of'] is not None and observed!=selected['as_of']:
        raise ContractError('指数权重日期与请求或截止时点不符')
    return parsed


def retained_official_member_sources(task):
    """Current original codes authorize lookups, not issuer identity or membership claims."""
    if not extended_profiles_enabled(task) or task.get('research_mode')!='current':return []
    results=[]
    for record in task.get('source_records',[]):
        if record.get('source')!='official' or record.get('operation')!='index_file':continue
        try:
            role=record.get('snapshot',{}).get('role')
            if role=='weights':parsed=verified_weight_source(task,record)
            elif role=='members':parsed=_verified_index_file(task,record,'members')
            else:continue
        except (ContractError,KeyError,TypeError,ValueError):continue
        results.append(dict(artifact_id=record['id'],members=parsed['members']))
    return results


def weight_row_identity(parsed,member):
    return dict(mode='publisher_index_observation',provider_code=parsed['index_code'],object=parsed['index_code'],
        member_code=member['code'],member_name=member['name'],exchange=member['exchange'])


def weight_row_text(parsed,member):
    return (parsed['index_code']+'，'+member['observation_date']+'，成分券'+member['code']+' '+member['name']
        +'，权重'+str(member['weight'])+'%。')


def weight_table_choices(task,views=None):
    if not weight_evidence_enabled(task):return []
    allowed={view['id'] for view in views} if views is not None else None
    result=[]
    for record in task.get('source_records',[]):
        if allowed is not None and record['id'] not in allowed:continue
        if not weight_evidence_enabled(task,record):continue
        try:parsed=verified_weight_source(task,record)
        except ContractError:continue
        result.append(dict(reference='index_weights_'+fingerprint([task['id'],task['version'],task['input_hash'],
            record['id'],record['hash']])[:24],artifact_id=record['id'],snapshot_hash=record['hash'],
            index_code=parsed['index_code'],observation_date=parsed['observation_date'],unit='%',
            observed_members=parsed['row_count'],available_weights=parsed['available_weight_count'],
            missing_weights=copy.deepcopy(parsed['missing_weight_rows']),weight_sum=parsed['weight_sum'],
            expected_size_hint=parsed['expected_size_hint'],row_count_matches_hint=parsed['row_count_matches_hint'],
            rule='显式选择本原件所有实际有效权重，不补值、不归一化；行数吻合不证明独立完整性或历史可得。'))
    return result


def expand_weight_selection(task,selection):
    if set(selection)!={'index_weight_table_selection','question_ids','review'}:
        raise ContractError('整表权重仅选择原件引用、问题及适用性说明，不混填数值或批准')
    matches=[choice for choice in weight_table_choices(task) if choice['reference']==selection['index_weight_table_selection']]
    if len(matches)!=1:raise ContractError('整表权重选择不属于本任务同版原件')
    choice=matches[0];record=next(r for r in task['source_records'] if r['id']==choice['artifact_id'])
    parsed=verified_weight_source(task,record)
    from .evidence import current_snapshot_reference
    available=current_snapshot_reference(task,record)
    if available is None:raise ContractError('指数权重缺少实际当前取数依据')
    result=[]
    for member in parsed['members']:
        if member['weight'] is None:continue
        locator=dict(sheet=parsed['sheet'],row=member['excel_row']-1,column=9,field='weight')
        result.append(dict(id='rh_iw_'+fingerprint([task['id'],task['version'],record['id'],record['hash'],locator])[:24],
            artifact_id=record['id'],record_locator=locator,object=parsed['index_code'],period=member['observation_date'],
            identity=weight_row_identity(parsed,member),text=weight_row_text(parsed,member),nature='已实现事实',
            raw_value=member['weight'],value=member['weight'],raw_unit='%',unit='%',conversion_factor=1,
            published_at=None,publication=dict(at=None,basis_ref=None,certainty='未知',precision='未知',timezone='未知',
                basis='官方当前版本的成分权重；首次公开时刻未核验，不证明历史当时可得'),
            availability=copy.deepcopy(available),question_ids=copy.deepcopy(selection['question_ids']),
            review=copy.deepcopy(selection['review'])))
    return result


def validate_weight_item(item,task,artifact):
    parsed=verified_weight_source(task,artifact);locator=item.get('record_locator',{})
    row=locator.get('row')
    if (set(locator)!={'sheet','row','column','field'} or type(row) is not int
            or not 1<=row<=len(parsed['members']) or locator['sheet']!=parsed['sheet']
            or locator['column']!=9 or locator['field']!='weight'):
        raise ContractError('指数权重定位须为实际XLS工作表、行和权重列')
    member=parsed['members'][row-1]
    if member['weight'] is None:raise ContractError('该成分权重缺失，不能补零')
    if any(type(item.get(key)) not in (int,float) for key in ('value','raw_value','conversion_factor')):
        raise ContractError('权重和换算因子须为明确数字，不采用布尔值')
    expected=dict(object=parsed['index_code'],period=member['observation_date'],identity=weight_row_identity(parsed,member),
        text=weight_row_text(parsed,member),nature='已实现事实',raw_value=member['weight'],value=member['weight'],
        raw_unit='%',unit='%',conversion_factor=1)
    if any(item.get(key)!=value for key,value in expected.items()):
        raise ContractError('指数权重主体、成分、原值、日期或单位不符')


def validate_official_index_request(task,operation,params):
    profiles=official_index_profiles(task)
    if operation=='index_catalog':
        if not isinstance(params,dict) or set(params)!={'index_code'} or params['index_code'] not in profiles:
            raise ContractError('官方指数目录须属于本任务已登记指数范围')
        return dict(index_code=params['index_code'],role='catalog',url=csi_catalogue_url(params['index_code']))
    if operation!='index_file' or not isinstance(params,dict) or set(params)!={'catalog_id','entry_id','as_of'}:
        raise ContractError('官方指数原件须显式选择本任务目录、文件和所需日期')
    date=params['as_of']
    if date is not None:
        if not isinstance(date,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',date):raise ContractError('指数目标日期须为YYYY-MM-DD或明确当前版本')
        try:datetime.strptime(date,'%Y-%m-%d')
        except ValueError:raise ContractError('指数目标日期无效') from None
        if date>market_date(task['cutoff']):raise ContractError('指数目标日期晚于研究截止日期')
    records=[r for r in task.get('source_records',[]) if r.get('id')==params['catalog_id'] and
        r.get('source')=='official' and r.get('operation')=='index_catalog' and r.get('task_id')==task['id']
        and type(r.get('research_version')) is int and r['research_version']==task['version']]
    if len(records)!=1:raise ContractError('指数目录不属于本任务版本，不借用其他任务原件')
    record=records[0];snapshot=record['snapshot'];index_code=record['parameters']['index_code']
    from .evidence import current_query_reference
    if index_code not in profiles or current_query_reference(task,record) is None:
        raise ContractError('指数原件只从同任务实际获取的当前官方目录选择')
    raw=base64.b64decode(snapshot['original_base64'],validate=True)
    if hashlib.sha256(raw).hexdigest()!=snapshot['original_sha256']:raise ContractError('指数目录原始字节变化')
    contents=csi_catalogue_contents(raw,index_code);entries=contents['entries']
    if entries!=snapshot.get('entries'):raise ContractError('指数目录条目与实际原件不一致')
    if snapshot.get('catalogue_issues',[])!=contents['issues']:
        raise ContractError('指数目录文件缺口与原始响应不一致')
    selected=[entry for entry in entries if entry['id']==params['entry_id']]
    if len(selected)!=1:raise ContractError('指数文件选择不在当前实际目录中')
    return dict(selected[0],catalog_id=record['id'],catalog_hash=record['hash'],as_of=date)


def official_index_shapes(task):
    profiles=official_index_profiles(task)
    if not profiles:return []
    tools=[dict(qualified_id_for_lookup_only='official.index_catalog',source='official',operation='index_catalog',
        parameter_keys=['index_code'],available_parameter_sets=[dict(index_code=code) for code in profiles],raw_only=True,
        guidance='补充iFinD尚未登记的完整权重渠道，仅取官方实际文件目录，不猜历史路径。')]
    choices=[]
    for record in task.get('source_records',[]):
        if record.get('source')!='official' or record.get('operation')!='index_catalog':continue
        for entry in record.get('snapshot',{}).get('entries',[]):
            try:validate_official_index_request(task,'index_file',dict(catalog_id=record['id'],entry_id=entry['id'],as_of=None))
            except (ContractError,KeyError,TypeError,ValueError):continue
            choices.append(dict(catalog_id=record['id'],entry_id=entry['id'],index_code=entry['index_code'],role=entry['role'],title=entry['title']))
    if choices:tools.append(dict(qualified_id_for_lookup_only='official.index_file',source='official',operation='index_file',
        parameter_keys=['catalog_id','entry_id','as_of'],official_index_file_choices=choices,raw_only=True,
        guidance='as_of填实际需要的日期；只要当前文件版本时填null。原件日期不符则保留原件并明确拒绝，不改为所需日期。'))
    if choices and weight_evidence_enabled(task):
        tools[-1].update(raw_only=False,numeric_fields_by_role={'weights':['weight'],'members':[]},
            guidance=tools[-1]['guidance']+'权重原件本身含成分代码和名称，足以回答成分及权重时不额外强取名单；名单独立核对仅按原问题需要。')
    return tools


def download_csi_blob(url,index_code,role,timeout):
    """Fixed public GET with one deadline, TLS checking and a 5MB ceiling."""
    validate_csi_url(url,index_code,role)
    from .evidence import _PublicFilingConnection
    parsed=urlsplit(url);deadline=time.monotonic()+timeout
    connection=_PublicFilingConnection(parsed.hostname,timeout=timeout,use_local_proxy=True)
    try:
        connection.request('GET',parsed.path+('?'+parsed.query if parsed.query else ''),headers={'Accept-Encoding':'identity'})
        response=connection.getresponse()
        if response.status!=200:raise urllib.error.HTTPError(url,response.status,'官方指数资料未成功返回',response.headers,None)
        expected='application/json' if role=='catalog' else 'application/vnd.ms-excel'
        if response.getheader('Content-Type','').split(';')[0].lower()!=expected:raise ContractError('官方指数内容类型与登记格式不符')
        declared=response.getheader('Content-Length')
        if declared is not None and (not declared.isdigit() or int(declared)>CSI_MAX_BYTES):raise ContractError('官方指数原件长度异常或超过5MB')
        chunks=[];size=0
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise ContractError('官方指数下载超过本次时限')
            if connection.sock is not None:connection.sock.settimeout(remaining)
            block=response.read1(min(65536,CSI_MAX_BYTES+1-size))
            if not block:break
            size+=len(block)
            if size>CSI_MAX_BYTES:raise ContractError('官方指数原件超过5MB')
            chunks.append(block)
        if declared is not None and int(declared)!=size:raise ContractError('官方指数原件未完整接收')
        raw=b''.join(chunks)
        return dict(original_base64=base64.b64encode(raw).decode('ascii'),original_sha256=hashlib.sha256(raw).hexdigest(),
            url=url,response_headers={key:value for key,value in response.headers.items()
                if key.lower() in ('content-type','etag','last-modified','date','content-length')})
    finally:connection.close()


def official_index_source_view(record):
    """Expose every original row once, with the untouched binary in archive."""
    snapshot=record['snapshot']
    common=dict(code=snapshot['code'],index_code=snapshot['index_code'],role=snapshot['role'],
        url=snapshot['url'],response_headers=copy.deepcopy(snapshot['response_headers']),
        original_archive_ref=dict(artifact_id=record['id'],snapshot_hash=record['hash'],
            original_sha256=snapshot['original_sha256'],binary_retained=True),raw_only=True,financial_approval=False)
    if record['operation']=='index_catalog':
        common['entries']=copy.deepcopy(snapshot['entries'])
        if snapshot.get('catalogue_issues'):
            common['catalogue_issues']=copy.deepcopy(snapshot['catalogue_issues'])
    else:
        parsed=snapshot['parsed']
        common['workbook']={key:copy.deepcopy(parsed[key]) for key in
            ('sheet','headers','raw_rows','observation_date','row_count','unique_members','unit','weight_sum')}
        common['catalogue_reference']=copy.deepcopy(snapshot['catalogue_reference'])
        if parsed.get('version')==2 and snapshot['role']=='weights':
            common.update(raw_only=False,numeric_fields=['weight'],
                weight_coverage={key:copy.deepcopy(parsed[key]) for key in
                    ('expected_size_hint','row_count_matches_hint','available_weight_count','missing_weight_rows','weight_sum_is_complete')})
    return common


def index_response(task, params, payload):
    code, observed = validate_index_request(task,params)
    if (not isinstance(payload,dict) or isinstance(payload.get('errorcode'),bool)
            or payload.get('errorcode') not in (0,'0') or not isinstance(payload.get('tables'),list)
            or len(payload['tables']) != 1 or not isinstance(payload['tables'][0],dict)):
        raise ContractError('指数成分须保留单表成功业务响应，不把异常当空名单')
    columns = payload['tables'][0].get('table')
    if not isinstance(columns,dict) or any(not isinstance(columns.get(field),list) for field in FIELDS):
        raise ContractError('指数成分缺少四项实际原始列，不补列或猜字段')
    labels, descriptions = payload.get('outParams'), payload.get('descrs')
    if (not isinstance(labels,dict) or not isinstance(descriptions,dict)
            or any(labels.get(field) != label or not isinstance(descriptions.get(field),dict)
                or descriptions[field].get('type') != kind for field,(label,kind) in FIELDS.items())):
        raise ContractError('指数成分字段标签或类型与登记定义不符，保留原始响应待核对')
    count = len(columns['p03291_f002'])
    if not 1 <= count <= 2000 or any(len(columns[field]) != count for field in FIELDS):
        raise ContractError('指数成分列须非空、等长且不超过2000行；不截断名单后继续')
    rows, seen = [], set()
    for index in range(count):
        date, member, name, historical_name = [columns[field][index] for field in FIELDS]
        if date != observed.replace('-','/'):
            raise ContractError('指数成分原始日期与本次请求不一致，不用请求日期覆盖回显')
        if (not isinstance(member,str) or not re.fullmatch(r'\d{6}\.(?:SH|SZ)',member)
                or member in seen or not isinstance(name,str) or not name.strip()
                or historical_name is not None and not isinstance(historical_name,str)):
            raise ContractError('指数成分代码、名称或重复行异常，不修复后缀、去重或补名称')
        seen.add(member)
        rows.append(dict(row=index,code=member,name=name,name_in_time=historical_name,date=observed))
    return dict(version=1,index_code=code,blockname=params['functionpara']['blockname'],observation_date=observed,
        members=rows,observed_rows=count,expected_size_hint=PROFILES[code]['expected_size_hint'],
        row_count_matches_hint=count==PROFILES[code]['expected_size_hint'],
        complete_set_verified=False,weights_available=False,numeric_evidence_registered=False,limitations=LIMITATION)


def callable_member_codes(task):
    """Only an actual same-task source may widen lookup scope; not identity approval."""
    if not enabled(task):
        return []
    from .evidence import current_query_reference
    codes = set()
    for record in task.get('source_records',[]):
        if record.get('source') != 'ifind' or record.get('operation') != 'data_pool':
            continue
        if (record.get('task_id') != task['id'] or type(record.get('research_version')) is not int
                or record['research_version'] != task['version']):
            continue
        if record.get('status') != '成功待核验' or current_query_reference(task,record) is None:
            continue
        if fingerprint(record.get('snapshot')) != record.get('hash'):
            continue
        try:
            result = index_response(task,record['parameters'],record['snapshot'])
        except (ContractError,KeyError,TypeError,ValueError):
            continue
        codes.update(member['code'] for member in result['members'])
    for source in retained_official_member_sources(task):
        codes.update(member['code'] for member in source['members'])
    return sorted(codes)
