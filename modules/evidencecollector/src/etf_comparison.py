"""Period, benchmark, performance and recurring fees from ETF interim reports."""
from collections import defaultdict
from decimal import Decimal
import re

from .announcement_sources import CHANNELS, ETF_CHANNELS, _NO_OFFICIAL
from .contracts import ContractError, market_date
from .etf_listing import POLICY as LISTING_POLICY, name_key, resolve_names
from .etf_trading import trading_terms
from .fee_sources import annual_fee_mentions
from .parallel_study import FUND_GROUP, branch_question

PERFORMANCE = 'etf_reported_nav_return'
BENCHMARK = 'etf_reported_benchmark_return'
MANAGEMENT = 'etf_reported_management_fee'
CUSTODY = 'etf_reported_custody_fee'
ASSETS = 'etf_reported_net_assets'
TRACKING_ERROR = 'etf_reported_annual_tracking_error'
FIELDS = {
    PERFORMANCE: ('份额净值增长率', '%'),
    BENCHMARK: ('业绩比较基准收益率', '%'),
    MANAGEMENT: ('年管理费率', '%/年'),
    CUSTODY: ('年托管费率', '%/年'),
    ASSETS: ('期末基金资产净值', '人民币元'),
    TRACKING_ERROR: ('年化跟踪误差', '%'),
}
REQUIRED_FIELDS = {PERFORMANCE, BENCHMARK, MANAGEMENT, CUSTODY}
CELL_KIND = 'etf_interim_report_cell'
FEE_SUM = 'etf_recurring_fee_sum_v1'
TRACKING_GAP = 'etf_interim_benchmark_gap'
HOLDING_POLICY = 'draft_etf_holding_comparison_version'

_YEAR = r'(?:19|20)\d{2}'
_AFTER_YEAR = r'(?:年\s*(?:上半年|中期|半年度|[1一]月(?:至|到|[-—–])\s*[6六]月)|[-\s]*H1\b)'
_SCOPE_PERIOD = re.compile(r'(?<!\d)('+_YEAR+r')\s*'+_AFTER_YEAR, re.I)
_RELATIVE_PERIOD = re.compile(r'(今年|去年|前年)\s*(?:上半年|中期|半年度)')
_COMPARE = re.compile(r'比较|对比|对照|研究|核对|想知道|想了解|请问|看看')
_FUND = re.compile(r'ETF|交易型开放式', re.I)
_SEPARATOR = re.compile(r'\s*(?:以及|还是|和|与|及|、)\s*')
_TARGET_BOUNDARY = re.compile(
    r'管理费|托管费|费率|持有费|持有成本|跟踪(?:表现|偏离)|净值(?:增长率|收益率)|'
    r'日均成交|成交额|成交金额|交投活跃度|流动性|收盘折溢价|折溢价|大额|盘口|买卖价差|执行条件')
_TARGET_SUFFIX = re.compile(r'(?:[，,]\s*(?:请|并|再)?\s*(?:重点|分别)?\s*(?:分析|核对|看|比较|对比)?|\s*的)\s*$')
_NAME = re.compile(r'[A-Za-z0-9\u3400-\u9fff\s·&()+\-]{2,60}')
_REPORT_PERIOD = re.compile(
    r'本报告期自\s*('+_YEAR+r')\s*年\s*0?1\s*月\s*0?1\s*日\s*(?:起)?\s*至\s*'
    r'(?:('+_YEAR+r')\s*年\s*)?0?6\s*月\s*30\s*日\s*(?:止|。)')
_CODE = re.compile(r'(?m)^基金主代码[ \t:：]+(\d{6})[ \t]*$')
_TRADE_CODE = re.compile(r'(?m)^交易代码[ \t:：]+(\d{6})[ \t]*$')
_INDEX = re.compile(
    r'(?:本基金(?:的)?业绩比较基准为(?:标的指数[:：])?)?'
    r'([\u3400-\u9fffA-Za-z0-9().（）-]{2,45}指数)(?:收益率)?[。.]?')
_METRICS = re.compile(r'跟踪(?:表现|偏离(?:度)?)|(?:净值(?:增长率|收益率)?).{0,9}(?:相对|与|对比).{0,9}(?:基准|指数)')
_FEES = re.compile(r'(?:管理费.{0,12}托管费|托管费.{0,12}管理费|费率|持有费(?:用|率)|持有成本)')
_FEE_COMPARISON_TAIL = re.compile(
    r'(?:管理费(?:率)?\s*(?:[、，,与和及]|以及)\s*托管费(?:率)?|'
    r'托管费(?:率)?\s*(?:[、，,与和及]|以及)\s*管理费(?:率)?|(?:年)?费率|持有费(?:用|率))'
    r'(?:[，,]?\s*(?:各自?|分别)?(?:是多少|多少|有何差异|有什么区别|相差多少))?[。！？?]*\Z')
_ADDITIONAL = re.compile(
    r'折溢价|流动性|盘口|买卖价差|成交|换手|期权|历史回测|申赎|申购|赎回|跟踪误差|'
    r'预测|目标价|未来|选一|推荐|值得|买入|卖出|交易建议|因果|情景|上一年|下半年|年报|'
    r'最新|最近|至今|以来|各季度|发行|成分(?:股|权重)|分红明细|比较原因|拆解|择时|定投|仓位|组合配置')
_PERFORMANCE_TITLE = '3.2.1基金份额净值增长率及其与同期业绩比较基准收益率的比较'
_PERCENT = r'([+\-−]?\d{1,3}(?:\.\d{1,4})?)\s*[%％]'
_SIX_MONTH_ROW = re.compile(r'(?m)^\s*过去[六6]个\s*月\s+'+r'\s+'.join([_PERCENT]*6)+r'[ \t]*$')
_NET_ASSET = re.compile(r'(?m)^期末基金资产净值[ \t]+([0-9,]+(?:\.\d{1,2})?)[ \t]*$')
_ACTUAL_TRACKING_ERROR = re.compile(
    r'本报告期内[，,]\s*本基金日均跟踪偏离(?:度)?为\s*[+\-−]?\d+(?:\.\d+)?\s*[%％][，,]\s*'
    r'年化跟踪误差为\s*(\d+(?:\.\d{1,4})?)\s*[%％]')
_ATTRIBUTION = re.compile(
    r'跟踪偏离度为\s*[+\-−]?\d+(?:\.\d+)?\s*[%％][，,]\s*与业绩比较基准产生偏离的\s*'
    r'主要原因为(?P<drivers>[^。；;\d]{4,140})。')
_ATTRIBUTION_DRIVERS = {'日常运作费用': '基金运作费用', '指数结构调整': '指数调仓',
                        '申购赎回': '申赎现金流', '成份股分红': '成分股分红'}
_HOLDING_GOAL = re.compile(r'长期(?:持有|跟踪|配置)|持有为主')
_PRODUCT_CHOICE = re.compile(r'怎么选|如何选|选哪(?:一)?只|哪(?:一)?只|哪个|更适合|更合适')
_HOLDING_FRAGMENT = re.compile(r'[，,。！？?；;\n]')
_OTHER_PERIOD = re.compile(r'\d{4}[-年]|(?:近|过去|最近|前)\s*[\d一二三四五六七八九十]+\s*'
                           r'(?:个)?(?:交易日|日|天|周|月|季|年)|下半年|今年以来|年初至今|年报|季报|上季度|下季度')
_BARE_ETF_PAIR = re.compile(r'\d{6}(?:\.(?:SH|SZ))?(?:\s*(?:以及|和|与|及|、|还是)\s*\d{6}(?:\.(?:SH|SZ))?)+', re.I)


def interim_title(title, year):
    return isinstance(title, str) and re.search(
        r'交易型开放式.*'+re.escape(year)+r'年?(?:中期|半年度)报告[ \t]*$',
        title.replace(' ', '')) is not None


def _comparison_question(task):
    from .draft_sources import _unquoted_local_scope
    from .subject_scope import scope_question

    if (task.get('official_source_expansion_version') != 1 or task.get('research_mode') != 'current'
            or task.get('intent') == '原件内容核对'):
        return None
    branch = branch_question(task, FUND_GROUP)
    question = _unquoted_local_scope(scope_question(task, branch['question'] if branch else None))
    return question if _FUND.search(question) else None


def _holding_question(task, question):
    return bool(task.get(HOLDING_POLICY) == 1 and _HOLDING_GOAL.search(question)
                and _PRODUCT_CHOICE.search(question)
                and not re.search(r'ETF联接|ETF连接|场外基金|\d+年回测', question, re.I))


def _period_scope(task, question, *, fees_only=False):
    holding = _holding_question(task, question)
    if not holding and (not (_METRICS.search(question) or fees_only) or not _FEES.search(question) or
                        task.get(LISTING_POLICY) != 1 and not re.search(r'对比|比较|对照|差异', question)):
        return None
    years = set(_SCOPE_PERIOD.findall(question))
    cutoff = market_date(task['cutoff'])
    if task.get(LISTING_POLICY) == 1:
        offsets = {'今年': 0, '去年': 1, '前年': 2}
        years.update(str(int(cutoff[:4])-offsets[when]) for when in _RELATIVE_PERIOD.findall(question))
    if not years and holding and not _OTHER_PERIOD.search(question):
        # Fall back to the latest interim reporting window whose usual
        # two-month disclosure period has elapsed. Verify both originals.
        year = int(cutoff[:4]) - (cutoff[5:] < '09-01')
        years.add(str(year))
    if len(years) != 1:
        return None
    year, = years
    if year+'-07-01' > cutoff:
        return None
    return question, year


def _holding_subject(question):
    fragments = [part.strip() for part in _HOLDING_FRAGMENT.split(question)]
    for part in fragments:
        subject = _PRODUCT_CHOICE.split(part, maxsplit=1)[0].strip()
        if compare := _COMPARE.search(subject):
            subject = subject[compare.end():].strip()
        subject = re.split(r'(?:看|查|分析)(?=\s*(?:\d{4}年|今年|去年|前年))', subject, maxsplit=1)[0].strip()
        subject = re.sub(r'(?:(?:这)?两只(?:ETF|基金)?|的(?:产品|基金))\Z', '', subject).strip()
        if not _SEPARATOR.search(subject):
            continue
        if _BARE_ETF_PAIR.fullmatch(subject) or _FUND.search(subject):
            return subject
        managers = _SEPARATOR.split(subject)
        if len(managers) >= 2 and all(re.fullmatch(r'[\u3400-\u9fff]{2,16}', name) for name in managers):
            stems = {match[1] for fragment in fragments if (match := re.search(
                r'长期(?:跟踪|持有|配置)\s*([\u3400-\u9fffA-Za-z0-9]{2,32}ETF)$', fragment, re.I))}
            if len(stems) == 1:
                stem, = stems
                return '和'.join(manager+stem for manager in managers)
    return ''


def named_targets(task, question=None, *, fees_only=False):
    """Read the ETF names explicitly named for comparison, including a shared benchmark."""
    if task.get(LISTING_POLICY) != 1 or not (question := question or _comparison_question(task)):
        return []
    fees_only = fees_only or bool(branch_question(task, FUND_GROUP))
    if not _period_scope(task, question, fees_only=fees_only) and not trading_terms(task, question):
        return []
    subject = _holding_subject(question) if _holding_question(task, question) else ''
    if not subject:
        boundary = _TARGET_BOUNDARY.search(question)
        if boundary is None:
            return []
        subject = question[:boundary.start()]
        if compare := _COMPARE.search(subject):
            subject = subject[compare.end():]
    subject = _SCOPE_PERIOD.sub('', _RELATIVE_PERIOD.sub('', subject))
    subject = re.sub(r'(?:近|过去|最近|前)\s*(?:[0-9]{1,3}|十|二十|三十)\s*个?(?:共同|可比)?交易日',
                     '', subject)
    subject = _TARGET_SUFFIX.sub('', subject).strip()
    subject = re.sub(r'(?:在|于)\s*$', '', subject).rstrip(' ，,的')
    names = _SEPARATOR.split(subject.strip(' ，,的'))
    if len(names) < 2:
        return []
    # “甲和乙的沪深300ETF” identifies two managers and one shared product description.
    manager, delimiter, stem = names[-1].partition('的')
    if delimiter and _FUND.search(stem) and all(
            re.fullmatch(r'[\u3400-\u9fff]{2,16}', name.strip()) for name in [*names[:-1], manager]):
        names = [name.strip()+stem.strip() for name in [*names[:-1], manager]]
    result = []
    for name in names:
        if re.search(r'\d{6}\.(?:SH|SZ|HK)\b', name, re.I):
            continue
        if _NAME.fullmatch(name.strip()):
            result.append(name.strip())
    return list(dict((name_key(name), name) for name in result).values())


def comparison_scope(task, *, direct=False, allow_fee_only=False):
    """Keep published interim fundamentals and later execution evidence in separate windows."""
    from .evidence import declared_security_codes, security_code_mentions
    from .subject_scope import subject_allowed

    if not (question := _comparison_question(task)):
        return None
    holding = _holding_question(task, question)
    trading = trading_terms(task, question)
    branch = branch_question(task, FUND_GROUP)
    fees_only = bool((allow_fee_only or branch) and not holding and not trading
                     and _FEES.search(question) and not _METRICS.search(question))
    interim = _period_scope(task, question, fees_only=fees_only)
    if holding and interim and trading is None:
        trading = dict(turnover=True, premium=True, execution=False, sessions=10,
                       ticket=None, unresolved_window=False)
    if not interim and not trading:
        return None
    if direct:
        if holding and (interim is None or re.search(r'顺便|另外(?:请|再|还)|并(?:解释|分析)(?:宏观|政策|行业)', question)):
            return None
        from .etf_trading import EXECUTION_WORDS, PREMIUM_WORDS, TURNOVER_WORDS

        remainder = question
        if trading:
            if re.search(r'(?:最低|最高|中位数|分位数|逐日|每日)(?:的)?(?:成交|溢价|折价)|'
                         r'(?:20\d\d[-年]\d{1,2}[-月]\d{1,2}日?).{0,10}(?:折溢价|折价率|溢价率)', question):
                return None
            for words in (TURNOVER_WORDS, PREMIUM_WORDS, EXECUTION_WORDS):
                remainder = words.sub('', remainder)
            remainder = re.sub(r'(?:(?:近|过去|最近|前)\s*(?:[0-9]{1,3}|十|二十|三十)\s*个?(?:共同|可比)?交易日|'
                               r'(?:最近|最新)(?:一个|共同|可比)?(?:估值日|交易日|收盘))', '', remainder)
        if _ADDITIONAL.search(remainder) or re.search(
                r'假设|假如|忽略|不(?:用|需要|考虑)|总费率|实际持有成本|分红贡献|所有费用|逐笔|'
                r'如果(?!有?\s*[0-9]+\s*(?:万|亿))', remainder):
            return None
        if fees_only and (not _FEE_COMPARISON_TAIL.search(question) or re.search(
                r'规模|净值|收益|评级|成立|投资策略|归因|为什么|跑赢', question)):
            return None
        if not interim and (_FEES.search(question) or _METRICS.search(question)):
            return None
    declared = (sorted({code for code in security_code_mentions(question, encoding=task.get('security_declaration_encoding', 1))
                        if subject_allowed(task, code)}) if branch else declared_security_codes(task))
    names = named_targets(task, question, fees_only=fees_only)
    if len(declared)+len(names) < 2 or any(
            (channel := ETF_CHANNELS.get(code.split('.')[-1])) is None
            or re.fullmatch(CHANNELS[channel]['code_pattern'], code) is None for code in declared):
        return None
    resolved, unresolved = resolve_names(task, names) if names else ([], [])
    codes = sorted(set(declared) | {item['code'] for item in resolved})
    scope = dict(codes=codes)
    if branch:
        scope['question_id'] = branch['id']
    if fees_only:
        scope['fees_only'] = True
    if holding and interim:
        scope['holding'] = True
    if interim:
        _, year = interim
        scope.update(year=year, period=year+'-01-01至'+year+'-06-30')
    if trading:
        scope['trading'] = trading
    if names:
        scope.update(names=names, resolved=resolved, unresolved=unresolved)
    return scope


def filing_requests(task, scope):
    """Choose one listed interim report per product and reuse the current original."""
    from .draft_sources import local_sources_only, pdf_download_disabled, validate_stated_source
    from .official_sources import release_choices

    if not scope.get('year') or local_sources_only(task) or pdf_download_disabled(task):
        return []
    try:
        validate_stated_source(task, 'official')
    except ContractError:
        return []
    cutoff = market_date(task['cutoff'])
    exclusions = [match['source'] for match in _NO_OFFICIAL.finditer(task['question'])]
    if any(label.startswith('官方') for label in exclusions):
        return []
    choices = defaultdict(list)
    for row in release_choices(task, with_details=True):
        code = row.get('issuer_code')
        if (code in scope['codes'] and row['channel'] == ETF_CHANNELS[code.split('.')[-1]]
                and interim_title(row['title'], scope['year'])):
            choices[code].append(row)
    requests = []
    question_id = scope.get('question_id', task['questions'][0]['id'])
    for code in scope['codes']:
        channel = ETF_CHANNELS[code.split('.')[-1]]
        if channel == 'cninfo_fund' and any(label.startswith('巨潮') for label in exclusions):
            continue
        matching = choices[code]
        if not matching:
            requests.append(dict(id='etf_interim_catalog_'+code.replace('.', '_'), source='official',
                operation='release_catalog', parameters=dict(channel=channel, code=code,
                    start_date=scope['year']+'-07-01', end_date=cutoff, page='1', keyword='中期报告'),
                question_ids=[question_id], selection_reason='定位所选ETF的当期中期报告。'))
        elif len(matching) == 1:
            item, = matching
            requests.append(dict(id='etf_interim_original_'+code.replace('.', '_'), source='official',
                operation='release_original', parameters={key: item[key] for key in ('catalog_id', 'release_id')},
                question_ids=[question_id],
                selection_reason='核对基金费率、当期净值增长和同表业绩基准。'))
    return requests


def _compact(text):
    return re.sub(r'\s+', '', text)


def _proof(part, quote):
    return dict(part=part['locator'], quote=quote.strip())


def _unique(candidates):
    if not candidates or len({value for value, _ in candidates}) != 1:
        return None
    return candidates[0]


def report_cells(record, code, year):
    """Read literal lines from the selected document, preserving physical PDF page anchors."""
    snapshot = record.get('snapshot') or {}
    origin = snapshot.get('official_origin') or {}
    if (record.get('source') != 'official' or record.get('operation') != 'release_original'
            or origin.get('code') != code or not interim_title(origin.get('title'), year)
            or origin.get('channel') != ETF_CHANNELS.get(code.split('.')[-1])):
        return []
    material = snapshot
    pages = [part for part in material.get('parts', [])
             if re.fullmatch(r'第\d+页', part.get('locator', '')) and isinstance(part.get('text'), str)
             and not part.get('pdf_text_limited')]
    periods = [(match.groups(), _proof(part, match.group())) for part in pages[:4]
               for match in _REPORT_PERIOD.finditer(part['text'])]
    period = _unique(periods)
    if not period or period[0][0] != year or period[0][1] not in (None, year):
        return []
    literal = code.split('.')[0]
    identities = [(match[1], _proof(part, match.group())) for part in pages[:14]
                  for match in _CODE.finditer(part['text']) if '基金运作方式交易型开放式' in _compact(part['text'])]
    identity = _unique(identities)
    trading = {match[1] for part in pages[:14] for match in _TRADE_CODE.finditer(part['text'])}
    if not identity or identity[0] != literal or (trading and trading != {literal}):
        return []
    indices = []
    for part in pages[:14]:
        for line in part['text'].splitlines():
            normalized = _compact(line)
            if not normalized.startswith('业绩比较基准'):
                continue
            name = _INDEX.fullmatch(normalized.removeprefix('业绩比较基准').lstrip(':：'))
            if name:
                indices.append((name[1], _proof(part, line)))
    benchmark = _unique(indices)
    name = origin['title'].split('交易型开放式', 1)[0].strip().replace(' ', '')+'ETF'
    values = {}
    matched_rows = []
    for part in pages[:18]:
        raw_text = part['text']
        packed = _compact(raw_text)
        if (_PERFORMANCE_TITLE not in packed or not all(
                token in packed for token in ('份额净值增长率①', '业绩比较基准收益率③', '②', '④'))):
            continue
        for match in _SIX_MONTH_ROW.finditer(raw_text):
            row = tuple(value.replace('−', '-') for value in match.groups())
            nav, _, index, _, published_gap, _ = map(Decimal, row)
            if nav >= -100 and index >= -100 and abs(nav - index - published_gap) <= Decimal('0.01'):
                matched_rows.append(((row[0], row[2]), _proof(part, match.group())))
    returns = _unique(matched_rows)
    if returns and benchmark:
        values[PERFORMANCE] = returns[0][0], returns[1]
        values[BENCHMARK] = returns[0][1], returns[1]
    for field, metric in ((MANAGEMENT, 'management_fee'), (CUSTODY, 'custody_fee')):
        candidates = []
        for part in pages:
            for mention in annual_fee_mentions(part['text']):
                if (mention['metric'] == metric and mention['net_assets']
                        and 0 <= Decimal(mention['raw_token']) <= 10):
                    candidates.append((mention['raw_token'], _proof(part, part['text'][mention['start']:mention['end']])))
        if chosen := _unique(candidates):
            values[field] = chosen
    assets = []
    for part in pages[:14]:
        if '金额单位：人民币元' not in _compact(part['text']) and '金额单位:人民币元' not in _compact(part['text']):
            continue
        assets.extend((match[1].replace(',', ''), _proof(part, match.group()))
                      for match in _NET_ASSET.finditer(part['text']) if Decimal(match[1].replace(',', '')) > 0)
    if chosen := _unique(assets):
        values[ASSETS] = chosen
    errors = [(match[1], _proof(part, match.group())) for part in pages[:32]
              for match in _ACTUAL_TRACKING_ERROR.finditer(part['text']) if Decimal(match[1]) <= 100]
    if chosen := _unique(errors):
        values[TRACKING_ERROR] = chosen
    rationale = []
    for part in pages[:32]:
        for match in _ATTRIBUTION.finditer(part['text']):
            drivers = _compact(match['drivers'])
            names = tuple(label for literal, label in _ATTRIBUTION_DRIVERS.items() if literal in drivers)
            if names:
                rationale.append((names, _proof(part, match.group())))
    reason = _unique(rationale)
    return [dict(field=field, label=FIELDS[field][0], unit=FIELDS[field][1], raw=raw, name=name,
        locator=dict(kind=CELL_KIND, **proof, product=identity[1], reporting_period=period[1],
                     benchmark=benchmark[1] if field in (PERFORMANCE, BENCHMARK) else None,
                     benchmark_name=benchmark[0] if field in (PERFORMANCE, BENCHMARK) else None,
                     manager_drivers=list(reason[0]) if field == PERFORMANCE and reason else [],
                     manager_discussion=reason[1] if field == PERFORMANCE and reason else None))
            for field, (raw, proof) in values.items()]


def product_observations(facts, scope):
    """Only compare a unique, source-bound cell for each product and metric."""
    products = defaultdict(dict)
    grouped = defaultdict(list)
    for row in facts:
        if (row.get('locator', {}).get('kind') == CELL_KIND and row.get('period') == scope['period']
                and row.get('code') in scope['codes'] and row.get('field') in FIELDS):
            grouped[row['code'], row['field']].append(row)
    for (code, field), rows in grouped.items():
        if len(rows) == 1:
            products[code][field] = rows[0]
    return dict(products)


def common_benchmark(products, scope):
    if len(scope['codes']) < 2 or scope.get('unresolved') or not all(
            BENCHMARK in products.get(code, {}) for code in scope['codes']):
        return None
    bases = {(products[code][BENCHMARK]['locator']['benchmark_name'],
              Decimal(products[code][BENCHMARK]['raw_value'])) for code in scope['codes']}
    return next(iter(bases)) if len(bases) == 1 else None


def frozen_cell_locations(content, facts):
    """Check that detailed-report excerpts still point to the frozen PDF data."""
    from .contracts import fingerprint

    eligible = [row for row in facts if row.get('locator', {}).get('kind') == CELL_KIND]
    if not eligible:
        return {}
    records = {record['id']: record for record in content['source_records']
               if record.get('task_id') == content['task_id']
               and record.get('research_version') == content['research_version']}
    locations, cells_by_record = {}, {}
    for row in eligible:
        reference = row['artifact_id']
        record = records.get(reference)
        if not record or record.get('snapshot_hash') != fingerprint(record.get('snapshot')):
            continue
        if reference not in cells_by_record:
            cells_by_record[reference] = {cell['field']: cell
                for cell in report_cells(record, row['code'], row['period'][:4])}
        cell = cells_by_record[reference].get(row['field'])
        if (cell and cell['raw'] == row['raw_value'] and cell['unit'] == row['unit']
                and cell['locator'] == row['locator']):
            locations[row['id']] = cell['locator']
    return locations
