"""Registered index constituents, current weights, and source-backed industry samples."""
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

from .contracts import ContractError, DuplicateJSONField, fingerprint, market_date, unique_json_object
from .subject_scope import scope_question, subject_allowed

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
INDUSTRY_SAMPLE_POLICY = 'draft_industry_sample_version'
INDUSTRY_SAMPLE_VERSION = 2
BROKER_READING_POLICY = 'draft_broker_reading_version'
_CSI_FACTSHEETS = CSI_FILES+'/static/html/csindex/public/uploads/indices/detail/files/zh_CN/'
_INDUSTRY_PROFILES = {
    '399975.SZ': dict(code='399975', name='中证全指证券公司指数', aliases=['中证全指证券公司指数'],
        expected_rows=47, topic='券商', topic_pattern=r'A股券商|券商(?:行业|板块|样本)|证券(?:公司)?(?:行业|板块)|中证全指证券公司指数',
        index_documentation=_CSI_FACTSHEETS+'399975factsheet.pdf'),
    '399986.SZ': dict(code='399986', name='中证银行指数', aliases=['中证银行指数'],
        expected_rows=42, topic='银行', topic_pattern=r'银行(?:行业|板块|业(?!绩))|中证银行|A股银行|上市银行',
        index_documentation=_CSI_FACTSHEETS+'399986factsheet.pdf'),
    '399997.SZ': dict(code='399997', name='中证白酒指数', aliases=['中证白酒指数', '中证白酒'],
        expected_rows=17, topic='白酒', topic_pattern=r'白酒(?:行业|板块|样本)|中证白酒|A股白酒',
        index_documentation=_CSI_FACTSHEETS+'399997factsheet.pdf', operating_drivers=True),
    'H30184.SH': dict(code='H30184', name='中证全指半导体产品与设备指数',
        aliases=['中证全指半导体产品与设备指数'], expected_rows=87, topic='半导体',
        topic_pattern=r'半导体(?:行业|板块|样本|产品与设备)|A股半导体',
        index_documentation=_CSI_FACTSHEETS+'H30184factsheet.pdf', operating_drivers=True),
}
CSI_PROFILES={
    '000300.SH':dict(code='000300',name='沪深300',aliases=['沪深300','CSI 300'],expected_rows=300),
    '000905.SH':dict(code='000905',name='中证500',aliases=['中证500','中证小盘500','CSI 500'],expected_rows=500,
        index_documentation=CSI_FILES+'/static/html/csindex/public/uploads/indices/detail/files/zh_CN/000905factsheet.pdf'),
    '000852.SH':dict(code='000852',name='中证1000',aliases=['中证1000','CSI 1000'],expected_rows=1000,
        index_documentation=CSI_FILES+'/static/html/csindex/public/uploads/indices/detail/files/zh_CN/000852factsheet.pdf'),
    **_INDUSTRY_PROFILES}
CSI_LEGACY_CODES=frozenset(('000300.SH',))
PROFILE_POLICY='official_index_profile_catalogue_version'
DRAFT_WEIGHT_POLICY = 'draft_index_weight_scope_version'
DRAFT_WEIGHT_REFRESH_VERSION = 3
COMPOUND_WEIGHT_VERSION = 4
DRAFT_WEIGHT_VERSION = COMPOUND_WEIGHT_VERSION
DRAFT_LATEST_WEIGHT_VERSIONS = (2, DRAFT_WEIGHT_REFRESH_VERSION, DRAFT_WEIGHT_VERSION)
_WEIGHT_RANK = re.compile(r'前\s*([0-9]+|[一二三四五六七八九十百千两]+)\s*(?:大|只|个|名|位)?\s*(?:成[分份](?:股|券)?|权重股?)')

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


def _index_allowed(task, code, profile):
    return subject_allowed(task, code) and all(subject_allowed(task, value)
        for alias in profile['aliases'] for value in (alias, alias + '指数'))


def registered_market_index_names(task):
    """Use the registered index identities without checking source permissions."""
    profiles = PROFILES | CSI_PROFILES
    return {alias: (code, profile['name'])
            for code, profile in profiles.items() if _index_allowed(task, code, profile)
            for alias in profile['aliases']}


def available_profiles(task):
    if not enabled(task):
        return {}
    from .evidence import declared_security_codes, identity_resolutions
    codes = set(declared_security_codes(task)) | {code for item in identity_resolutions(task)
        if item['status'] == '代码映射已核验' for code in item['codes']}
    texts = [scope_question(task), *task.get('objects', [])]
    return {code:copy.deepcopy(profile) for code,profile in PROFILES.items()
        if _index_allowed(task, code, profile) and
        (code in codes or any(alias in text for alias in profile['aliases'] for text in texts))}


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
    codes = set(declared_security_codes(task))
    texts = [scope_question(task), *task.get('objects', [])]
    extended=extended_profiles_enabled(task)
    industry_scope = industry_operating_scope(task)
    if industry_scope:
        codes.add(industry_scope['index_code'])
    return {code:copy.deepcopy(profile) for code,profile in CSI_PROFILES.items()
        if (extended or code in CSI_LEGACY_CODES) and _index_allowed(task, code, profile) and (code in codes
            or any(alias in text for alias in profile['aliases'] for text in texts))}


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
    pattern=(re.escape(CSI_PREFIX)+r'(?:closeweight|closeweightlintiao/\d{8})/'+code+r'closeweight\.xls' if role=='weights'
        else re.escape(CSI_PREFIX)+'cons/'+code+r'cons\.xls' if role=='members' else None)
    if pattern is None or not re.fullmatch(pattern,parsed.path) or parsed.query and not re.fullmatch(r'\d{14}',parsed.query):
        raise ContractError('指数原件须使用实际目录提供的完整XLS地址，不构造历史路径或其他文件')


def csi_catalogue_contents(payload,index_code):
    """Treat each advertised file independently, without guessing a missing one.

    Directory success is transport evidence, not coverage or financial approval.
    A missing/ambiguous/unsafe file cannot invalidate the other valid role.
    """
    if index_code not in CSI_PROFILES:raise ContractError('官方指数未登记，不猜指数代码')
    try:
        data = json.loads(payload.decode('utf-8-sig'), object_pairs_hook=unique_json_object)
    except DuplicateJSONField:
        raise
    except (ValueError, UnicodeError, AttributeError):
        raise ContractError('官方指数目录不是有效UTF-8 JSON') from None
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


def weight_readout_enabled(task):
    version = task.get(DRAFT_WEIGHT_POLICY)
    return type(version) is int and version in (1, *DRAFT_LATEST_WEIGHT_VERSIONS) and task.get('research_mode') == 'current'


def _weight_scope(task, *, clause=None):
    """Keep a specified day, or use the newest verified sheet for a simple readout."""
    if not weight_readout_enabled(task):
        return None
    text = scope_question(task) if clause is None else clause
    if '权重' not in text or not re.search(r'成[分份]|集中度|权重股', text):
        return None
    profiles = official_index_profiles(task)
    if clause is not None:
        from .evidence import security_code_mentions

        codes = set(security_code_mentions(clause, encoding=task.get('security_declaration_encoding', 1)))
        profiles = {code: profile for code, profile in profiles.items() if code in codes
                    or not codes and any(alias in clause for alias in profile['aliases'])}
    if len(profiles) != 1:
        return None
    from .numeric_integrity import _number, draft_day_points, draft_day_intervals

    ranges = draft_day_intervals(text)
    days = {day.isoformat() for _, _, day in draft_day_points(text, ranges)}
    if ranges or len(days) > 1:
        return None
    (code, profile), = profiles.items()
    if not days and (task[DRAFT_WEIGHT_POLICY] not in DRAFT_LATEST_WEIGHT_VERSIONS
                     or not _only_weight_question(task, code, text)):
        return None
    rank = _WEIGHT_RANK.search(text)
    count = int(_number(rank[1])) if rank else None
    if count is not None and count < 1:
        return None
    return dict(code=code, name=profile['name'], day=next(iter(days), None), count=count,
                expected_rows=profile['expected_rows'], total=bool(re.search(r'合计|总计|总和|总权重', text)
                or count is not None and '集中度' in text))


def stated_weight_scopes(task):
    """Project explicit index lookups without using dates from other clauses."""
    text = scope_question(task)
    if not weight_readout_enabled(task) or '权重' not in text:
        return []
    if task[DRAFT_WEIGHT_POLICY] == COMPOUND_WEIGHT_VERSION:
        from .draft_sources import _unquoted_local_scope
        from .market_series import _latest_clauses

        clauses = _latest_clauses(_unquoted_local_scope(text), valuation=True)
        if len(clauses) > 1:
            scopes = []
            for clause in clauses:
                scope = direct_weight_scope(task, clause=clause)
                if scope is not None and scope not in scopes:
                    scopes.append(scope)
            # Other clauses can contain unrelated dates and index codes.
            # An unrecognized weight clause must not inherit either.
            return scopes
    scope = _weight_scope(task)
    return [scope] if scope is not None else []


def stated_weight_scope(task):
    scopes = stated_weight_scopes(task)
    return scopes[0] if len(scopes) == 1 else None


@lru_cache(maxsize=8)
def _draft_weight_binary_parse(raw, code, version):
    if version == 2:
        return _weight_binary_parse(raw, code)
    return parse_csi_workbook(raw, code, 'weights', version=version)


def _refresh_latest_weight(task, scope):
    return (task.get(DRAFT_WEIGHT_POLICY) in (DRAFT_WEIGHT_REFRESH_VERSION, DRAFT_WEIGHT_VERSION)
            and scope['day'] is None and bool(task.get('update_from')))


def stated_weight_sheet(task, scope=None):
    """Reconcile the selected sheet with its immutable original bytes."""
    scope = stated_weight_scope(task) if scope is None else scope
    if scope is None:
        return None
    requested_day = scope['day']
    result = dict(scope, requested_day=requested_day, source_ref=None, rows=[], complete=False, reason='missing')
    cutoff = market_date(task['cutoff'])
    if requested_day and requested_day > cutoff:
        return result
    if task.get(DRAFT_WEIGHT_POLICY) in DRAFT_LATEST_WEIGHT_VERSIONS:
        from .draft_sources import validate_stated_source
        try:
            validate_stated_source(task, 'official')
        except ContractError:
            return dict(result, reason='source_restricted')
    refresh = _refresh_latest_weight(task, scope)
    candidates = {}
    for record in task.get('source_records', ()):
        if (record.get('source'), record.get('operation'), record.get('status')) != ('official', 'index_file', '成功待核验'):
            continue
        if (record.get('task_id'), record.get('research_version')) != (task['id'], task['version']):
            continue
        snapshot = record.get('snapshot') or {}
        parsed = snapshot.get('parsed') or {}
        observed = parsed.get('observation_date')
        if ((snapshot.get('index_code'), snapshot.get('role')) != (scope['code'], 'weights')
                or not isinstance(observed, str) or observed > cutoff or requested_day and observed != requested_day):
            continue
        try:
            if record['hash'] != fingerprint(snapshot) or type(parsed.get('version')) is not int or parsed['version'] not in (1, 2):
                continue
            raw = base64.b64decode(snapshot['original_base64'], validate=True)
            digest = hashlib.sha256(raw).hexdigest()
            if digest != snapshot.get('original_sha256') or _draft_weight_binary_parse(raw, scope['code'], parsed['version']) != parsed:
                continue
        except (ContractError, KeyError, TypeError, ValueError):
            continue
        files = candidates.setdefault(observed, {})
        previous = files.get(digest)
        retained = record.get('retained_from_version')
        # Prefer this version's receipt for the same original bytes. Different
        # files with the same observation date still require reconciliation.
        if previous is None or (refresh and retained is None and previous[2] is not None):
            files[digest] = record['id'], parsed, retained
    if not candidates:
        return result
    observed = max(candidates)
    result['day'] = observed
    latest = candidates[observed]
    if len(latest) > 1:
        return dict(result, reason='versions')
    (source_ref, parsed, retained), = latest.values()
    if refresh and retained is not None:
        result['retained_from_version'] = retained
    rows = [(index, member) for index, member in enumerate(parsed['members']) if member.get('weight') is not None]
    complete = len(rows) == len(parsed['members']) == scope['expected_rows']
    count = scope['count']
    result.update(source_ref=source_ref, complete=complete, available=len(rows), reason='' if complete else 'incomplete')
    if count is not None and (count > len(rows) or not complete):
        return dict(result, reason='rank')
    if count is not None:
        rows = sorted(rows, key=lambda pair: (-Decimal(str(pair[1]['weight'])), pair[1]['code']))[:count]
    return dict(result, rows=rows)


def stated_weight_sheets(task):
    return [stated_weight_sheet(task, scope) for scope in stated_weight_scopes(task)]


def selected_weight_rows(sheets, source_ref):
    """A reused workbook contributes each requested original row once."""
    return dict(pair for sheet in sheets if sheet['source_ref'] == source_ref for pair in sheet['rows'])


def weight_total_label(count):
    return (f'前{count}大成分' if count is not None else '原表成分')+'权重合计'


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


_HALF_YEAR = re.compile(r'(20\d{2})\s*年?\s*(?:上半年|中报|半年度报告|半年报|H1)', re.I)
_OTHER_PERIOD = re.compile(r'一季报|三季报|季度|下半年|(?<!半)年报|全年|近[几三四五六七八九十\d]+年|多年度|TTM', re.I)
_SAMPLE_COUNT = re.compile(r'(?:前|选|取|挑|比较|对比)\s*([0-9]+|[一二两三四五六七八九十])\s*(?:大|只|家|名)')
_INDUSTRY_ACTION = re.compile(r'经营|财务|业绩|盈利|营收|收入|利润|基本面|半年报|中报')
_BRIEF_INDUSTRY_ASK = re.compile(
    r'\s*(?:(?:请|帮我|想|看看|看一下|看下|了解|研究(?:一下)?|分析(?:一下)?)\s*)*'
    r'(?:A股)?(?P<topic>券商(?:行业|板块)?|证券行业|银行(?:业|行业|板块)?)'
    r'(?:(?:[，,。\s]*)(?:写|给我|做|生成)?(?:一份)?(?:投研|研究)?报告)?[。！？\s]*')
_ADDITIONAL_RESEARCH = re.compile(r'买入|卖出|目标价|市盈率|估值|股价|推荐|涨跌|资金流|风险溢价|政策影响|景气前瞻|盈利预测')
_MIXED_INDUSTRY = re.compile(r'(?:券商|银行)(?:行业|板块|业)?\s*(?:和|及|与|、|对比)\s*(?:A股)?(?:券商|银行)')
_OPERATING_REPORT_ASK = re.compile(
    r'\s*(?:请\s*)?(?:帮我\s*)?(?:研究|分析|看看|看一下)?\s*'
    r'(?:20\d{2}\s*年?\s*上半年\s*)?(?:A股\s*)?'
    r'(?:券商(?:行业|板块)?|证券行业|银行(?:业|行业|板块)?)'
    r'(?:\s*20\d{2}\s*年?\s*上半年)?\s*(?:的)?'
    r'(?:经营|盈利|业绩|财务|基本面)?(?:分化|表现|情况|差异)?'
    r'(?:[，,。\s]+(?:写|生成|做|给我)(?:一份)?(?:投研|研究)?报告)?[。！？\s]*')
_OPERATING_SUBJECT = (r'(?:券商(?:行业|板块)?|证券行业|银行(?:业|行业|板块)?|'
                      r'白酒(?:行业|板块)?|半导体(?:行业|板块)?|中证白酒指数|中证全指半导体产品与设备指数)')
_OPERATING_MIXED = re.compile(rf'{_OPERATING_SUBJECT}\s*(?:以及|或是|和|及|与|、|对比|跟)\s*(?:A股\s*)?{_OPERATING_SUBJECT}')
_OPERATING_PERIOD = r'(?:(?:20\d{2}\s*年?\s*|今年|去年)?上半年)'
_OPERATING_METRIC = r'(?:营业收入|营收|毛利(?:额|率)?|归母净利润|经营现金流(?:净额)?|现金回流)'
_OPERATING_STUDY_ASK = re.compile(
    rf'\s*(?:(?:请|帮我|想)\s*)*(?:研究(?:一下)?|分析(?:一下)?|比较|对比|看看|看一下|看下|了解)?\s*'
    rf'(?:{_OPERATING_PERIOD}\s*)?(?:A股\s*)?(?P<topic>{_OPERATING_SUBJECT})'
    rf'(?:\s*{_OPERATING_PERIOD})?\s*(?:的)?'
    r'(?:经营|盈利|业绩|财务|基本面)?(?:分化|表现|情况|差异)?(?:怎么样|如何)?'
    rf'(?P<metrics>[，,。？?\s]+(?:重点看|比较|关注)\s*{_OPERATING_METRIC}'
    rf'(?:[、，,或及和与\s]+{_OPERATING_METRIC}){{0,5}})?'
    r'(?:[，,。？?\s]+(?:写|生成|做|给我)(?:一份)?(?:投研|研究)?报告)?[。！？?\s]*')


_BROKER_OPENING = re.compile(
    rf'(?:(?:请|帮我|想|看看|研究|分析)\s*)*(?:{_OPERATING_PERIOD}\s*)?'
    rf'(?:A股\s*)?(?:券商(?:行业|板块)?|证券行业)(?:{_OPERATING_PERIOD}\s*)?(?:的)?'
    r'(?:经营|盈利|业绩|财务)(?:表现|分化|情况)?(?:怎么样|如何)?')
_BROKER_REVENUE_ASK = re.compile(
    r'(?:营业收入|营收|收入)(?:增长|变化|变动)?(?:主要)?'
    r'(?:(?:来自|靠|受)什么(?:驱动)?|从哪来|'
    r'(?:靠|来自)(?:(?:净利息|利息|手续费|佣金|收费|投资收益|自营|公允价值|浮盈)(?:和|及|还是|、|与)?){1,5})')
_BROKER_PROFIT_ASK = re.compile(r'(?:归母)?(?:净)?利润(?:(?:有没有|能不能)跟上|跟上了吗|怎么样|如何)')
_BROKER_DELIVERY_ASK = re.compile(r'(?:给我|写|给我写|做|生成)(?:一份)?(?:投研|研究)?报告')


def broker_reading_enabled(task):
    return type(task.get(BROKER_READING_POLICY)) is int and task[BROKER_READING_POLICY] == 1


def _spoken_broker_study(task, text):
    if not broker_reading_enabled(task):
        return False
    clauses = [clause.strip() for clause in re.split(r'[，,。？?；;！!]', text) if clause.strip()]
    if len(clauses) < 2 or not _BROKER_OPENING.fullmatch(clauses[0]):
        return False
    return (any(_BROKER_REVENUE_ASK.fullmatch(clause) for clause in clauses[1:])
            and all(any(pattern.fullmatch(clause) for pattern in
                        (_BROKER_REVENUE_ASK, _BROKER_PROFIT_ASK, _BROKER_DELIVERY_ASK))
                    for clause in clauses[1:]))


def operating_report_question(task):
    """Recognize a standalone operating study for completion, independently of routing."""
    if task.get('question_origin') != 'default':
        return False
    if task.get(INDUSTRY_SAMPLE_POLICY) != INDUSTRY_SAMPLE_VERSION:
        return bool(_OPERATING_REPORT_ASK.fullmatch(task['question']))
    if _spoken_broker_study(task, task['question']):
        return True
    match = _OPERATING_STUDY_ASK.fullmatch(task['question'])
    return bool(match and (not match['metrics'] or any(profile['topic'] in match['topic'] and profile.get('operating_drivers')
        for profile in _INDUSTRY_PROFILES.values())))


def industry_operating_scope(task, *, direct=False):
    """Resolve one industry and a comparable statement period before taking a sample."""
    from .draft_sources import (_NO_PDF, _unquoted_local_scope, local_sources_only, stated_access_restricted,
                                validate_stated_source)
    from .evidence import declared_security_codes
    from .numeric_integrity import _number

    version = task.get(INDUSTRY_SAMPLE_POLICY)
    if (type(version) is not int or version not in (1, INDUSTRY_SAMPLE_VERSION)
            or task.get('research_mode') != 'current' or task.get('plan_version', 1) != 1
            or task.get('intent') not in ('综合研究', '解释变化', '比较对象')
            or len(task.get('questions', ())) != 1 or local_sources_only(task)):
        return None
    question, = task['questions']
    if question.get('answer_scope') or question.get('question') != task['question']:
        return None
    text = _unquoted_local_scope(_NO_PDF.sub('', scope_question(task)))
    mixed = _OPERATING_MIXED if version == INDUSTRY_SAMPLE_VERSION else _MIXED_INDUSTRY
    if (stated_access_restricted(task, text) or _OTHER_PERIOD.search(text) or mixed.search(text)
            or re.search(r'全量|逐家|全覆盖|(?:全部|所有)(?:上市公司|样本|成分|券商|银行)|'
                         r'不(?:要|用)?(?:选样|比较|对比)|(?:不|不要|仅|只)(?:查|核对)?(?:指数)?权重|'
                         r'香港|港股|美股|海外|假设|假如', text)):
        return None
    try:
        validate_stated_source(task, 'official')
    except ContractError:
        return None
    brief = (_OPERATING_STUDY_ASK if version == INDUSTRY_SAMPLE_VERSION else _BRIEF_INDUSTRY_ASK).fullmatch(text)
    spoken = version == INDUSTRY_SAMPLE_VERSION and _spoken_broker_study(task, text)
    matched = [(code, profile) for code, profile in _INDUSTRY_PROFILES.items()
               if (version == INDUSTRY_SAMPLE_VERSION or not profile.get('operating_drivers'))
               and _index_allowed(task, code, profile) and subject_allowed(task, profile['topic'])
               and (re.search(profile['topic_pattern'], text) or spoken and profile['topic'] == '券商'
                    or brief and (profile['topic'] in brief['topic']
                                  or profile['topic'] == '券商' and brief['topic'] == '证券行业'))]
    if len(matched) != 1:
        return None
    index_code, profile = matched[0]
    explicit = set(declared_security_codes(task)) - {index_code}
    if explicit or (not _INDUSTRY_ACTION.search(text) and not brief and not spoken):
        return None
    if direct and (_ADDITIONAL_RESEARCH.search(text) or task.get('materials') or task.get('pending_materials')
                   or task.get('calculation_specs') or task.get('update_from')):
        return None
    if direct and profile.get('operating_drivers') and not brief:
        return None
    if (direct and version == INDUSTRY_SAMPLE_VERSION and brief and brief['metrics']
            and not profile.get('operating_drivers')):
        return None
    cutoff = market_date(task['cutoff'])
    dates = {int(year) for year in _HALF_YEAR.findall(text)}
    if version == INDUSTRY_SAMPLE_VERSION:
        dates.update(int(cutoff[:4]) - (period == '去年') for period in re.findall(r'(今年|去年)上半年', text))
    if len(dates) > 1 or not dates and re.search(r'20\d{2}\s*年', text):
        return None
    latest_comparable = int(cutoff[:4]) - int(cutoff[5:] < '09-01')
    year = next(iter(dates), latest_comparable)
    if (year < 2001 or str(year)+'-06-30' > cutoff
            or year < latest_comparable and not re.search(r'现有(?:成分|样本)|当前(?:成分|样本)|最新(?:成分|样本)', text)):
        return None
    width = _SAMPLE_COUNT.search(text)
    count = int(_number(width[1])) if width else 3
    if not 2 <= count <= 5:
        return None
    scope = dict(index_code=index_code, index_name=profile['name'], industry=profile['topic'], count=count,
                 periods=[str(year-1)+'0630', str(year)+'0630'], question_ids=[question['id']],
                 revenue_bridge=profile['topic'] == '券商')
    if profile.get('operating_drivers'):
        scope['operating_drivers'] = True
    return scope


def selected_industry_sample(task, scope=None):
    """Rank one actual publisher weight table without authorizing its whole universe."""
    scope = industry_operating_scope(task) if scope is None else scope
    if scope is None:
        return None
    sheets = {}
    for record in task.get('source_records', ()):
        if record.get('snapshot', {}).get('index_code') != scope['index_code']:
            continue
        try:
            parsed = verified_weight_source(task, record)
        except (ContractError, KeyError, TypeError, ValueError):
            continue
        dates = sheets.setdefault(parsed['observation_date'], {})
        dates.setdefault(record['snapshot']['original_sha256'], (record, parsed))
    if not sheets:
        return None
    day = max(sheets)
    if len(sheets[day]) != 1:
        return None
    (record, parsed), = sheets[day].values()
    members = parsed['members']
    # A missing weight might outrank the last selected company.
    if (not parsed['weight_sum_is_complete'] or len(members) < scope['count']
            or abs(Decimal(parsed['weight_sum'])-100) > max(Decimal('0.05'), Decimal(len(members))*Decimal('0.0005'))):
        return None
    eligible = [member for member in members if subject_allowed(task, member['code'])
                and subject_allowed(task, member['name']) and member['weight'] > 0]
    selected = sorted(eligible, key=lambda member: (-Decimal(str(member['weight'])), member['code']))[:scope['count']]
    if len(selected) < (scope['count'] if task.get(INDUSTRY_SAMPLE_POLICY) == INDUSTRY_SAMPLE_VERSION else 2):
        return None
    return dict(scope, source_ref=record['id'], workbook_date=day, index_size=len(members),
                selected=[{key: member[key] for key in ('code', 'name', 'weight', 'excel_row')} for member in selected],
                selected_weight=str(sum(Decimal(str(member['weight'])) for member in selected)))


def industry_sample_requests(task, scope):
    """Choose the provider's published weights via its current catalogue."""
    from .evidence import current_query_reference

    if selected_industry_sample(task, scope):
        return []
    code = scope['index_code']
    choices = {row['entry_id']: row for tool in official_index_shapes(task)
               for row in tool.get('official_index_file_choices', [])
               if row['index_code'] == code and row['role'] == 'weights'}
    if len(choices) == 1:
        row, = choices.values()
        operation = 'index_file'
        parameters = dict(catalog_id=row['catalog_id'], entry_id=row['entry_id'], as_of=None)
    elif not choices and not any(record.get('source') == 'official' and record.get('operation') == 'index_catalog'
                                  and record.get('parameters', {}).get('index_code') == code
                                  and current_query_reference(task, record) for record in task.get('source_records', ())):
        operation = 'index_catalog'
        parameters = dict(index_code=code)
    else:
        return []
    return [dict(id='industry_sample_'+operation, source='official', operation=operation, parameters=parameters,
                 question_ids=scope['question_ids'], selection_reason='以指数编制方公布的当前权重和证券名单确定行业研究样本。')]


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


_DIRECT_WEIGHT_WORDS = re.compile(
    r'请|帮我|只|仅|核对|查询|查一下|列出|查看|给出|生成报告|指数|官方|中证|原表|成[分份](?:股|券)?'
    r'|权重|总计|合计|总和|总|以及|的|在|及|和|与|[，。,.；;、！？?!：:\s]')
_CURRENT_WEIGHT_WORDS = re.compile(r'最新一期|最近一期|最新|当前')


def _only_weight_question(task, code, text):
    from .draft_sources import local_scope_text
    from .market_series import _IFIND_ONLY
    from .numeric_integrity import draft_day_points

    if task.get(DRAFT_WEIGHT_POLICY) in DRAFT_LATEST_WEIGHT_VERSIONS:
        text = local_scope_text(text)
    for start, end, _ in reversed(list(draft_day_points(text, ()))):
        text = text[:start]+text[end:]
    if task.get(DRAFT_WEIGHT_POLICY) in DRAFT_LATEST_WEIGHT_VERSIONS:
        text = _CURRENT_WEIGHT_WORDS.sub('', _IFIND_ONLY.sub('', text))
    text = _WEIGHT_RANK.sub('', text)
    for alias in sorted([code, *CSI_PROFILES[code]['aliases']], key=len, reverse=True):
        text = text.replace(alias, '')
    return not _DIRECT_WEIGHT_WORDS.sub('', text)


def direct_weight_scope(task, *, clause=None):
    """Only use direct acquisition when the complete question is a weight readout."""
    if clause is not None and task.get(DRAFT_WEIGHT_POLICY) != COMPOUND_WEIGHT_VERSION:
        return None
    scope = _weight_scope(task, clause=clause)
    text = scope_question(task) if clause is None else clause
    return scope if scope and _only_weight_question(task, scope['code'], text) else None


def stated_weight_requests(task, scope):
    """Select the registered publisher file, keeping the user's observation date."""
    from .evidence import current_query_reference

    sheet = stated_weight_sheet(task, scope)
    refresh = _refresh_latest_weight(task, scope)
    if (scope['day'] and scope['day'] > market_date(task['cutoff'])
            or sheet['reason'] == 'source_restricted' or not refresh and sheet['reason'] != 'missing'):
        return []
    code = scope['code']
    if refresh and any(record.get('source') == 'official' and record.get('operation') == 'index_file'
                       and record.get('snapshot', {}).get('index_code') == code
                       and record['snapshot'].get('role') == 'weights' and current_query_reference(task, record)
                       for record in task.get('source_records', ())):
        return []
    choices = {row['entry_id']:row for tool in official_index_shapes(task)
               for row in tool.get('official_index_file_choices', [])
               if row['index_code'] == code and row['role'] == 'weights'}
    if len(choices) == 1:
        row, = choices.values()
        operation = 'index_file'
        parameters = dict(catalog_id=row['catalog_id'],entry_id=row['entry_id'],as_of=scope['day'])
    elif not choices and not any(record.get('source') == 'official' and record.get('operation') == 'index_catalog'
                                  and record.get('parameters', {}).get('index_code') == code
                                  and current_query_reference(task, record) for record in task.get('source_records', ())):
        operation = 'index_catalog'
        parameters = dict(index_code=code)
    else:
        return []
    return [dict(id='stated_weight_'+fingerprint([operation,parameters])[:24], source='official',
                 operation=operation, parameters=parameters, question_ids=[task['questions'][0]['id']],
                 selection_reason='按原问题核对中证指数权重原表及实际日期。')]


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
    industry_scope = industry_operating_scope(task)
    sample = selected_industry_sample(task, industry_scope) if industry_scope else None
    industry_files = {row['id'] for row in task.get('source_records', ()) if industry_scope
                      and row.get('snapshot', {}).get('index_code') == industry_scope['index_code']}
    for source in retained_official_member_sources(task):
        if source['artifact_id'] in industry_files:
            continue
        codes.update(member['code'] for member in source['members'])
    if sample:
        codes.update(member['code'] for member in sample['selected'])
    return sorted(codes)
