"""Current-edition growth quality from one issuer's complete A-share interim filing."""

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
import re

from .announcement_sources import ISSUER_NAME, _NO_OFFICIAL
from .contracts import ContractError, market_date
from .financial_data import NUMBER, UNIT_FACTORS, compact, half_year_filing_title, monetary_cell
from .financial_sources import PEER_ADJUSTED_PROFIT, PEER_CASH_COMPONENTS, financial_reporting_currency_choices

POLICY = 'official_stock_quality_version'
QUALITY_BRIDGE_VERSION = 2
OPEN_COMPANY_POLICY = 'open_company_quality_version'
COMPANY_SCOPE_VERSION = 2
STRATEGY_POLICY = 'stock_strategy_evidence_version'
CELL_KIND = 'stock_quality_filing_cell'
BASIS = '同份半年报上年同期可比列'
REVENUE = 'ths_revenue_stock'
PROFIT = 'ths_np_atoopc_stock'
ADJUSTED = PEER_ADJUSTED_PROFIT
CASH = 'ths_ncf_from_oa_stock'
COST = 'ths_operating_cost_stock'
CASH_IN = 'filing.stock_operating_cash_in'
CASH_OUT = 'filing.stock_operating_cash_out'
RECEIPTS = PEER_CASH_COMPONENTS['销售商品、提供劳务收到的现金']
ASSET_LOSS = 'filing.stock_asset_impairment'
PRETAX = 'filing.stock_pretax_profit'
INCOME_TAX = 'filing.stock_income_tax'
MINORITY_PROFIT = 'filing.stock_minority_profit'
PURCHASE_PAYMENTS = 'filing.stock_purchase_payments'
LABELS = {REVENUE: '营业收入', PROFIT: '归母净利润', ADJUSTED: '扣非归母净利润',
          CASH: '经营现金流净额', RECEIPTS: '销售商品、提供劳务收到的现金',
          COST: '营业成本', CASH_IN: '经营现金流入', CASH_OUT: '经营现金流出',
          ASSET_LOSS: '资产减值损失', PRETAX: '利润总额', INCOME_TAX: '所得税费用',
          MINORITY_PROFIT: '少数股东损益', PURCHASE_PAYMENTS: '购买商品、接受劳务支付的现金'}
CORE_FIELDS = (REVENUE, PROFIT, ADJUSTED, CASH)
_MATCH = re.compile(rf'^(?:请(?:问)?|帮我)?\s*(?:研究|分析|核对|看看|看一下|验证)(?:一下)?\s*'
                    rf'(?P<name>{ISSUER_NAME}?)(?:的)?\s*(?P<year>20\d{{2}}年|今年)\s*'
                    r'(?:上半年|半年度)(?=[\u3400-\u9fff\s，,。！？?]|$)')
_NATURAL_MATCH = re.compile(rf'^(?:我想(?:知道|看看)|想看看)?\s*'
                            rf'(?P<name>{ISSUER_NAME}?)(?:的)?\s*(?P<year>20\d{{2}}年|今年)\s*'
                            r'(?:上半年|半年度)(?=[\u3400-\u9fff\s，,。！？?]|$)')
_NATURAL_GROWTH = re.compile(r'(?:营收|收入|销售|卖得)[^。！？；]{0,12}(?:增长|扩大|多了|更多|上去了)')
_NATURAL_PROFIT = re.compile(r'利润|盈利|赚(?:钱|得)')
_NATURAL_CASH = re.compile(r'现金|收现|回款')
_NATURAL_CONVERSION = re.compile(r'跟上|转化|兑现|同步|带来|增收不增利')
_GROWTH = re.compile(r'增长质量|(?:营收|收入)[^。！？；]{0,12}(?:增长|增幅|增速)[^。！？；]{0,16}'
                     r'(?:转化|变成|传导|带来|同步)[^。！？；]{0,20}(?:利润|盈利)[^。！？；]{0,12}现金')
_OTHER_TASK = re.compile(r'港股|H股|北交所|估值|股价|行情|市盈率|预测|回测|同行|对标|行业|全年|'
                         r'一季度|三季度|二季度|四季度|分部|分产品|首发|当时|'
                         r'(?:排除|不看|不查|不取|仅核对|只看|只取|只问|不要(?:考虑|分析|看))|'
                         r'(?:并|再|另外|同时)(?:研究|分析|比较|评估|测算)(?!增|现|盈|收|利)', re.I)
_PUBLIC = re.compile(r'(?:按|用|根据|依据|查|以)\s*(?:A股|沪深)?\s*(?:公司)?(?:半年度?报告|半年报|定期报告|财报|巨潮资讯)', re.I)
_OPERATING_TOPIC = r'(?:经营|增长质量|盈利质量|现金创造能力|现金流|收入|营收|利润|盈利|回款|赚钱|卖得)'
_COMPANY_START = re.compile(
    rf'^(?:(?:请|帮我|我想|想)(?:再)?\s*)?(?:(?:研究|分析|看看|看一下|了解|知道|问)(?:一下)?\s*)?'
    rf'(?P<name>{ISSUER_NAME}?)(?:的)?\s*(?=(?:近期|最近)?{_OPERATING_TOPIC}|[，,。！？?])')
_QUALITY_TOPIC = re.compile(r'经营质量|增长质量|盈利质量|现金创造能力|利润[的]?成色|现金含量')
_CASH_QUESTION = re.compile(r'经营[^。！？；]{0,18}(?:现金|收现|回款)')
_EARNINGS_FOCUS = re.compile(r'盈利质量|利润[的]?成色|(?:归母净利润|净利润|利润|盈利)[^。！？；]{0,14}'
                             r'(?:变化|下滑|下降|回落|改善|减少|增长|上升|扭亏|转亏|驱动|原因)')
_CASH_FOCUS = re.compile(r'现金创造能力|现金含量|经营(?:活动)?(?:净)?现金流|回款质量')
_EXPLAIN_CHANGE = re.compile(r'为什么|为何|成因|原因|驱动|主要来自|怎么回事|归因')
_EXPLICIT_WINDOW = re.compile(r'\d{4}年|今年|去年|前年|上半年|下半年|(?<!半)年报|一季报|三季报|'
                              r'某季度|季度|最近(?:一期|[两三四五十\d]+年)|(?:近|过去)[一二两三四五六七八九十\d]+(?:年|月)')
_BEYOND_COMPANY = re.compile(r'港股|H股|北交所|估值|市盈率|股价|行情|行业|同行|对手|对标|预测|回测|'
                             r'买卖|投资机会|竞争|明年|未来|接下来|持续性|能否延续|产品|分部|是什么原因|'
                             r'(?:另|同时|顺便|再)(?:看|分析|比较|评估|测算)', re.I)
_OPEN_SUBJECT_CONFLICT = re.compile(r'(?:集团|股份|公司|银行|证券|保险|电器|能源)(?:和|以及|与|、).{2,}|对比|比较|不是|并非')
_FILING_YEAR = re.compile(r'(?<!\d)(20\d{2})\s*年?\s*半年度报告')
_FILING_CORRECTION = re.compile(r'修订|更正|更新|重述')
_HEADLINE = re.compile(r'(?:[一二三四五六七八九十\d]+[、.．])?(?:公司)?主要会计数据和财务指标$')
_MARKET_SCOPE = r'归属于(?:上市公司股东|母公司(?:所有者|股东))的?'
_METRICS = {
    REVENUE: r'营业收入',
    PROFIT: _MARKET_SCOPE + r'净利润',
    ADJUSTED: _MARKET_SCOPE + r'扣除非经常性损益(?:的|后)?净利润',
    CASH: r'经营活动产生的现金流量净额',
}
_UNIT = r'(?:亿元|百万元|万元|千元|元)'
_PERCENT = r'(?:[+−-]?\d+(?:\.\d+)?[%％]|不适用|[-—–]+)'
_SUMMARY_ROWS = {
    field: re.compile(rf'^{metric}\s*[（(](?P<currency>人民币)?(?P<unit>{_UNIT})[）)]\s+'
                      rf'(?P<current>{NUMBER})\s+(?P<prior>{NUMBER})\s+(?P<rate>{_PERCENT})$')
    for field, metric in _METRICS.items()
}
_SHARED_SUMMARY_ROWS = {
    field: re.compile(rf'^{metric}\s+(?P<current>{NUMBER})\s+(?P<prior>{NUMBER})\s+'
                      r'(?P<rate>[+−-]?\d+(?:\.\d+)?[%％]?|不适用|[-—–]+)$')
    for field, metric in _METRICS.items()
}
_SHARED_SUMMARY_HEADER = re.compile(
    r'(?:主要会计数据|项目)?(?:本报告期|本期)(?:[（(]1[-－—–至]6月[）)])?上年同期'
    r'本报告期比上年同期增减[（(][%％][）)]'
)
_CURRENCY_TERMS = re.compile(rf'(?P<scales>元(?:、{_UNIT})*)指(?P<currencies>人民币元(?:、人民币{_UNIT})*)$')
_CURRENCY_LINE = re.compile(rf'(?:金额)?单位(?:[:：]|(?:均)?为)人民币(?P<unit>{_UNIT})(?=$|[）)，,；;。])')
_UNIT_AND_CURRENCY = re.compile(rf'(?:金额)?单位[:：](?P<unit>{_UNIT})[，,]?币种[:：]人民币(?:元)?$')
_SCALE_LINE = re.compile(rf'(?:金额)?单位[:：](?P<unit>{_UNIT})')
_PARENT_REPORTING_CONTEXT = re.compile(r'(?:本公司|本集团)以人民币为记账本位币(?=[，,。；;]|$)')
_CONSOLIDATION_TRANSLATION = re.compile(
    r'合并财务报表中包含的[^。]{0,140}(?:大陆地区以外|境外)[^。]{0,70}子公司的财务报表均已折算为人民币列示')
_FX_NOTE_PREFIX = '备注'
_CAPTION = r'合并(?:及(?:母公司|公司))?'
_STATEMENT_METRICS = {
    '利润表': {REVENUE: r'营业收入', COST: r'营业成本'},
    '现金流量表': {
        RECEIPTS: r'销售商品[、，,]提供劳务收到的现金',
        CASH_IN: r'经营活动现金流入小计', CASH_OUT: r'经营活动现金流出小计',
        CASH: r'经营活动产生的现金流量净额',
    },
}
_LOSS_QUALIFIER = r'(?:[（(][^（）()]{1,30}[）)])?'
_STRATEGY_METRICS = {
    '利润表': {ASSET_LOSS: r'资产减值损失' + _LOSS_QUALIFIER,
             PRETAX: r'(?:[一二三四五六七八九十]+、)?利润总额' + _LOSS_QUALIFIER,
             INCOME_TAX: r'(?:减[:：]\s*)?所得税费用',
             MINORITY_PROFIT: r'(?:[1-9][.．、])?少数股东损益' + _LOSS_QUALIFIER},
    '现金流量表': {PURCHASE_PAYMENTS: r'购买商品[、，,]接受劳务支付的现金'},
}
_NEXT_STATEMENT = re.compile(r'(?:\d+[.．、])?(?:20\d{2}年半年度)?(?:合并(?:及(?:母公司|公司))?|母公司|公司)'
                             r'(?:利润表|现金流量表|资产负债表|所有者权益变动表)')
_NOTE = r'[一二三四五六七八九十]+[（(]\d+[）)](?:[（(][A-Za-z0-9]+[）)])?'
_STATEMENT_VALUES = re.compile(
    rf'^(?:{_NOTE}(?:[,，、]{_NOTE})*\s+)?(?P<current>{NUMBER})\s+(?P<prior>{NUMBER})'
    rf'(?P<rest>(?:\s+(?:{NUMBER}|[-—–]+)){{2}})?$')


def open_company_enabled(task):
    version = task.get(OPEN_COMPANY_POLICY)
    return type(version) is int and version in (1, COMPANY_SCOPE_VERSION)


def _question_focus(text):
    earnings = bool(_EARNINGS_FOCUS.search(text))
    cash = bool(_CASH_FOCUS.search(text))
    if _GROWTH.search(text) or '经营质量' in text or (earnings and cash):
        return 'operating'
    if earnings:
        return 'earnings'
    if cash:
        return 'cash'
    return None


def study_scope(task):
    from .draft_sources import local_scope_text, pdf_download_disabled, validate_stated_source
    from .evidence import declared_security_codes
    from .planning import question_driven_arguments

    version = task.get(POLICY)
    if (type(version) is not int or version not in (1, QUALITY_BRIDGE_VERSION) or task.get('draft_first_version') != 1
            or task.get('research_mode') != 'current' or task.get('entry') not in ('个股', '混合')
            or task.get('intent') not in ('综合研究', '解释变化', '验证观点')
            or task.get('materials') or task.get('pending_materials') or task.get('calculation_specs')
            or declared_security_codes(task)):
        return None
    questions = [row for row in task['questions'] if row['required']]
    if len(questions) != 1 or questions[0]['question'] != task['question']:
        return None
    if any(questions[0].get(key) for key in ('answer_scope', 'evidence_needs', 'method_needs', 'dependencies')):
        return None
    text = local_scope_text(task['question']).strip().lstrip('，,。 ')
    match = _MATCH.match(text)
    growth = _GROWTH.search(text)
    public_source = bool(_PUBLIC.search(text))
    open_company = open_company_enabled(task)
    standard_columns = task.get(OPEN_COMPANY_POLICY) == COMPANY_SCOPE_VERSION
    if version == QUALITY_BRIDGE_VERSION and (public_source or open_company):
        match = match or _NATURAL_MATCH.match(text)
        growth = growth or all(pattern.search(text) for pattern in
                               (_NATURAL_GROWTH, _NATURAL_PROFIT, _NATURAL_CASH, _NATURAL_CONVERSION))
    filing_allowed = not (_NO_OFFICIAL.search(text) or pdf_download_disabled(task))
    try:
        validate_stated_source(task, 'official')
    except ContractError:
        filing_allowed = False
    focus = _question_focus(text) if question_driven_arguments(task) else None
    if not match or not (growth or focus) or _OTHER_TASK.search(text):
        return (_open_company_scope(task, questions[0]['id'], text, filing_allowed, standard_columns)
                if open_company else None)
    if not filing_allowed and (not open_company or public_source):
        return None
    name = match['name'].removesuffix('的')
    # A single security brief; names joined with comparison language remain under the planner.
    if not re.fullmatch(ISSUER_NAME, name) or re.search(r'对比|比较', name):
        return None
    cutoff = market_date(task['cutoff'])
    year = int(cutoff[:4] if match['year'] == '今年' else match['year'][:4])
    start = f'{year}-07-01'
    if start > cutoff:
        return None
    scope = dict(name=name, year=year, periods=[f'{year-1}-06-30', f'{year}-06-30'],
                 start=start, end=min(cutoff, f'{year}-12-31'), question_id=questions[0]['id'],
                 explicit_public_source=public_source)
    if open_company:
        scope.update(whole_question=True, filing_allowed=bool(filing_allowed), standard_columns=standard_columns,
                     strategy_evidence=task.get(STRATEGY_POLICY) == 1)
    if focus:
        scope.update(focus='operating' if growth else focus, explain_change=bool(_EXPLAIN_CHANGE.search(text)))
    return scope


def _open_company_scope(task, question_id, text, filing_allowed, standard_columns):
    from .planning import question_driven_arguments

    if _EXPLICIT_WINDOW.search(text):
        return None
    opening = _COMPANY_START.match(text)
    if not opening:
        return None
    name = opening['name']
    if _OPEN_SUBJECT_CONFLICT.search(name) or not re.fullmatch(ISSUER_NAME, name):
        return None
    if question_driven_arguments(task) and re.search(r'(?:行业|板块|产业链|指数|ETF|基金|市场)$', name, re.I):
        return None
    if any(item != name for item in task.get('objects', ())):
        return None
    body = text[opening.end():]
    asks_conversion = (re.search(r'营收|收入|销售|卖得', body) and _NATURAL_PROFIT.search(body)
                       and _NATURAL_CASH.search(body))
    public_source = bool(_PUBLIC.search(text))
    focus = _question_focus(body) if question_driven_arguments(task) else None
    if not (focus or _QUALITY_TOPIC.search(body) or asks_conversion or _CASH_QUESTION.search(body)):
        return None
    if public_source and not filing_allowed:
        return None
    cutoff = market_date(task['cutoff'])
    scope = dict(name=name, question_id=question_id, year=None, latest_report=True,
                 start=f'{int(cutoff[:4])-1}-07-01', end=cutoff,
                 whole_question=not _BEYOND_COMPANY.search(body), explicit_public_source=public_source,
                 filing_allowed=bool(filing_allowed), standard_columns=standard_columns,
                 strategy_evidence=task.get(STRATEGY_POLICY) == 1)
    if focus or question_driven_arguments(task):
        scope.update(focus='operating' if asks_conversion else focus or 'operating',
                     explain_change=bool(_EXPLAIN_CHANGE.search(body)))
    return scope


def catalogue_request(task, scope):
    return dict(id='stock_quality_catalogue', source='official', operation='release_catalog',
                parameters=dict(channel='cninfo', name=scope['name'], start_date=scope['start'],
                                end_date=scope['end'], page='1', keyword='半年度报告'),
                question_ids=[scope['question_id']], selection_reason='核对发行人证券简称及对应完整A股半年报。')


def selected_releases(task, scope):
    from .official_sources import release_choices, verified_catalogue

    if not scope.get('filing_allowed', True):
        return []
    expected = catalogue_request(task, scope)['parameters']
    catalogues = {row['id'] for row in task['source_records'] if row.get('parameters') == expected}
    rows = [row for row in release_choices(task, with_details=True)
            if row['catalog_id'] in catalogues and row.get('issuer_name') == scope['name']]
    if scope.get('latest_report'):
        snapshots = [value for row in task['source_records'] if row['id'] in catalogues
                     and (value := verified_catalogue(task, row))]
        if len(snapshots) != 1:
            return []
        coverage = snapshots[0]['coverage']
        if (coverage['has_more'] or coverage['publisher_reported_total'] != coverage['returned_rows']
                or any(coverage['excluded'][key] for key in ('invalid_row', 'unsupported_original'))):
            return []
        by_year = defaultdict(list)
        for row in rows:
            if '摘要' in row['title'] or not (year := _FILING_YEAR.search(row['title'])):
                continue
            by_year[int(year[1])].append(row)
        if not by_year:
            return []
        latest = max(by_year)
        rows = by_year[latest]
        # Keep conflicting corrections visible as source records without
        # silently selecting the original or rolling back to an older year.
        if any(_FILING_CORRECTION.search(row['title']) for row in rows):
            return []
        kind_period = f'{latest}-06-30'
    else:
        kind_period = scope['periods'][-1]
    result = {}
    for row in rows:
        if half_year_filing_title(row['title'], kind_period):
            result.setdefault(row['release_id'], row)
    return list(result.values())


def resolved_study_scope(task, scope, releases=None):
    """Use the actual latest complete report title, separately from the search window."""
    if not scope.get('latest_report'):
        return scope
    releases = selected_releases(task, scope) if releases is None else releases
    if len(releases) != 1:
        return None
    year = int(_FILING_YEAR.search(releases[0]['title'])[1])
    if f'{year}-06-30' > market_date(task['cutoff']):
        return None
    return dict(scope, year=year, periods=[f'{year-1}-06-30', f'{year}-06-30'])


def named_financial_inputs(task, scope):
    """Compile the standard current-edition iFinD cells for an exactly matched company."""
    from .evidence import identity_resolutions
    from .subject_scope import subject_allowed

    if not open_company_enabled(task) or scope['explicit_public_source']:
        return []
    matches = [item for item in identity_resolutions(task) if item.get('query') == scope['name']
               and item.get('mode') == 'secname' and item['status'] == '代码映射已核验']
    if len(matches) != 1:
        return []
    code, = matches[0]['codes']
    if not re.fullmatch(r'(?:6\d{5}\.SH|[03]\d{5}\.SZ)', code) or not subject_allowed(task, code):
        return []
    if scope.get('latest_report'):
        cutoff = market_date(task['cutoff'])
        year = int(cutoff[:4]) - (cutoff[5:] < '09-01')
        periods = (f'{year-1}0630', f'{year}0630')
    else:
        periods = (period.replace('-', '') for period in scope['periods'])
    return [dict(codes=code, report_period=period, statement_type='1',
                 indicators=[REVENUE, COST, PROFIT, CASH], question_ids=[scope['question_id']])
            for period in periods]


def _location(page, lines):
    return dict(part=page['locator'], quote='\n'.join(lines))


def _pdf_pages(material):
    return [(part, [line.strip() for line in part['text'].splitlines() if line.strip()])
            for part in material['parts'] if not part.get('pdf_table') and part.get('text') and part.get('locator')]


def _reporting_currency(material, pages):
    """Require a presentation-currency statement or both consolidation anchors."""
    declared = financial_reporting_currency_choices(dict(snapshot=material), dict(parts=[page for page, _ in pages]))
    if declared:
        return [{key: item[key] for key in ('part', 'quote')} for item in declared[:1]]
    parent, translated = None, None
    for page, lines in pages:
        for index, line in enumerate(lines):
            if parent is None and _PARENT_REPORTING_CONTEXT.search(compact(line)):
                parent = _location(page, [line])
            if translated is None and '合并财务报表中包含的' in compact(line):
                for end in range(index+1, min(index+4, len(lines)+1)):
                    block = lines[index:end]
                    if _CONSOLIDATION_TRANSLATION.search(compact(''.join(block))):
                        translated = _location(page, block)
                        break
        if parent and translated:
            return [parent, translated]
    return []


def _currency_context(pages, year):
    """Accept RMB expressly defined in the report, or declared by its statements."""
    terms, statement_currencies = {}, {}
    for page, lines in pages:
        for i, line in enumerate(lines):
            text = compact(line)
            if (match := _CURRENCY_TERMS.fullmatch(text)):
                scales = match['scales'].split('、')
                if match['currencies'].split('、') == ['人民币' + scale for scale in scales]:
                    terms.update((scale, _location(page, [line])) for scale in scales)
            caption = re.fullmatch(rf'(?:{year}年半年度)?{_CAPTION}(利润表|现金流量表)', text)
            if caption:
                for other in lines[i+1:i+4]:
                    if _CURRENCY_LINE.search(compact(other)):
                        statement_currencies[caption[1]] = _location(page, [line, other])
                        break
    if len(statement_currencies) != 2:
        statement_currencies.clear()
    return terms, statement_currencies


def _amounts(match):
    try:
        return tuple(monetary_cell(match[key]) for key in ('prior', 'current'))
    except ContractError:
        return None


def _valid_reported_rate(rate, prior, current):
    if not rate.endswith(('%', '％')) or prior <= 0:
        return True
    stated = Decimal(rate[:-1].replace('−', '-'))
    return ((current / prior - 1) * 100).quantize(Decimal(1).scaleb(stated.as_tuple().exponent),
                                                rounding=ROUND_HALF_UP) == stated


def _fx_attribution(page, lines):
    """Retain a company's explanation only when the note states both treatments."""
    for index, line in enumerate(lines):
        if not compact(line).startswith(_FX_NOTE_PREFIX):
            continue
        for stop in range(index+1, min(index+5, len(lines)+1)):
            quote = lines[index:stop]
            value = compact(''.join(quote))
            if re.search(r'扣除非经常性损益.{0,18}同比下降主要由于', value) and re.search(
                    r'(?:汇率损失|汇兑损失).{0,20}(?:归属于|计入)经常性损益', value) and re.search(
                    r'(?:外汇风险|外汇套保).{0,35}(?:归属于|计入)非经常性损益', value):
                return _location(page, quote)
    return None


def _shared_summary_region(lines, caption):
    """The same table must state RMB, the amount scale and a percent-change column."""
    stop = min(caption+9, len(lines))
    for start in range(caption+2, stop):
        unit_index = start-1
        if not (unit := _UNIT_AND_CURRENCY.fullmatch(compact(lines[unit_index]))):
            continue
        for end in range(start+1, min(start+3, stop+1)):
            if _SHARED_SUMMARY_HEADER.fullmatch(compact(''.join(lines[start:end]))):
                return unit_index, unit['unit'], start, end
    return None


def _summary_pairs(pages, terms, statements, reporting_currency, *, standard_columns=False):
    """Use current and prior-year columns within the explicitly headed table."""
    groups = defaultdict(list)
    for page, lines in pages[:18]:
        for index, caption in enumerate(lines):
            if not _HEADLINE.fullmatch(compact(caption)):
                continue
            prefixes = ('本报告期上年同期', '本期上年同期')
            if standard_columns:
                prefixes += tuple('项目'+value for value in prefixes)
            header = next((i for i in range(index+1, min(index+9, len(lines)))
                           if compact(lines[i]).startswith(prefixes)), None)
            shared_region = _shared_summary_region(lines, index) if standard_columns else None
            if shared_region:
                unit_index, table_scale, header, start_row = shared_region
                header_lines = lines[header:start_row]
            elif header is not None:
                start_row = header+1
                header_lines = [lines[header]]
            else:
                continue
            end = next((i for i in range(start_row, min(header+33, len(lines)))
                        if ('本报告期末' in compact(lines[i]) and '上年度末' in compact(lines[i]))
                        or compact(lines[i]).startswith(('五、', '五.'))), min(header+33, len(lines)))
            for field, pattern in _SUMMARY_ROWS.items():
                for start in range(start_row, end):
                    for stop in range(start+1, min(start+4, end+1)):
                        quote = lines[start:stop]
                        # Line breaks inside a Chinese label carry no information.
                        line = re.sub(r'(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff（(])', '', ' '.join(quote))
                        match = pattern.fullmatch(line)
                        table_level_unit = match is None and shared_region is not None
                        if table_level_unit:
                            match = _SHARED_SUMMARY_ROWS[field].fullmatch(line)
                        if not match or not (values := _amounts(match)):
                            continue
                        rate = match['rate']
                        if table_level_unit and re.fullmatch(r'[+−-]?\d+(?:\.\d+)?', rate):
                            rate += '%'
                        if not _valid_reported_rate(rate, *values):
                            continue
                        scale = table_scale if table_level_unit else match['unit']
                        unit_quote = quote
                        if table_level_unit:
                            unit_quote = [lines[unit_index]]
                            currency = [_location(page, unit_quote)]
                        elif match['currency']:
                            currency = [_location(page, quote)]
                        elif scale in terms:
                            currency = [terms[scale]]
                        else:
                            currency = list(statements.values()) or reporting_currency
                        if not currency:
                            continue
                        locator = dict(_location(page, quote), kind=CELL_KIND,
                            statement='主要会计数据和财务指标', table_header='\n'.join(header_lines),
                            statement_basis=_location(page, [caption]),
                            period_basis=_location(page, header_lines),
                            unit_basis=dict(_location(page, unit_quote), currency='人民币', scale=scale, references=currency),
                            reported_yoy=rate)
                        if field == ADJUSTED and (note := _fx_attribution(page, lines[end:])):
                            locator['management_explanation'] = note
                        groups[field].append((match, values, locator))
    return groups


def _continuation_rows(page, pages, page_index, first_rows):
    """Retain the next physical page only until a new financial statement."""
    for quote in first_rows:
        if _NEXT_STATEMENT.fullmatch(compact(quote)):
            return
        yield page, quote
    if page_index + 1 == len(pages):
        return
    following, lines = pages[page_index+1]
    origin = re.fullmatch(r'第(\d+)页', page['locator'])
    if not origin or following['locator'] != f'第{int(origin[1])+1}页':
        return
    for quote in lines[:36]:
        if _NEXT_STATEMENT.fullmatch(compact(quote)):
            break
        yield following, quote


def _statement_pairs(pages, scope, reporting_currency):
    """Read only consolidated period columns; signed outflows retain their original token."""
    groups = defaultdict(list)
    year = scope['year']
    prefix = r'(?:[1-9]\d*[.．、])?' if scope.get('standard_columns') else ''
    caption_pattern = rf'{prefix}(?:{year}年半年度)?{_CAPTION}(利润表|现金流量表)'
    period_labels = f'{year}年半年度{year-1}年半年度'
    for page_index, (page, lines) in enumerate(pages):
        for index, caption in enumerate(lines):
            if not (kind := re.fullmatch(caption_pattern, compact(caption))):
                continue
            preamble = lines[index+1:index+7]
            units = [(line, match, []) for line in preamble if (match := _CURRENCY_LINE.search(compact(line)))]
            if not units and scope.get('standard_columns'):
                units = [(line, match, []) for line in preamble if (match := _UNIT_AND_CURRENCY.fullmatch(compact(line)))]
            if not scope.get('standard_columns'):
                units = units[:1]
            if not units and reporting_currency and not any(re.search(r'币种[:：](?!人民币)', compact(line))
                                                           for line in preamble):
                units = [(line, match, reporting_currency) for line in preamble
                         if (match := _SCALE_LINE.fullmatch(compact(line)))]
            header = next((line for line in preamble
                           if re.search(rf'(?:{period_labels}){{1,2}}$', compact(line))), '')
            grouped = compact(header).endswith(period_labels*2)
            combined = '及' in caption
            if (len(units) != 1 or not header or grouped != combined or (grouped and not any(
                    compact(line) == '合并合并公司公司' for line in preamble))):
                continue
            (unit_line, unit, currency_references), = units
            unit_basis = dict(_location(page, [unit_line]), currency='人民币', scale=unit['unit'])
            if currency_references:
                unit_basis['references'] = currency_references
            statement_rows = lines[index+1:index+40]
            matches = [(page, quote, _STATEMENT_METRICS[kind[1]]) for quote in statement_rows]
            if scope.get('strategy_evidence'):
                matches.extend((source_page, quote, _STRATEGY_METRICS[kind[1]])
                               for source_page, quote in _continuation_rows(page, pages, page_index, lines[index+1:]))
            for source_page, quote, metrics in matches:
                for field, label in metrics.items():
                    row = re.match(rf'^(?:其中[:：]\s*)?{label}\s+', quote)
                    match = _STATEMENT_VALUES.fullmatch(quote[row.end():]) if row else None
                    if not match or bool(match['rest']) != grouped or not (values := _amounts(match)):
                        continue
                    locator = dict(_location(source_page, [quote]), kind=CELL_KIND,
                        statement='合并'+kind[1], table_header=header,
                        statement_basis=_location(page, [caption]), period_basis=_location(page, [header]),
                        unit_basis=unit_basis)
                    if field in (COST, CASH_OUT, PURCHASE_PAYMENTS):
                        if min(values) < 0 < max(values):
                            continue
                        values = tuple(abs(value) for value in values)
                        locator['statement_sign'] = '支出以正数用于比较'
                    groups[field].append((match, values, locator))
    return groups


def _same_amount_pair(first, second):
    if len(first) != 1 or len(second) != 1:
        return False

    def scaled(row):
        factor = UNIT_FACTORS[row[0][2]['unit_basis']['scale']]
        return tuple(value * factor for value in row[0][1])

    return scaled(first) == scaled(second)


def _cash_totals_reconcile(detail, summary):
    keys = (CASH_IN, CASH_OUT, CASH)
    rows = [detail.get(key, ()) for key in keys]
    if any(len(group) != 1 for group in rows) or not _same_amount_pair(detail[CASH], summary.get(CASH, ())):
        return False
    inflow, outflow, net = [group[0] for group in rows]
    return (len({row[2]['unit_basis']['scale'] for row in (inflow, outflow, net)}) == 1
            and all(a - b == c for a, b, c in zip(inflow[1], outflow[1], net[1], strict=True)))


def _strategy_pairs(detail, summary):
    groups = {}
    profit_fields = (PRETAX, INCOME_TAX, MINORITY_PROFIT)
    if all(len(detail.get(field, ())) == 1 for field in profit_fields) and len(summary.get(PROFIT, ())) == 1:
        pretax, tax, minority = (detail[field][0] for field in profit_fields)
        parent, = summary[PROFIT]
        scale = {row[2]['unit_basis']['scale'] for row in (pretax, tax, minority, parent)}
        periods = zip(pretax[1], tax[1], minority[1], parent[1], strict=True)
        if len(scale) == 1 and all(total - expense - outside == owner for total, expense, outside, owner in periods):
            groups.update({field: detail[field] for field in profit_fields})
            loss = detail.get(ASSET_LOSS, ())
            if len(loss) == 1 and loss[0][2]['unit_basis']['scale'] in scale:
                groups[ASSET_LOSS] = loss
    payments = detail.get(PURCHASE_PAYMENTS, ())
    if len(payments) == 1 and _cash_totals_reconcile(detail, summary):
        paid, = payments
        out, = detail[CASH_OUT]
        if (paid[2]['unit_basis']['scale'] == out[2]['unit_basis']['scale']
                and all(0 <= a <= b for a, b in zip(paid[1], out[1], strict=True))):
            groups[PURCHASE_PAYMENTS] = payments
    return groups


def _asset_loss_explanation(pages):
    """The note's own explanation for a rise in recognized asset impairments."""
    for page, lines in pages:
        for index, line in enumerate(lines):
            if not re.fullmatch(r'\d+[、.．]资产减值损失', compact(line)):
                continue
            for start in range(index+1, min(index+28, len(lines))):
                for end in range(start+1, min(start+3, len(lines)+1)):
                    quote = lines[start:end]
                    if re.fullmatch(r'资产减值损失增加主要原因[:：][^。]{3,100}所致。', compact(''.join(quote))):
                        return _location(page, quote)
    return None


def filing_cells(task, record, scope):
    from .evidence import current_original_reference

    if not current_original_reference(task, record):
        return []
    snapshot = record['snapshot']
    origin = snapshot.get('official_origin') or {}
    if origin.get('channel') != 'cninfo' or origin.get('format') != 'pdf':
        return []
    releases = selected_releases(task, scope)
    if len(releases) != 1 or any(releases[0][key] != record['parameters'][key]
                                 for key in ('catalog_id', 'release_id')):
        return []
    resolved = resolved_study_scope(task, scope, releases)
    return _material_cells(snapshot, resolved) if resolved else []


def _material_cells(snapshot, scope):
    origin = snapshot['official_origin']
    material = snapshot.get('material', snapshot)
    if material.get('status') != '已解析':
        return []
    pages = _pdf_pages(material)
    terms, statements = _currency_context(pages, scope['year'])
    standard_columns = scope.get('standard_columns', False)
    reporting_currency = _reporting_currency(material, pages) if standard_columns else []
    groups = _summary_pairs(pages, terms, statements, reporting_currency, standard_columns=standard_columns)
    detail = _statement_pairs(pages, scope, reporting_currency)
    groups[RECEIPTS] = detail.get(RECEIPTS, [])
    if _same_amount_pair(detail.get(REVENUE, ()), groups.get(REVENUE, ())):
        groups[COST] = detail.get(COST, [])
    if _cash_totals_reconcile(detail, groups):
        groups.update({field: detail[field] for field in (CASH_IN, CASH_OUT)})
    if scope.get('strategy_evidence'):
        groups.update(_strategy_pairs(detail, groups))
        if groups.get(ASSET_LOSS) and (explanation := _asset_loss_explanation(pages)):
            groups[ASSET_LOSS][0][2]['management_explanation'] = explanation
    result = []
    for field in LABELS:
        matches = groups.get(field, ())
        if len(matches) != 1:
            continue
        (match, values, locator), = matches
        scale = locator['unit_basis']['scale']
        for key, period, amount in zip(('prior', 'current'), scope['periods'], values, strict=True):
            result.append(dict(code=origin['code'], field=field, label=LABELS[field], period=period,
                raw=str(amount), unit='人民币' + scale,
                locator=dict(locator, raw_token=match[key], column='上年同期' if key == 'prior' else '本报告期')))
    return result


def frozen_locations(content, facts):
    """Build a readable workpaper from the original already frozen with this report."""
    from .contracts import fingerprint
    from .draft_sources import _number

    records = defaultdict(list)
    for record in content['source_records']:
        records[record['id']].append(record)
    addresses = defaultdict(list)
    for row in facts:
        if row.get('locator', {}).get('kind') == CELL_KIND:
            addresses[row['artifact_id']].append(row)
    locations = {}
    for artifact, rows in addresses.items():
        candidates = records.get(artifact, ())
        if len(candidates) != 1:
            continue
        record, = candidates
        snapshot = record.get('snapshot') or {}
        origin = snapshot.get('official_origin') or {}
        if (record.get('task_id') != content['task_id'] or record.get('research_version') != content['research_version']
                or (record.get('source'), record.get('operation')) != ('official', 'release_original')
                or record.get('snapshot_hash') != fingerprint(snapshot) or origin.get('channel') != 'cninfo'
                or origin.get('format') != 'pdf'):
            continue
        dates = sorted({row['period'] for row in rows if isinstance(row.get('period'), str)})
        if len(dates) != 2 or not all(re.fullmatch(r'\d{4}-06-30', day) for day in dates):
            continue
        year = int(dates[-1][:4])
        if str(year - 1) != dates[0][:4] or not half_year_filing_title(origin.get('title'), dates[-1]):
            continue
        cells = _material_cells(snapshot, dict(year=year, periods=dates,
            standard_columns=content.get(OPEN_COMPANY_POLICY) == COMPANY_SCOPE_VERSION,
            strategy_evidence=content.get(STRATEGY_POLICY) == 1))
        by_address = {(cell['code'], cell['field'], cell['period']): cell for cell in cells}
        for row in rows:
            cell = by_address.get((row['code'], row['field'], row['period']))
            if (cell and cell['locator'] == row['locator'] and cell['raw'] == row['raw_value']
                    and type(row.get('value')) in (int, float) and _number(cell['raw']) == row['value']
                    and cell['unit'] == row['unit']):
                locations[row['id']] = cell['locator']
    return locations


def selected_pairs(task, facts):
    """Only unique same-filing, same-subject pairs can support a conclusion."""
    scope = study_scope(task)
    if not scope or not (scope := resolved_study_scope(task, scope)):
        return {}
    return _comparable_pairs((row for row in facts if row.get('object_label') == scope['name']), scope['periods'])


def frozen_pairs(content, facts):
    """Recover the observed comparison from the saved original when exporting."""
    locations = frozen_locations(content, facts)
    rows = [row for row in facts if row['id'] in locations]
    periods = sorted({row['period'] for row in rows})
    return _comparable_pairs(rows, periods) if len(periods) == 2 else {}


def _comparable_pairs(facts, periods):
    groups = defaultdict(list)
    for row in facts:
        if (row.get('locator', {}).get('kind') == CELL_KIND and row['field'] in LABELS
                and row.get('currency') == 'RMB' and row['unit'].startswith('人民币')
                and row['unit'].removeprefix('人民币') in UNIT_FACTORS and row['value'] is not None):
            groups[row['field']].append(row)
    pairs = {}
    for field, rows in groups.items():
        if len(rows) != 2:
            continue
        ordered = sorted(rows, key=lambda row: row['period'])
        if [row['period'] for row in ordered] == periods and len({(row['artifact_id'], row['code'], row['unit'])
                                                                  for row in ordered}) == 1:
            pairs[field] = ordered
    anchors = {(rows[0]['artifact_id'], rows[0]['code']) for rows in pairs.values()}
    return pairs if len(anchors) <= 1 else {}
