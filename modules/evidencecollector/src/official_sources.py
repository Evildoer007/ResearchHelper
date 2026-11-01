"""Fixed official release navigation; downloaded text is never approval.

Only retained registered directory links can be fetched. No web search, credentials,
redirects, active content or guessed document URLs enter this adapter.
"""
import base64
import hashlib
from functools import lru_cache
from html.parser import HTMLParser
import re
import time
import urllib.error
from urllib.parse import urljoin, urlsplit

from .contracts import ContractError, fingerprint, market_date, timepoint

ROOT_URL = 'https://www.stats.gov.cn/sj/zxfbhjd/'
FED_CALENDAR_URL = 'https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm'
MAX_BYTES = 4*1024*1024
REGISTRY = {
    'official.release_catalog': dict(version=1,kind='已登记官方发布目录',raw_only=True,fields={}),
    'official.release_original': dict(version=1,kind='目录绑定的官方HTML原文',raw_only=False,fields={}),
}
ARTICLE = re.compile(r'/sj/(?:zxfbhjd|zxfb|sjjd)/\d{6}/t(\d{8})_\d+\.html')
FED_STATEMENT = re.compile(r'/newsevents/pressreleases/monetary(\d{8})a\.htm')
ENGLISH_MONTHS = ('January', 'February', 'March', 'April', 'May', 'June', 'July',
                  'August', 'September', 'October', 'November', 'December')
FED_HEADER_DATE = re.compile(r'('+'|'.join(ENGLISH_MONTHS)+r') ([0-9]{1,2}), ([0-9]{4})', re.I)
STATISTICAL_MONTH = re.compile(r'(?<!\d)(\d{4})年(0?[1-9]|1[0-2])月(?:份)?(?!\s*\d{1,2}日)')


def statistical_period_anchors(task, record, parts=None):
    """Literal completed statistical months, not inferred month-end dates."""
    if (type(task.get('statistical_period_version')) is not int
            or task['statistical_period_version'] != 1 or task.get('research_mode') != 'current'
            or task.get('intent') == '原件内容核对' or not isinstance(record, dict)
            or record.get('source') != 'official' or record.get('operation') != 'release_original'):
        return []
    try:
        article_url(record.get('snapshot', {}).get('official_origin', {}).get('url'), channel='nbs_release')
        validate_original(task, record)
    except (ContractError, KeyError, TypeError):
        return []
    actual = {part['locator']: part['text'] for part in record['snapshot'].get('parts', [])}
    selected = record['snapshot'].get('parts', []) if parts is None else parts
    cutoff_month = market_date(task['cutoff'])[:7]
    result, seen = [], set()
    for part in selected:
        text = part.get('text')
        if (not isinstance(text, str) or not text or part.get('locator') not in actual
                or text not in actual[part['locator']]):
            continue
        for match in STATISTICAL_MONTH.finditer(text):
            if int(match[1]) < 1:
                continue
            value = f'{int(match[1]):04d}-{int(match[2]):02d}'
            if value >= cutoff_month:
                continue  # Current partial months need another explicit contract.
            key = (value, part['locator'], match.group())
            if key not in seen:
                result.append(dict(observation_period=dict(frequency='month', value=value),
                    part=part['locator'], quote=match.group()))
                seen.add(key)
    return result


def validate_statistical_period(task, record, item):
    period = item.get('observation_period')
    if (not isinstance(period, dict) or set(period) != {'frequency', 'value'}
            or period.get('frequency') != 'month' or 'observation_date' in item
            or item.get('period') != period.get('value')):
        raise ContractError('月度统计须保留同一所属月，不填写或推算日度观测日期')
    locator = item.get('record_locator', {})
    matches = [anchor for anchor in statistical_period_anchors(task, record)
               if anchor['observation_period'] == period and anchor['part'] == locator.get('period_part')
               and anchor['quote'] == locator.get('period_quote')]
    if len(matches) != 1:
        raise ContractError('统计所属月缺少同任务统计局原文的明确位置，未批准月度用途')
    if 'value' in item:
        text = str(item.get('text', ''))
        explicit = {f'{int(match[1]):04d}-{int(match[2]):02d}'
                    for match in STATISTICAL_MONTH.finditer(text)}
        if explicit and explicit != {period['value']}:
            raise ContractError('数字摘录包含其他或多个统计月份，须明确选择对应原值范围')
        if not explicit:
            # A bare monthly paragraph inherits its own actual article title,
            # not an unrelated historical row elsewhere in the same table.
            title = record['snapshot']['official_origin']['title']
            headings = [part['text'] for part in record['snapshot']['parts']
                        if part['locator'].startswith('标题') and title in part['text']]
            declared = {f'{int(match[1]):04d}-{int(match[2]):02d}'
                        for heading in headings for match in STATISTICAL_MONTH.finditer(heading)}
            short = {int(match[1]) for match in re.finditer(
                r'(?<![\d年])(0?[1-9]|1[0-2])月(?:份)?(?!\s*\d{1,2}日)', text)}
            if declared != {period['value']} or short and short != {int(period['value'][-2:])}:
                raise ContractError('数字所在段落月份与实际原文标题或所选月份不一致，不能借用其他表行的日期')


def fomc_statement_date_match(record, text, quote, observed):
    """A literal dated statement header, not first publication or effect time.

    Keep the generic publication grammar unchanged. Only an already-bound
    FOMC original can use this representation; last-update/implementation
    paragraphs and dates inferred from paths alone are not accepted.
    """
    if (record.get('source')!='official' or record.get('operation')!='release_original'
            or not isinstance(quote,str) or text!=quote):
        return None
    url=record.get('snapshot',{}).get('official_origin',{}).get('url')
    try:
        article_url(url,channel='fed_fomc')
    except ContractError:
        return None
    match=FED_HEADER_DATE.fullmatch(quote)
    if match is None:
        return None
    month=next(i for i,name in enumerate(ENGLISH_MONTHS,1) if name.lower()==match[1].lower())
    value=f'{int(match[3]):04d}-{month:02d}-{int(match[2]):02d}'
    raw=FED_STATEMENT.fullmatch(urlsplit(url).path)[1]
    path_date=raw[:4]+'-'+raw[4:6]+'-'+raw[6:]
    return match if value==observed==path_date else None


def current_statement_temporal_contract(task, record, view, availability):
    """Expose the fixed adapter's supported use, not a publication receipt.

    This adapter retains the current statement but does not implement a
    first-publication clock verifier. Only new tasks opt into its bounded
    extraction grammar; other documents and legacy tasks are unchanged.
    """
    if (task.get('official_temporal_contract_version') != 1
            or task.get('official_policy_channels_version') != 1
            or record.get('source') != 'official' or record.get('operation') != 'release_original'
            or not isinstance(availability, dict) or availability.get('kind') != 'current_original'):
        return None
    url = record.get('snapshot', {}).get('official_origin', {}).get('url')
    try:
        article_url(url, channel='fed_fomc')
    except ContractError:
        return None
    validate_original(task, record)
    raw_date = FED_STATEMENT.fullmatch(urlsplit(url).path)[1]
    observed = raw_date[:4] + '-' + raw_date[4:6] + '-' + raw_date[6:]
    actual = {part['locator']: part['text'] for part in record['snapshot']['parts']}
    candidates = []
    for part in view.get('parts', []):
        text = part.get('text')
        if actual.get(part.get('locator')) != text:
            continue
        if fomc_statement_date_match(record, text, text, observed):
            candidate = dict(date=observed, part=part['locator'], quote=text)
            if candidate not in candidates:
                candidates.append(candidate)
    return dict(version=1, allowed_modes=['unknown_current_original'], date_candidates=candidates,
        first_publication_verified=False, source_or_financial_approval=False,
        rule='本适配仅登记当前声明用途；period是独立标题所载声明日期，不能填写为已核验首发。'
             '按date_candidates显式选择日期及定位，availability保持原样，temporal_use为current_version。'
             '没有读到标题时先读取，不从路径、Last Update或取得时间补造；历史及因果需求仍保留缺口。')


def article_url(value, *, channel='nbs_release'):
    if not isinstance(value,str): raise ContractError('官方原文地址必须为文字')
    parsed=urlsplit(value)
    if channel not in ('nbs_release', 'fed_fomc'):
        raise ContractError('官方原文渠道未登记')
    host, pattern = (('www.stats.gov.cn', ARTICLE) if channel == 'nbs_release'
                     else ('www.federalreserve.gov', FED_STATEMENT))
    if (parsed.scheme!='https' or parsed.netloc!=host or parsed.query
            or parsed.fragment or not pattern.fullmatch(parsed.path)):
        raise ContractError('仅允许已登记官方栏目中的无凭据HTTPS原文')
    return value


def catalogue_url(page, *, channel='nbs_release', maximum_page=3):
    if channel == 'fed_fomc':
        if page != '0':
            raise ContractError('FOMC仅登记固定会议日历，不猜年度目录或自动翻页')
        return FED_CALENDAR_URL
    if channel != 'nbs_release':
        raise ContractError('官方目录渠道未登记')
    if (type(maximum_page) is not int or not 0<=maximum_page<999999999
            or not isinstance(page,str) or re.fullmatch(r'0|[1-9][0-9]{0,8}',page) is None
            or int(page)>maximum_page):
        raise ContractError('官方目录页码超出本任务已登记或实际声明的范围，不自动翻页')
    return ROOT_URL+('' if page=='0' else 'index_'+page+'.html')


def pagination_enabled(task):
    version=task.get('official_catalogue_pagination_version')
    if version is None:return False
    if type(version) is not int or version!=1:raise ContractError('官方目录分页版本未登记')
    return True


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__();self.active=False;self.parts=[];self.current=[]
    def handle_starttag(self,tag,attrs):
        if tag=='script':self.active=True;self.current=[]
    def handle_data(self,text):
        if self.active:self.current.append(text)
    def handle_endtag(self,tag):
        if tag=='script' and self.active:
            self.parts.append(''.join(self.current));self.active=False


_PAGINATION_CALL=re.compile(r'''(?m)^[ \t]*(?P<call>createPageHTML)\([ \t]*(?P<count>[1-9][0-9]{0,8})[ \t]*,[ \t]*(?P<page>0|[1-9][0-9]{0,8})[ \t]*,[ \t]*(['"])index\4[ \t]*,[ \t]*(['"])html\5[ \t]*\)[ \t]*;?[ \t]*$''')


def _top_level_calls(script):
    """Read literal metadata without running JavaScript or its instructions."""
    candidates={match.start('call'):match for match in _PAGINATION_CALL.finditer(script)}
    found=[];index=0;braces=0;parentheses=0
    while index<len(script):
        if script.startswith('//',index):
            end=script.find('\n',index+2);index=len(script) if end<0 else end+1;continue
        if script.startswith('/*',index):
            end=script.find('*/',index+2);index=len(script) if end<0 else end+2;continue
        char=script[index]
        if char in ('"',"'",'`'):
            quote=char;index+=1
            while index<len(script):
                if script[index]=='\\':index+=2;continue
                if script[index]==quote:index+=1;break
                index+=1
            continue
        if index in candidates and braces==parentheses==0:found.append(candidates[index])
        if char=='{':braces+=1
        elif char=='}':braces-=1
        elif char=='(':parentheses+=1
        elif char==')':parentheses-=1
        if braces<0 or parentheses<0:return []
        index+=1
    return found


@lru_cache(maxsize=4)
def _pagination_declaration(payload):
    reader=_Scripts();reader.feed(payload.decode('utf-8-sig'));reader.close()
    declarations=[(int(match['count']),int(match['page'])) for script in reader.parts
                  for match in _top_level_calls(script)]
    if len(declarations)!=1:return None
    count,page=declarations[0]
    return declarations[0] if 0<=page<count else None


def _catalogue_has_dispatch(task,record):
    return (record.get('source')=='official' and record.get('operation')=='release_catalog'
        and record.get('kind')=='api' and record.get('status')=='成功待核验'
        and record.get('task_id')==task['id'] and record.get('research_version')==task['version']
        and record.get('request_hash')==fingerprint(dict(source='official',operation='release_catalog',
            parameters=record.get('parameters')))
        and any(event.get('kind')=='data_requests' and event.get('source')=='official'
            and event.get('operation')=='release_catalog' and event.get('status')=='成功'
            and event.get('fingerprint')==record.get('request_hash') and event.get('response_snapshot_hash')==record.get('hash')
            and all(event.get(key) is True for key in ('transport_isolated','network_request_started','network_worker_reaped'))
            for event in task.get('ledger',{}).get('dispatches',[])))


def catalogue_pagination(task):
    """Derive page bounds from the task's actual metered homepage response.

    Imported snapshots, failed/cancelled dispatches and changed bytes cannot
    expand the registered four bootstrap pages. No completeness is approved.
    """
    if not pagination_enabled(task):return None
    for record in reversed(task.get('source_records',[])):
        parameters=dict(channel='nbs_release',page='0')
        if (record.get('source')!='official' or record.get('operation')!='release_catalog'
                or record.get('kind')!='api' or record.get('status')!='成功待核验'
                or record.get('task_id')!=task['id'] or record.get('research_version')!=task['version']
                or record.get('parameters')!=parameters
                or fingerprint(record.get('snapshot'))!=record.get('hash')):continue
        request_hash=fingerprint(dict(source='official',operation='release_catalog',parameters=parameters))
        if record.get('request_hash')!=request_hash:continue
        if not _catalogue_has_dispatch(task,record):continue
        try:
            raw=base64.b64decode(record['snapshot']['original_base64'],validate=True)
            if len(raw)>MAX_BYTES or parse_catalogue(raw,task['cutoff'])!=record['snapshot']:continue
            declared=_pagination_declaration(raw)
        except (ContractError,ValueError,KeyError,UnicodeError):continue
        if declared is None or declared[1]!=0:continue
        return dict(version=1,root_artifact_id=record['id'],root_snapshot_hash=record['hash'],
            root_request_hash=request_hash,page_count=declared[0],minimum_page=0,maximum_page=declared[0]-1,
            automatic_traversal=False,completeness_verified=False,financial_approval=False)
    return None


def _catalogue_maximum(task):
    declared=catalogue_pagination(task)
    return declared['maximum_page'] if declared is not None else 3


def _is_catalogue_address(value):
    """Transport syntax only; task authority is checked before dispatch."""
    if not isinstance(value,str):return False
    address=urlsplit(value)
    if (address.scheme!='https' or address.netloc!='www.stats.gov.cn' or address.query or address.fragment):return False
    match=re.fullmatch(r'/sj/zxfbhjd/(?:index_([1-9][0-9]{0,8})\.html)?',address.path)
    return match is not None


class ReleaseLinks(HTMLParser):
    def __init__(self, channel='nbs_release'):
        super().__init__(convert_charrefs=True)
        self.channel=channel
        self.root = ROOT_URL if channel == 'nbs_release' else FED_CALENDAR_URL
        self.href=None;self.text=[];self.links=[];self.skipped=0
    def handle_starttag(self,tag,attrs):
        if tag=='a':
            self.href=dict(attrs).get('href');self.text=[]
    def handle_data(self,value):
        if self.href is not None:self.text.append(value)
    def handle_endtag(self,tag):
        if tag!='a' or self.href is None:return
        title=' '.join(''.join(self.text).split())
        try:
            url=article_url(urljoin(self.root,self.href), channel=self.channel)
            if self.channel == 'fed_fomc':
                raw=FED_STATEMENT.fullmatch(urlsplit(url).path)[1]
                title='FOMC声明 '+raw[:4]+'-'+raw[4:6]+'-'+raw[6:]
            if title and len(title)<=300:self.links.append((url,title))
        except ContractError:self.skipped+=1
        self.href=None;self.text=[]


def _release_links(payload, channel):
    reader = ReleaseLinks(channel)
    reader.feed(payload.decode('utf-8-sig'))
    reader.close()
    return tuple(reader.links), reader.skipped


@lru_cache(maxsize=4)
def _cached_release_links(payload, channel):
    """Cache immutable syntax only, never task scope or source approval."""
    return _release_links(payload, channel)


def parse_catalogue(payload, cutoff, *, channel='nbs_release'):
    catalogue_url('0', channel=channel)  # Validate the channel independently.
    # Repeated provenance checks often inspect identical retained bytes. Every
    # check still validates channel, cutoff, snapshot and task independently.
    # Bound memory by both entry count and the existing transport byte cap.
    links, skipped = (_cached_release_links(payload, channel) if len(payload) <= MAX_BYTES
                      else _release_links(payload, channel))
    rows=[];seen=set();skipped_future=0
    for url,title in links:
        pattern=ARTICLE if channel == 'nbs_release' else FED_STATEMENT
        raw=pattern.fullmatch(urlsplit(url).path)[1]
        date=raw[:4]+'-'+raw[4:6]+'-'+raw[6:]
        try:timepoint(date)
        except ContractError:continue
        if date>market_date(cutoff):skipped_future+=1;continue
        if url in seen:continue
        seen.add(url)
        if len(rows)>=100:break
        prefix='nbs_' if channel == 'nbs_release' else 'fed_fomc_'
        rows.append(dict(id=prefix+hashlib.sha256(url.encode()).hexdigest()[:24],
            title=title,url=url,path_date_hint=date,published_at=None,
            limitation='路径日期仅供定位，不证明首发时刻或历史版本。'))
    if not links:raise ContractError('官方目录未识别到已登记原文链接，不把结构变化当作空目录')
    return dict(entries=rows,coverage=dict(completeness='未证实',maximum_entries=100,
        skipped_future=skipped_future,skipped_links=skipped),
        original_base64=base64.b64encode(payload).decode(),original_sha256=hashlib.sha256(payload).hexdigest())


def release_choices(task):
    choices=[]
    for record in task.get('source_records',[]):
        if record.get('source')!='official' or record.get('operation')!='release_catalog':continue
        if (record.get('task_id')!=task['id'] or record.get('research_version')!=task['version']
                or record.get('status')!='成功待核验' or fingerprint(record.get('snapshot'))!=record.get('hash')):continue
        try:
            validate_request(task,'release_catalog',record.get('parameters'))
            if record.get('request_hash')!=fingerprint(dict(source='official',operation='release_catalog',
                                                           parameters=record['parameters'])):continue
        except ContractError:continue
        snapshot=record['snapshot']
        try:
            payload=base64.b64decode(snapshot['original_base64'],validate=True)
            channel=record['parameters']['channel']
            if parse_catalogue(payload,task['cutoff'],channel=channel)!=snapshot:continue
        except (ContractError,ValueError,KeyError):continue
        choices.extend(dict(catalog_id=record['id'],release_id=row['id'],title=row['title'])
                       for row in snapshot['entries'])
    return choices if pagination_enabled(task) else choices[:100]


def selection_references(task):
    """One stable reference for a validated catalogue-entry pair, not a URL.

    The original byte validation remains in release_choices. Task identity,
    declared scope and catalogue content bind each reference; adding another
    page never renumbers an existing selection or grants financial approval.
    """
    if (type(task.get('official_selection_reference_version')) is not int
            or task['official_selection_reference_version'] != 1):
        return []
    records = {record['id']: record for record in task.get('source_records', [])}
    result = []
    for choice in release_choices(task):
        # Plan questions and request lists can be refined after this selection.
        # Bind the immutable user scope, not its derived plan's input_hash:
        # adopting a reference request must not invalidate that same reference.
        binding = dict(task_id=task['id'], research_version=task['version'],
            scope_hash=fingerprint({key: task[key] for key in ('question', 'entry', 'intent', 'objects', 'cutoff')}),
            catalog_id=choice['catalog_id'], release_id=choice['release_id'],
            catalog_hash=records[choice['catalog_id']]['hash'])
        result.append(dict(choice, selection_ref='rh_release_' + fingerprint(binding)[:24]))
    if len({item['selection_ref'] for item in result}) != len(result):
        raise ContractError('官方原文引用不唯一，未选择任何条目')
    return result


def _release_parameters(task, parameters):
    if isinstance(parameters, dict) and set(parameters) == {'selection_ref'}:
        matches = [item for item in selection_references(task)
                   if item['selection_ref'] == parameters['selection_ref']]
        if len(matches) != 1:
            raise ContractError('官方原文引用不属于本任务当前目录，请读取当前可选条目')
        return {key: matches[0][key] for key in ('catalog_id', 'release_id')}
    return parameters


def canonical_request_parameters(task, operation, parameters):
    """Resolve selection syntax before cache, ledger or provider dispatch.

    Saved acquisition records always retain the existing exact catalogue and
    release IDs. A reference does not replace their provenance contract.
    """
    canonical = _release_parameters(task, parameters) if operation == 'release_original' else parameters
    if (operation=='release_catalog' and pagination_enabled(task) and isinstance(canonical,dict)
            and set(canonical)=={'channel','page'} and type(canonical['page']) is int):
        canonical=dict(canonical,page=str(canonical['page']))
    validate_request(task, operation, canonical)
    return canonical


def validate_request(task,operation,parameters):
    if task.get('official_channels_version')!=1:raise ContractError('本任务未登记官方发布渠道')
    if operation=='release_catalog':
        if not isinstance(parameters,dict) or set(parameters)!={'channel','page'}:
            raise ContractError('官方目录须明确固定栏目及页码')
        if (parameters['channel']=='fed_fomc' and task.get('official_policy_channels_version')!=1):
            raise ContractError('本任务未登记FOMC固定官方渠道')
        maximum=_catalogue_maximum(task) if pagination_enabled(task) else 3
        return catalogue_url(parameters['page'],channel=parameters['channel'],maximum_page=maximum)
    if operation!='release_original':raise ContractError('官方发布操作未登记')
    parameters = _release_parameters(task, parameters)
    if not isinstance(parameters,dict) or set(parameters)!={'catalog_id','release_id'}:
        raise ContractError('官方原文须选择已留存目录，不接受任意URL')
    matches=[item for item in release_choices(task) if all(item[key]==parameters[key] for key in parameters)]
    if len(matches)!=1:raise ContractError('官方原文选择不属于本任务当前版本目录')
    record=next(record for record in task['source_records'] if record['id']==parameters['catalog_id'])
    return next(row for row in record['snapshot']['entries'] if row['id']==parameters['release_id'])


def request_shapes(task):
    if task.get('official_channels_version')!=1:return []
    common=dict(source='official',credential_required=False,
        selection_rule='iFinD仍为优先来源；选择官方原文须说明问题需要，不因权限失败静默换源。')
    result = [dict(common,qualified_id_for_lookup_only='official.release_catalog',
        operation='release_catalog',parameter_keys=['channel','page'],
        parameter_options=dict(channel=['nbs_release'],page=['0','1','2','3']),raw_only=True,
        limitation='每次只取指定一页，不自动翻页，目录不证明覆盖完整，也不是正文证据。'),
        dict(common,qualified_id_for_lookup_only='official.release_original',
             operation='release_original',parameter_keys=['catalog_id','release_id'],
             available_selections=release_choices(task),raw_only=False,
             limitation='保留原始HTML字节及静态摘录；下载不等于来源、时点或适用性核验。')]
    if task.get('official_policy_channels_version')==1:
        catalogue=result[0]
        catalogue.pop('parameter_options')
        catalogue['available_parameter_sets']=[dict(channel='nbs_release',page=str(page)) for page in range(4)] + [
            dict(channel='fed_fomc',page='0')]
        catalogue['channel_rule']='nbs_release为统计局目录；fed_fomc只取固定会议日历中的声明，不含会议纪要、实施说明或其他栏目，不证明目录完整或首发时刻。'
    if pagination_enabled(task):
        catalogue=result[0];declared=catalogue_pagination(task)
        catalogue.pop('parameter_options',None);catalogue.pop('available_parameter_sets',None)
        catalogue['page_number_profiles']=[dict(channel='nbs_release',minimum=0,
            maximum=declared['maximum_page'] if declared else 3)]
        if task.get('official_policy_channels_version')==1:
            catalogue['page_number_profiles'].append(dict(channel='fed_fomc',minimum=0,maximum=0))
        catalogue['pagination_proof']=declared
        catalogue['model_page_encoding']=dict(version=1,rule='模型按登记范围选择整数page或对应的十进制页码文字；内核只将整数序列化为实际请求文字。'
            '既有请求记录保留实际文字参数，不重复查询；每次仅下载所选一页，不自动遍历。'
            '页码不代表固定日期区间；按实际条目日期与问题选择，不把目录声明页数视为完整性核验。')
    if (type(task.get('official_selection_reference_version')) is int
            and task['official_selection_reference_version'] == 1):
        choices = selection_references(task)
        original = result[1]
        original.update(parameter_keys=['selection_ref'],
            available_parameter_sets=[{'selection_ref': item['selection_ref']} for item in choices],
            available_selections=[{key: item[key] for key in ('selection_ref', 'title')} for item in choices],
            selection_reference_version=1,
            selection_rule='只复制一个selection_ref；内核将其解析为同一实际目录的条目，不拼接catalog_id、release_id或URL。'
                           '尚无条目时先取所需目录，不猜引用；引用不批准最新性、数值或首发时间。')
    return result


def validate_original(task,record):
    selected=validate_request(task,'release_original',record.get('parameters'))
    if (record.get('task_id')!=task['id'] or record.get('research_version')!=task['version']
            or record.get('source')!='official' or record.get('operation')!='release_original'
            or record.get('request_hash')!=fingerprint(dict(source='official',operation='release_original',
                                                           parameters=record.get('parameters')))
            or fingerprint(record.get('snapshot'))!=record.get('snapshot_hash')
            or record.get('snapshot',{}).get('official_origin')!=selected):
        raise ContractError('官方原件的来源、主体或目录绑定已变化')


def download_html(value,timeout):
    """One pinned public HTTPS GET, with a shared read deadline and byte cap."""
    if not _is_catalogue_address(value) and value!=FED_CALENDAR_URL:
        channel='fed_fomc' if urlsplit(value).netloc=='www.federalreserve.gov' else 'nbs_release'
        article_url(value,channel=channel)
    from .evidence import _PublicFilingConnection
    parsed=urlsplit(value);deadline=time.monotonic()+timeout
    connection=_PublicFilingConnection(parsed.hostname,timeout=timeout,use_local_proxy=True)
    try:
        connection.request('GET',parsed.path,headers={'Accept':'text/html','Accept-Encoding':'identity'})
        response=connection.getresponse()
        if response.status!=200:
            raise urllib.error.HTTPError(value,response.status,'官方站点未返回成功原文',response.headers,None)
        if response.getheader('Content-Type','').split(';')[0].strip().lower() not in ('text/html','application/xhtml+xml'):
            raise ContractError('官方原文不是HTML，不按地址后缀猜测内容')
        declared=response.getheader('Content-Length')
        if declared is not None and (not declared.isdigit() or int(declared)>MAX_BYTES):
            raise ContractError('官方原文长度异常或超出4MB')
        chunks=[];size=0
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise ContractError('官方原文读取超过本次时限')
            if connection.sock is not None:connection.sock.settimeout(remaining)
            data=response.read1(min(65536,MAX_BYTES+1-size))
            if not data:break
            size+=len(data)
            if size>MAX_BYTES:raise ContractError('官方原文超出4MB')
            chunks.append(data)
        if declared is not None and size!=int(declared):raise ContractError('官方原文未完整接收')
        if time.monotonic()>=deadline:raise ContractError('官方原文读取超过本次时限')
        payload=b''.join(chunks);payload.decode('utf-8-sig')
        return payload
    finally:connection.close()
