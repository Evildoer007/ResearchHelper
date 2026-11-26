"""Literal, source-bound amounts from registered financial filing tables."""

import re
from collections import defaultdict
from fractions import Fraction

from .contracts import fingerprint
from .financial_data import (NUMBER, UNIT_FACTORS, FinancialDataUnavailable, compact,
                             half_year_filing_title, monetary_cell)
from .financial_sources import (
    BANK_CREDIT_IMPAIRMENT, BANK_INTEREST_INCOME, BANK_PROFIT_COMPONENTS, FINANCIAL_METRIC_LABELS,
    PEER_ADJUSTED_PROFIT, PEER_CASH_COMPONENTS, PEER_OPERATING_FIELDS, PEER_PROFIT_COMPONENTS,
    PEER_REVENUE_COMPONENTS, _financial_amount_unit, _pdf_metric_label, _statement_titles,
    financial_statement_labels, named_financial_fields, original_interval_spans,
)
from .pdf_material import ALIGNED_EXTRACT_VERSIONS, TEXT_TABLE_VERSION
from .source_catalogue import disclosure_fields

SEGMENT_PREFIX = 'filing.segment.revenue:'
SEGMENT_COMPARISON_POLICY = 'draft_segment_attribution_version'
SEGMENT_CHANGE = 'segment_revenue_change'
SEGMENT_UNIT_VERSION = 16
CONSOLIDATED_SCOPE_VERSION = 17
CONSOLIDATED_METRICS_VERSION = 27
_UNIT = re.compile(r'(?:单位[:：]人民币|金额单位(?:均)?为人民币)(亿元|百万元|万元|千元|元)(?![\u3400-\u9fff/／])')
_CONSOLIDATED = re.compile(r'(合并利润表|合并现金流量表)(?:[（(]续[）)])?\Z')
_STATEMENT_FAMILIES = ('利润表', '资产负债表', '现金流量表')
_FIELDS = {
    '营业收入': ('ths_revenue_stock', ('营业收入', '营收')),
    '归属于母公司股东的净利润': ('ths_np_atoopc_stock', ('归属于母公司', '归母', '净利润')),
    '归属于母公司所有者的净利润': ('ths_np_atoopc_stock', ('归属于母公司', '归母', '净利润')),
}
_ADDITIONAL_METRICS = {
    '合并利润表': (('operating_cost', 'ths_operating_cost_stock'), ('net_profit', 'ths_np_stock')),
    '合并现金流量表': (('operating_cash_flow', 'ths_ncf_from_oa_stock'),),
}
_SEGMENT_HEADERS = ('主营业务分行业', '主营业务分产品', '主营业务分地区')
_AMOUNT_OR_GAP = '(?:' + NUMBER + r'|[-—–]+)'
_SEGMENT_ROW = re.compile(r'^\s*(?P<label>[\u3400-\u9fff\s]{2,32}?)\s+'
                          r'(?P<revenue>' + _AMOUNT_OR_GAP + r')\s+' + _AMOUNT_OR_GAP + r'(?=\s|$)')
_AMOUNT_UNIT_LABEL = r'(?:金额单位|单位)(?:[:：]|(?:均)?为)'
_CURRENCY_LABEL = r'币种[:：]'
_RMB_LABEL = r'人民币|CNY|RMB'
_DECLARATION_END = r'$|[()（）,，;；。]'
_UNIT_OR_CURRENCY = re.compile(_AMOUNT_UNIT_LABEL + '|' + _CURRENCY_LABEL)
_SEGMENT_DECLARATION = re.compile(r'^[（(]?(?:除另有注明外[，,])?(?:' + _AMOUNT_UNIT_LABEL + '|' + _CURRENCY_LABEL + ')')
_SEGMENT_UNIT = re.compile(_AMOUNT_UNIT_LABEL + r'(?P<currency>' + _RMB_LABEL + r')?'
                           r'(?P<unit>亿元|百万元|万元|千元|元)(?=' + _DECLARATION_END + '|' + _CURRENCY_LABEL + ')', re.I)
_SEGMENT_CURRENCY = re.compile(_CURRENCY_LABEL + '(?:' + _RMB_LABEL + ')(?='
                               + _DECLARATION_END + '|' + _AMOUNT_UNIT_LABEL + ')', re.I)


def segment_field(field):
    return isinstance(field, str) and field.startswith(SEGMENT_PREFIX)


def is_filing_amount(row):
    locator = row.get('locator')
    if not isinstance(locator, dict):
        return False
    origin = row.get('source'), row.get('source_operation')
    if locator.get('kind') == 'peer_filing_cell':
        return origin in (('ifind', 'filing_original'), ('official', 'release_original'))
    return origin == ('ifind', 'filing_original') and locator.get('kind') in ('filing_financial_cell', 'filing_segment_comparison')


def _basis(part, quote):
    return dict(part=part['locator'], quote=quote)


def _unique_unit(lines):
    matches = [(match[1], line) for line in lines for match in _UNIT.finditer(compact(line))]
    if len({unit for unit, _ in matches}) != 1:
        return None
    return matches[0]


def _amount(token):
    try:
        return monetary_cell(token)
    except FinancialDataUnavailable:
        return None


def _statement_scope(physical_lines, caption, table_leading, header, statement):
    """Locate the actual table header within its own consolidated statement."""
    table_preamble = '\n'.join(map(compact, table_leading))
    titles = set().union(*(_statement_titles(table_preamble, family) for family in _STATEMENT_FAMILIES))
    if titles - {statement}:
        return None
    other_captions = [i for i, line in enumerate(physical_lines) if i != caption
                      and any(_statement_titles(compact(line), family) for family in _STATEMENT_FAMILIES)]
    if other_captions and not titles:
        return None
    stop = next((index for index in other_captions if index > caption), len(physical_lines))
    section = physical_lines[caption+1:stop]
    heading = compact(header)
    physical = [compact(line) for line in section]
    text = ''.join(physical)
    offset, starts = 0, []
    for index, line in enumerate(physical):
        if line and heading and text.startswith(heading, offset):
            starts.append(index)
        offset += len(line)
    if len(starts) != 1:
        return None
    start, = starts
    unit = _declared_table_unit(section[:start], omit_preparer_label=True)
    if unit is None:
        return None
    end, length = start, 0
    while length < len(heading):
        length += len(physical[end])
        end += 1
    return section[:end], unit


def _consolidated_cells(parts, scope, *, bound_scope, extended_metrics=False):
    by_locator = {part['locator']: part for part in parts}
    requested = set()
    if extended_metrics:
        for sentence in re.split(r'[。；;！？\n]', scope):
            statements = set()
            for clause in re.split(r'[，,]', sentence):
                statements = financial_statement_labels(clause) or statements
                if not statements or '合并报表' in statements:
                    requested.update(named_financial_fields(clause))
    for part in parts:
        shape = part.get('pdf_table') or {}
        if (shape.get('version') != TEXT_TABLE_VERSION or shape.get('representation') != 'aligned_words'
                or shape.get('geometry_only') is not True):
            continue
        physical = by_locator.get(f"第{shape.get('page')}页")
        if not physical:
            continue
        physical_lines = physical['text'].splitlines()
        captions = [(index, match[1]) for index, line in enumerate(physical_lines)
                    if (match := _CONSOLIDATED.fullmatch(compact(line)))
                    and (match[1] == '合并利润表' or extended_metrics)]
        lines = part['text'].splitlines()
        header_index = shape.get('header_row')
        if len(captions) != 1 or type(header_index) is not int or not 0 <= header_index < len(lines):
            continue
        (caption, statement), = captions
        header = lines[header_index]
        if bound_scope:
            bound = _statement_scope(physical_lines, caption, lines[:header_index], header, statement)
            if bound is None:
                continue
            period_lines, unit = bound
        else:
            declaration = _financial_amount_unit('\n'.join(physical_lines[caption+1:]), header)
            unit = _unique_unit([declaration['quote']]) if declaration else None
            if unit is None:
                continue
            period_lines = physical_lines
        columns = header.split('\t')
        period_columns = {}
        for index, literal in shape.get('period_columns', {}).items():
            if not isinstance(index, str) or not index.isdigit() or not isinstance(literal, str):
                continue
            column = int(index)
            intervals = list(original_interval_spans(literal))
            if not 0 < column < len(columns) or len(intervals) != 1:
                continue
            period = intervals[0]['period']
            heading = compact(columns[column])
            if heading not in (period[:4]+'年', compact(literal)):
                continue
            original_line = next((line for line in period_lines if compact(literal) in compact(line)), None)
            if original_line is None:
                continue
            period_columns[column] = period, _basis(physical, original_line)
        for row_index, line in enumerate(lines[header_index+1:], header_index+1):
            cells = line.split('\t')
            name = re.sub(r'^[一二三四五六七八九十]+、', '', compact(cells[0]))
            words = shape.get('word_cells', {}).get(str(row_index))
            if len(cells) != len(columns) or not isinstance(words, list) or len(words) != len(cells):
                continue
            field = None
            known = _FIELDS.get(name) if statement == '合并利润表' else None
            if known and (known[0] in requested if extended_metrics else any(term in scope for term in known[1])):
                field = known[0]
            elif extended_metrics:
                for metric, candidate in _ADDITIONAL_METRICS[statement]:
                    label = FINANCIAL_METRIC_LABELS[metric]
                    if candidate in requested and _pdf_metric_label(cells[0], label):
                        field, name = candidate, label
                        break
            if not field:
                continue
            for column, (period, period_basis) in period_columns.items():
                if (not isinstance(words[column], list)
                        or not all(isinstance(word, dict) and isinstance(word.get('text'), str) for word in words[column])
                        or ' '.join(word['text'] for word in words[column]) != cells[column]):
                    continue
                value = _amount(cells[column])
                if value is not None:
                    yield dict(field=field, label=name, period=period, value=value,
                               raw_token=cells[column], unit=unit[0],
                               locator=dict(kind='filing_financial_cell', part=part['locator'], quote=line,
                                   raw_token=cells[column], table_header=header,
                                   unit_basis=_basis(physical, unit[1]), period_basis=period_basis,
                                   statement_basis=_basis(physical, physical_lines[caption]), statement='合并报表'))


def _segment_label(value):
    # The same report can abbreviate the residual line as 其他 in its table.
    return '其他业务' if value == '其他' else value


def _declared_unit_context(lines):
    """Return the declared scale and RMB support, or None for a conflict."""
    scales = set()
    rmb = False
    for line in lines:
        text = compact(line)
        units = list(_SEGMENT_UNIT.finditer(text))
        currencies = _SEGMENT_CURRENCY.findall(text)
        if (len(units) != len(re.findall(_AMOUNT_UNIT_LABEL, text))
                or len(currencies) != len(re.findall(_CURRENCY_LABEL, text))):
            return None
        scales.update(match['unit'] for match in units)
        rmb = rmb or bool(currencies) or any(match['currency'] for match in units)
    if len(scales) > 1:
        return None
    return next(iter(scales), None), rmb


def _declared_table_unit(lines, *, omit_preparer_label=False):
    readings = [compact(line) for line in lines]
    if omit_preparer_label:
        # The preparing entity uses the same 单位 label as the amount scale.
        readings = [re.sub(r'编制单位[:：]', '', line) for line in readings]
    marked = [index for index, line in enumerate(readings) if _UNIT_OR_CURRENCY.search(line)]
    declared = _declared_unit_context([readings[index] for index in marked])
    if declared and declared[0] and declared[1]:
        return declared[0], '\n'.join(lines[marked[0]:marked[-1]+1])
    return None


def _segment_table_unit(lines, verify_units):
    return _declared_table_unit(lines) if verify_units else _unique_unit(lines)


def _segment_note_header(lines, index, verify_units):
    count = 6 if verify_units else 3
    nearby = [line for line in lines[max(0, index-count):index] if line.strip()]
    if not verify_units:
        return (nearby[-1], []) if nearby else ('', [])
    captions = [i for i, line in enumerate(nearby) if not _SEGMENT_DECLARATION.match(compact(line))]
    if not captions:
        return '', []
    period = captions[-1]
    start = captions[-2]+1 if len(captions) > 1 else 0
    return nearby[period], nearby[start:period]+nearby[period+1:]


def _segment_note_tables(parts, verify_units, *, continued=False):
    """Keep each period, its ordered columns and the literal revenue row together."""
    previous = None
    for part in parts:
        text = part['text']
        page = re.fullmatch(r'第(\d+)页', part['locator'])
        if continued and page is None:
            continue
        lines = text.splitlines()
        headings = [compact(line) for line in lines[:16] if '分部业绩' in line]
        explicit = '分部报告' in compact(text) and bool(headings if continued else '分部业绩' in compact(text))
        follow_on = continued and previous is not None and int(page[1]) == previous + 1 and any(
            re.search(r'[（(]续[）)]$', heading) for heading in headings)
        if not explicit and not follow_on:
            previous = None
            continue
        previous = int(page[1]) if continued else None
        for index, line in enumerate(lines):
            labels = line.split()
            if (not 3 <= len(labels) <= 25 or labels[-1] != '合计' or len(labels) != len(set(labels))
                    or any(not re.fullmatch(r'[\u3400-\u9fff]{2,20}', label) for label in labels)):
                continue
            period_quote, unit_quotes = _segment_note_header(lines, index, verify_units)
            intervals = list(original_interval_spans(period_quote))
            restated = False
            suffix = compact(period_quote[intervals[0]['end']:]) if len(intervals) == 1 else ''
            if continued:
                suffix, modifiers = re.subn(r'[（(]已重述[）)]$', '', suffix)
                restated = bool(modifiers)
            # The column must follow a date-to-date header, not just an
            # unrelated mention of the annual report's dates.
            if (len(intervals) != 1 or period_quote[:intervals[0]['start']].strip()
                    or suffix not in ('', '止期间', '期间', '止')):
                continue
            values = ''
            for row in lines[index+1:index+(6 if verify_units else 4)]:
                if not row.strip():
                    continue
                if verify_units and _SEGMENT_DECLARATION.match(compact(row)):
                    unit_quotes.append(row)
                    continue
                values = row
                break
            declared = _declared_unit_context(unit_quotes)
            if declared is None:
                continue
            match = re.match(r'^\s*(?:一、)?营业收入\s+', values)
            if not match:
                continue
            tokens = values[match.end():].split()
            if len(tokens) != len(labels):
                continue
            note = dict(part=part['locator'], header=line, quote=values, period_quote=period_quote)
            if unit_quotes:
                note['unit_quotes'] = unit_quotes
            yield dict(period=intervals[0]['period'], columns=list(zip(labels, tokens, strict=True)),
                       note=note, unit=declared, restated=restated)


def _segment_notes(parts, verify_units):
    for table in _segment_note_tables(parts, verify_units):
        for name, token in table['columns'][:-1]:
            value = _amount(token)
            if value is not None:
                yield _segment_label(name), value, table['period'], table['note'], table['unit']


def _same_segment_amount(value, unit, note_value, note_unit, note_rmb):
    if note_unit is None or note_unit == unit:
        return value == note_value
    # Different stated scales are comparable only with confirmed currency.
    # Fractions preserve every digit during the comparison.
    return note_rmb and (Fraction(value) * int(UNIT_FACTORS[unit])
                         == Fraction(note_value) * int(UNIT_FACTORS[note_unit]))


def _segment_cells(parts, verify_units):
    confirmations = defaultdict(list)
    for name, value, period, note, declared in _segment_notes(parts, verify_units):
        confirmations[name].append((value, period, note, declared))
    if not confirmations:
        return
    for part in parts:
        lines = part['text'].splitlines()
        sections = [index for index, line in enumerate(lines)
                    if any(compact(line).startswith(marker) for marker in _SEGMENT_HEADERS)]
        for head, stop in zip(sections, sections[1:]+[len(lines)]):
            columns = next((index for index in range(head+1, min(stop, head+6))
                            if re.match(r'^分(?:行业|产品|地区)营业收入营业成本', compact(lines[index]))), None)
            unit = _segment_table_unit(lines[head:columns], verify_units) if columns is not None else None
            if unit is None:
                continue
            active = False
            for index in range(columns+1, stop):
                line = lines[index]
                if compact(line).startswith('合计'):
                    break
                match = _SEGMENT_ROW.match(line)
                if match is None:
                    if line.strip() and (active or index-columns > 12):
                        break
                    continue
                active = True
                raw = match['revenue']
                value = _amount(raw)
                name = _segment_label(compact(match['label']))
                options = [(period, note) for note_value, period, note, declared in confirmations.get(name, ())
                           if value is not None and _same_segment_amount(value, unit[0], note_value, *declared)]
                if len({period for period, _ in options}) != 1:
                    continue
                period, note = options[0]
                yield dict(field=SEGMENT_PREFIX+name, label=name+'收入', period=period, value=value,
                           raw_token=raw, unit=unit[0], locator=dict(kind='filing_financial_cell',
                               part=part['locator'], quote=line, raw_token=raw, table_header=lines[columns],
                               unit_basis=_basis(part, unit[1]), period_basis=dict(part=note['part'], quote=note['period_quote']),
                               statement_basis=note, statement='集团分部'))


def stated_segment_scope(task):
    """Find an explicit company and two comparable halves for revenue attribution."""
    from .contracts import market_date
    from .draft_sources import _unquoted_local_scope
    from .evidence import declared_security_codes
    from .subject_scope import scope_question

    if task.get(SEGMENT_COMPARISON_POLICY) != 1 or task.get('research_mode') != 'current' or task.get('intent') == '原件内容核对':
        return None
    text = _unquoted_local_scope(scope_question(task))
    if not (re.search(r'分部|主营业务|业务构成|收入结构|各(?:项)?业务', text) and
            re.search(r'上年同期|去年同期|同比', text) and re.search(r'增长|贡献|驱动|归因|拆解|变化', text)):
        return None
    if re.search(r'(?:不|别|无需|不要)(?:比较|分析|核对)(?:各(?:项)?业务|分部|上年同期|去年同期)', text):
        return None
    years = set(re.findall(r'(\d{4})\s*年\s*(?:上半年|半年度|半年报)', text))
    codes = declared_security_codes(task)
    if len(years) != 1 or len(codes) != 1 or not codes[0].endswith(('.SH', '.SZ')):
        return None
    year, = years
    if year+'-06-30' > market_date(task['cutoff']):
        return None
    return dict(code=codes[0], periods=[str(int(year)-1)+'0630', year+'0630'],
                question_ids=[row['id'] for row in task['questions'] if row.get('required', True)])


def _consolidated_revenue_pair(parts, periods):
    """Check both netted segment totals against the same consolidated statement row."""
    previous, current = (day[:4] for day in periods)
    cells = [cell for cell in _consolidated_cells(parts, '合并报表营业收入', bound_scope=True, extended_metrics=True)
             if cell['field'] == 'ths_revenue_stock' and cell['period'] == current+'-01-01/'+current+'-06-30']
    if len(cells) != 1:
        return None
    latest, = cells
    locator = latest['locator']
    indexed = {part['locator']: part for part in parts}
    table = indexed[locator['part']]
    shape = table['pdf_table']
    page = indexed[f"第{shape['page']}页"]
    header = locator['table_header']
    lines = page['text'].splitlines()
    caption = lines.index(locator['statement_basis']['quote'])
    scope = _statement_scope(lines, caption, table['text'].splitlines()[:shape['header_row']], header, '合并利润表')
    if not scope or not any(re.fullmatch(r'截至6月30日止(?:6|六)个月(?:期间)?', compact(line)) for line in scope[0]):
        return None
    labels = list(map(compact, header.split('\t')))
    quote = locator['quote']
    rows = [index for index, text in enumerate(table['text'].splitlines()) if text == quote]
    if len(rows) != 1 or any(labels.count(year+'年') != 1 for year in (previous, current)):
        return None
    words = shape.get('word_cells', {}).get(str(rows[0]), [])
    tokens = quote.split('\t')
    positions = [labels.index(year+'年') for year in (previous, current)]
    if len(tokens) != len(labels) or len(words) != len(tokens) or any(
            ' '.join(word['text'] for word in words[index]) != tokens[index] for index in positions):
        return None
    amounts = [_amount(tokens[index]) for index in positions]
    if None in amounts or latest['value'] != amounts[1]:
        return None
    return dict(totals=amounts, tokens=[tokens[index] for index in positions], unit=latest['unit'],
                locator=locator)


def segment_revenue_cells(record, scope):
    """Read whole reconciled segment tables or leave the attribution open."""
    directory = record.get('directory', {})
    if (record.get('kind') != 'filing' or directory.get('issuer') != scope['code'] or
            not half_year_filing_title(directory.get('title'), scope['periods'][-1])):
        return []
    parts = record['snapshot']['material']['parts']
    intervals = [period[:4]+'-01-01/'+period[:4]+'-06-30' for period in scope['periods']]
    candidates = [table for table in _segment_note_tables(parts, True, continued=True) if table['period'] in intervals]
    if len(candidates) != 2 or sorted(row['period'] for row in candidates) != intervals:
        return []
    statement = _consolidated_revenue_pair(parts, scope['periods'])
    if not statement:
        return []
    unit = statement['unit']
    candidates.sort(key=lambda item: item['period'])
    tables = []
    for table, total in zip(candidates, statement['totals'], strict=True):
        amounts = [(name, raw, _amount(raw)) for name, raw in table['columns']]
        detail_unit, _ = table['unit']
        if (any(value is None for _, _, value in amounts) or detail_unit not in (None, unit)
                or amounts[-1][2] != total or sum(value for _, _, value in amounts[:-1]) != total):
            return []
        tables.append(amounts[:-1])
    if [name for name, _, _ in tables[0]] != [name for name, _, _ in tables[1]]:
        return []
    rows = []
    statement_locator = statement['locator']
    for table, values, total, token in zip(candidates, tables, statement['totals'], statement['tokens'], strict=True):
        note = table['note']
        period_basis = dict(part=note['part'], quote=note['period_quote'])
        rows.append(dict(code=scope['code'], issuer=directory.get('security_name') or scope['code'],
            field='ths_revenue_stock', label='营业收入', period=table['period'], value=total, unit=unit,
            locator=dict(statement_locator, raw_token=token, period_basis=period_basis,
                         segment_total=True, restated=table['restated'])))
        for name, raw, value in values:
            label = _segment_label(name)
            rows.append(dict(code=scope['code'], issuer=directory.get('security_name') or scope['code'],
                field=SEGMENT_PREFIX+label, label=label, period=table['period'], value=value, unit=unit,
                locator=dict(kind='filing_segment_comparison', part=note['part'], quote=note['quote'],
                    token=raw, table_header=note['header'], period_basis=period_basis,
                    unit_basis=statement_locator['unit_basis'],
                    statement_basis=dict(part=statement_locator['part'], quote=statement_locator['quote']),
                    statement='集团分部', segment_count=len(values), restated=table['restated'])))
    return rows


def filing_amounts(record, scope, *, reader_version):
    """Supply exact printed cells only when column, period and unit agree."""
    disclosure = disclosure_fields(record)
    code = disclosure.get('issuer_code')
    if not code or not scope or not record.get('hash'):
        return []
    material = record['snapshot'].get('material', {})
    parts = material.get('parts', [])
    if material.get('status') != '已解析' or not parts:
        return []
    bound_scope = type(reader_version) is int and reader_version >= CONSOLIDATED_SCOPE_VERSION
    extended_metrics = bound_scope and reader_version >= CONSOLIDATED_METRICS_VERSION
    candidates = []
    if material.get('extract_version') in ALIGNED_EXTRACT_VERSIONS:
        candidates.extend(_consolidated_cells(parts, scope, bound_scope=bound_scope,
                                              extended_metrics=extended_metrics))
    if any(term in scope for term in ('分部', '分行业', '分业务', '分产品', '收入结构')):
        verify_units = type(reader_version) is int and reader_version >= SEGMENT_UNIT_VERSION
        candidates.extend(_segment_cells(parts, verify_units))
    grouped = defaultdict(list)
    for cell in candidates:
        grouped[cell['field'],cell['period']].append(cell)
    result = []
    for cells in grouped.values():
        if len({(row['value'], row['unit']) for row in cells}) == 1:
            result.append(dict(cells[0], code=code, issuer=disclosure.get('issuer_name', code)))
    return result


_PEER_NUMBERING = r'(?:[一二三四五六七八九十]+|\d+)[、.．]\s*'
_PEER_CAPTION = rf'(?:{_PEER_NUMBERING})?(?:未经审计)?'
_PEER_ACCOUNTING = r'(?:[-－—]按中国会计准则编制)?'
_PEER_STATEMENT = re.compile(rf'{_PEER_CAPTION}合并(?P<both>及(?:母公司|公司|银行|本行))?利润表'
    rf'(?:[（(－—-]?续[）)]?)?{_PEER_ACCOUNTING}\Z')
_PEER_CASH_STATEMENT = re.compile(rf'{_PEER_CAPTION}合并现金流量表{_PEER_ACCOUNTING}\Z')
_PEER_STATEMENT_END = re.compile(rf'{_PEER_CAPTION}(?:(?:母公司|本行|公司|银行|合并(?:及(?:母公司|公司|银行|本行))?)'
    rf'(?:资产负债表|现金流量表|所有者权益变动表|股东权益变动表)|(?:母公司|公司|本行|银行)利润表|财务报表附注)'
    rf'(?:[（(]续[）)])?{_PEER_ACCOUNTING}\Z')
_PEER_LOSS_SUFFIX = r'\s*[（(](?:净)?亏损以[^）)\n]{1,25}号(?:填列|列示)[）)]'
_PEER_TOTAL = re.compile(r'^\s*(?:[一二三四五六七八九十0-9]+[、.．]\s*)?'
    rf'(?P<metric>营业总?收入(?:合计)?|归属于(?:母公司(?:股东|所有者)|上市公司股东|本行股东)的净利润)'
    rf'(?:{_PEER_LOSS_SUFFIX})?\s+(?P<values>.+)$')
_PEER_WRAPPED_PROFIT = re.compile(r'^\s*(?:[一二三四五六七八九十0-9]+[、.．]\s*)?'
    r'归属于(?:母公司(?:股东|所有者)|上市公司股东|本行股东)的净利润(?:\s*[（(](?:净)?亏损(?:以[^）)\n]{0,24})?)?\s*$')
_PEER_SPLIT_LOSS = re.compile(r'[（(](?:净)?亏损[^）)]{1,38}[）)]')
_PEER_CASH_FIELDS = {'经营活动产生的现金流量净额': PEER_OPERATING_FIELDS[1],
                     **PEER_CASH_COMPONENTS}
_PEER_OPERATING_LABELS = '|'.join(re.escape(label) for label in ('营业收入', '营业成本', *_PEER_CASH_FIELDS))
_PEER_OPERATING_LINE = re.compile(rf'^\s*(?:{_PEER_NUMBERING}|其中[:：]\s*)?'
    rf'(?P<metric>{_PEER_OPERATING_LABELS})\s+'
    r'(?:(?:注释|附注(?:[一二三四五六七八九十]+)?)[.．、\s]*\d{1,3}\s+)?(?P<values>.+)$')
_PEER_SIGNED_INCOME = r'(?:收益(?:\s*[/／]\s*[（(]\s*损失\s*[）)])?|[（(]\s*损失\s*[）)]\s*[/／]\s*收益)'
_PEER_COMPONENT = re.compile(r'^\s*(?P<metric>手续费及佣金净收入|利息净收入|'
    rf'(?:投资|公允价值变动)\s*{_PEER_SIGNED_INCOME})'
    r'(?:\s*[（(](?:损失|亏损)[^）)\n]{0,40}[）)])?\s+(?P<values>.+)$')
_PEER_EXPENSE = re.compile(rf'^(?:{_PEER_NUMBERING})?营业(?:总)?(?:支出|成本)')
_PEER_AMOUNTS = re.compile(r'\s*' + NUMBER + r'(?:\s+' + NUMBER + r')*\s*\Z')
_PEER_NOTE_PREFIX = re.compile(r'^\s*(?:[/／]|[一二三四五六七八九十]+[、．.]\s*'
                               r'\d{1,3}(?:[（(]\d{1,3}[）)])?)\s+')
_PEER_COMPONENT_IDS = {label: field for field, label in PEER_REVENUE_COMPONENTS.items()}
_PEER_PROFIT_IDS = {label: field for field, (label, _) in PEER_PROFIT_COMPONENTS.items()}
_PEER_PROFIT_ROW = re.compile(rf'^\s*(?:(?:加|减)[:：]\s*|{_PEER_NUMBERING})?'
    rf'(?P<metric>{"|".join(_PEER_PROFIT_IDS)})(?=\s|[（(]|$)(?P<tail>.*)$')
_PEER_PROFIT_LOSS_SUFFIX = re.compile(r'[（(](?:净)?(?:亏损|损失)以[^）)]{1,25}号(?:填列|列示)[）)]')
_PEER_COMPREHENSIVE = re.compile(rf'^(?:{_PEER_NUMBERING})?其他综合收益(?:的税后净额|税后净额)?')
_PEER_CURRENCY_TERMS = re.compile(r'(?P<scales>元(?:、(?:千元|万元|百万元|亿元))*)指'
                                  r'(?P<currencies>人民币元(?:、人民币(?:千元|万元|百万元|亿元))*)\Z')
_PEER_CURRENCY_CAPTION = re.compile(r'^[（(]?(?:除(?:特别注明|另有说明)外[，,])?'
    r'(?:货币单位(?:均)?以|以)?人民币(?P<unit>亿元|百万元|万元|千元|元)(?:列示|列报)?[）)]?\Z')
_PEER_CURRENCY_NOTE = re.compile(r'本公司(?:及其子公司)?(?:采用|以)人民币(?:作为|为)记账本位币[。.]?\Z')
_PEER_PRESENTATION_CURRENCY = re.compile(r'(?:^|[。；;])本(?:合并)?财务报表(?:以|采用)人民币(?:列示|列报)[。；;.]')
_BANK_PROFIT_LINE = re.compile(r'^\s*(?P<metric>利息净收入|净利息收入|信用减值损失)\s+(?P<values>.+)$')
_PEER_KEY_FINANCIALS = re.compile(rf'(?:{_PEER_NUMBERING})?(?:公司)?主要会计数据和财务指标')
_PEER_PROFIT_SUMMARY = re.compile(
    rf'^归属于(?:母公司(?:所有者|股东)|上市公司股东)的(?P<adjusted>扣除非经常性损益(?:的|后))?净利润'
    rf'(?P<unit>[（(](?:人民币)?(?:亿元|百万元|万元|千元|元)[）)])?\s+'
    rf'(?P<current>{NUMBER})\s+(?P<prior>{NUMBER})\s+{NUMBER}%?\s*$')
_PEER_BASIS_NOTES = {
    'revenue_excludes_investment': re.compile(r'公司营业总收入不包括证券业务产生的投资收益及公允价值变动损益'),
    'prior_merger_gain': re.compile(r'营业外收入变动原因说明[:：]主要是上年同期吸收合并[\u3400-\u9fff]{2,24}产生的负商誉较大'),
}


def _peer_columns(preamble, latest, prior, mixed):
    """Choose only the group columns, including statements printed beside the parent."""
    expected = [latest, prior] * (2 if mixed else 1)
    combined = ''.join(map(compact, preamble))
    if mixed and not re.search(r'本集团本(?:公司|银行|行)', combined):
        return None
    years = [(index, year) for index, line in enumerate(preamble) for year in
             re.findall(r'(?<!\d)(\d{4})年(?:半年度|度|0?1[-—－至]0?6月)?(?=\d{4}年|$)', compact(line))]
    if [year for _, year in years] == expected:
        return '\n'.join(preamble[years[0][0]:years[-1][0]+1])
    if not mixed and not years:
        relative = [(index, label) for index, line in enumerate(preamble)
                    for label in re.findall(r'本期发生额|上期发生额', compact(line))]
        if [label for _, label in relative] == ['本期发生额', '上期发生额']:
            return '\n'.join(preamble[relative[0][0]:relative[-1][0]+1])
    return None


def _peer_table_currency(page, preamble, rmb_terms, mixed):
    unit = _declared_table_unit(preamble, omit_preparer_label=True)
    if unit:
        return unit[0], dict(unit_basis=_basis(page, unit[1]))
    captions = [(line, match['unit']) for line in preamble if (match := _PEER_CURRENCY_CAPTION.fullmatch(compact(line)))]
    marked_currency = [line for line in preamble if _UNIT_OR_CURRENCY.search(compact(line))]
    if len(captions) == 1 and not marked_currency:
        line, scale = captions[0]
        return scale, dict(unit_basis=_basis(page, line))
    marked = [(line, re.sub(r'编制单位[:：]', '', compact(line))) for line in preamble if _UNIT_OR_CURRENCY.search(compact(line))]
    declared = _declared_unit_context([text for _, text in marked])
    if declared and declared[0] in rmb_terms and not declared[1]:
        return declared[0], dict(unit_basis=_basis(page, '\n'.join(line for line, _ in marked)),
                                currency_basis=rmb_terms[declared[0]])
    units = [line for line in preamble if '人民币元' in compact(line)]
    if (mixed and units and all(re.fullmatch(r'(?:人民币元)+', compact(line)) for line in units)
            and sum(compact(line).count('人民币元') for line in units) == 4):
        return '元', dict(unit_basis=_basis(page, '\n'.join(units)))
    return None


def _peer_total_lines(lines, start, stop):
    for index in range(start, stop):
        quote = lines[index]
        match = _PEER_TOTAL.fullmatch(quote)
        prefix = compact(lines[index-1]) if index > start else ''
        if match is None and prefix in ('归属于：', '归属于:'):
            label = quote.lstrip()
            if label.startswith(('本行股东的净利润', '母公司股东的净利润', '母公司所有者的净利润')):
                match = _PEER_TOTAL.fullmatch('归属于'+label)
                if match:
                    quote = lines[index-1]+'\n'+quote
        if match is None and prefix in ('净利润归属于：', '净利润归属于:'):
            owner = re.fullmatch(r'\s*母公司股东\s+(?P<values>.+)', quote)
            if owner:
                match = _PEER_TOTAL.fullmatch('归属于母公司股东的净利润 '+owner['values'])
                quote = lines[index-1]+'\n'+quote
        if match is None and index+1 < stop and (re.match(_PEER_LOSS_SUFFIX, lines[index+1])
                                                 or _PEER_WRAPPED_PROFIT.fullmatch(quote)):
            quote += '\n'+lines[index+1]
            match = _PEER_TOTAL.fullmatch(quote.replace('\n', ' '))
        if match:
            yield index, match, quote


def _peer_following_profit(pages, page_index):
    if page_index+1 >= len(pages):
        return []
    current, following = pages[page_index:page_index+2]
    previous_page, next_page = (re.fullmatch(r'第(\d+)页', part['locator']) for part in (current, following))
    if not previous_page or not next_page or int(next_page[1]) != int(previous_page[1])+1:
        return []
    lines = following['text'].splitlines()
    stop = next((index for index, line in enumerate(lines) if _PEER_STATEMENT.fullmatch(compact(line))
                 or _PEER_STATEMENT_END.fullmatch(compact(line))), len(lines))
    totals = [(index, match, quote) for index, match, quote in _peer_total_lines(lines, 0, stop)
              if match['metric'].startswith('归属')]
    if len(totals) != 1:
        return []
    index, match, quote = totals[0]
    comp = [compact(line) for line in lines[:index]]
    # A true continuation reaches taxation and ownership allocation before the net-profit row.
    if (not any(line.startswith(('减:所得税费用', '减：所得税费用', '所得税费用')) for line in comp)
            or not any('按所有权归属分类' in line for line in comp)):
        return []
    return [(following, match, quote)]


def _peer_revenue_lines(lines, start, income, stop):
    # A statement may print the total either after the components or as the first row.
    above = [index for index in range(start, income) if compact(lines[index]) == '营业收入']
    if above:
        first, end = above[-1]+1, income
    else:
        first = income+1
        end = next((index for index in range(first, stop) if _PEER_EXPENSE.match(compact(lines[index]))), None)
        if end is None:
            return
    for index in range(first, end):
        match = _PEER_COMPONENT.fullmatch(lines[index])
        if not match:
            continue
        # A wrapped associated-company subline may also end in “投资收益”.
        prefix = compact(lines[index-1])
        if match['metric'].startswith('投资') and re.search(r'其中[:：].*(?:合营|联营).*的$', prefix):
            continue
        yield match, lines[index]


def _peer_adjusted_profit(pages, reported, rmb_terms):
    """Match a supplementary profit table to the verified statement values."""
    if len(reported) != 2:
        return []
    current, prior = reported
    expected = (current['value'], prior['value'])
    matches = []
    for page in pages[:32]:
        lines = [line.strip() for line in page['text'].splitlines()]
        anchor = next((i for i, line in enumerate(lines) if _PEER_KEY_FINANCIALS.fullmatch(compact(line))), None)
        if anchor is None:
            continue
        plain, adjusted = [], []
        for index in range(anchor+1, min(anchor+28, len(lines))):
            quote = lines[index]
            row = _PEER_PROFIT_SUMMARY.fullmatch(quote)
            if row is None and quote.startswith('归属于') and '扣除非经常性' in quote and index+1 < len(lines):
                quote += '\n'+lines[index+1]
                row = _PEER_PROFIT_SUMMARY.fullmatch(quote.replace('\n', ''))
            if row:
                (adjusted if row['adjusted'] else plain).append((index, row, quote))
        if len(plain) != 1 or len(adjusted) != 1:
            continue
        (index, net, _), = plain
        (_, extra, quote), = adjusted
        header = lines[anchor+1:index]
        if not re.search(r'本报告期(?:（[^）]+）|\([^)]*\))?上年同期', ''.join(map(compact, header))):
            continue
        if tuple(_amount(net[key]) for key in ('current', 'prior')) != expected:
            continue
        row_units = [row['unit'].strip('（）()').removeprefix('人民币') if row['unit'] else None for row in (net, extra)]
        unit = _peer_table_currency(page, header, rmb_terms, False)
        if unit is None and row_units == [current['unit']]*2 and current['unit'] in rmb_terms:
            unit = current['unit'], dict(unit_basis=_basis(page, quote), currency_basis=rmb_terms[current['unit']])
        if unit is None or unit[0] != current['unit'] or any(value not in (None, unit[0]) for value in row_units):
            continue
        pair = []
        for basis, key in zip(reported, ('current', 'prior'), strict=True):
            token = extra[key]
            if (value := _amount(token)) is None:
                break
            locator = dict(basis['locator'], part=page['locator'], quote=quote, raw_token=token,
                           table_header='\n'.join(header), **unit[1])
            pair.append(dict(basis, field=PEER_ADJUSTED_PROFIT, label='扣非归母净利润', value=value, locator=locator))
        if len(pair) == 2:
            matches.append(pair)
    return matches[0] if len(matches) == 1 else []


def _peer_amount_pair(match, years, count, *, footnotes=False):
    text = (_PEER_NOTE_PREFIX.sub('', match['values'], count=1) if footnotes else
            re.sub(r'^\s*[一二三四五六七八九十]+[、．.]\d+\s+', '', match['values']))
    text = re.sub(r'^\s*(?:注释|附注(?:[一二三四五六七八九十]+)?)[.．、\s]*\d{1,3}\s+', '', text)
    if not _PEER_AMOUNTS.fullmatch(text):
        return []
    tokens = re.findall(NUMBER, text)
    if len(tokens) == count+1 and re.fullmatch(r'\d{1,3}', tokens[0]):
        tokens = tokens[1:]
    if len(tokens) != count:
        return []
    values = tuple(_amount(token) for token in tokens[:2])
    if None in values:
        return []
    return list(zip(years, values, tokens[:2], strict=True))


def _peer_cells(fields, field, label, amounts, unit, locator):
    return [dict(field=field, label=label, code=fields['issuer_code'],
                 issuer=fields.get('issuer_name', fields['issuer_code']), period=year+'-06-30',
                 value=value, unit=unit, locator=dict(locator, raw_token=token))
            for year, value, token in amounts]


def _peer_statement_regions(pages, page_index, start):
    """Keep physical pages together only up to the next statement caption."""
    previous = None
    for offset, page in enumerate(pages[page_index:]):
        number = re.fullmatch(r'第(\d+)页', page['locator'])
        current = int(number[1]) if number else None
        if offset and (previous is None or current != previous + 1):
            return
        lines = page['text'].splitlines()[start:]
        end = next((i for i, line in enumerate(lines) if _PEER_STATEMENT_END.fullmatch(compact(line))
                    or _PEER_STATEMENT.fullmatch(compact(line))), len(lines))
        yield page, lines[:end]
        if end < len(lines):
            return
        previous, start = current, 0


def _peer_current_regions(regions, years, unit, rmb_terms, mixed):
    """A continued page cannot replace the anchor's column order or currency."""
    for page, lines in regions[1:]:
        header = [line for line in lines[:8] if re.search(r'^项目\D{0,10}20\d{2}年', compact(line))]
        if header and not _peer_columns(header, *years, mixed):
            return False
        markings = [line for line in lines[:8] if _UNIT_OR_CURRENCY.search(compact(line))]
        if markings:
            continued_unit = _peer_table_currency(page, markings, rmb_terms, mixed)
            if not continued_unit or continued_unit[0] != unit:
                return False
    return True


def _peer_cash_rows(regions):
    """Locate operating cash flow before the investing section of the same statement."""
    operating = False
    for page, section in regions:
        for line in section:
            label = compact(line)
            if re.fullmatch(r'(?:[一1][、．.])?经营活动产生的现金流量[:：]?', label):
                operating = True
            if re.fullmatch(r'(?:[二2][、．.])?投资活动产生的现金流量[:：]?', label):
                return
            match = _PEER_OPERATING_LINE.fullmatch(line) if operating else None
            if match and match['metric'] in _PEER_CASH_FIELDS:
                yield page, match, line


def _peer_operating_income_rows(regions, years, mixed, footnotes):
    """Read only income, cost and parent profit in the anchored group table."""
    rows = []
    for page, lines in regions:
        for index, line in enumerate(lines):
            match = _PEER_OPERATING_LINE.fullmatch(line) or _PEER_TOTAL.fullmatch(line)
            quote = line
            if _PEER_WRAPPED_PROFIT.fullmatch(line):
                for length in (2, 3):
                    parts = lines[index:index+length]
                    if len(parts) != length:
                        break
                    joined = _PEER_SPLIT_LOSS.sub(lambda item: compact(item[0]), ' '.join(part.strip() for part in parts))
                    combined = _PEER_TOTAL.fullmatch(joined)
                    if combined and _peer_amount_pair(combined, years, 4 if mixed else 2, footnotes=footnotes):
                        match, quote = combined, '\n'.join(parts)
                        break
            if match and (match['metric'].startswith(('营业', '归属'))):
                rows.append((page, match, quote))
    if any(match['metric'] == '营业收入' for _, match, _ in rows):
        return [row for row in rows if not row[1]['metric'].startswith('营业总收入')]
    return rows


def _peer_profit_rows(regions, years, count, footnotes):
    """Read disjoint profit lines before other comprehensive income."""
    for page, lines in regions:
        for index, line in enumerate(lines):
            if _PEER_COMPREHENSIVE.match(compact(line)):
                return
            match = _PEER_PROFIT_ROW.fullmatch(line)
            if not match:
                continue
            tail = match['tail'].lstrip()
            quote = line
            if tail.startswith(('（', '(')):
                # Some filers wrap the negative-sign convention over two lines.
                if not re.search(r'[）)]', tail) and index+1 < len(lines):
                    tail += ' '+lines[index+1].strip()
                    quote += '\n'+lines[index+1]
                closing = re.search(r'[）)]', tail)
                if not closing or not _PEER_PROFIT_LOSS_SUFFIX.fullmatch(compact(tail[:closing.end()])):
                    continue
                tail = tail[closing.end():].lstrip()
            values = re.fullmatch(r'(?P<values>.+)', tail)
            if values and (amounts := _peer_amount_pair(values, years, count, footnotes=footnotes)):
                yield page, match['metric'], quote, amounts


def _peer_currency_note(pages):
    findings = []
    for page in pages:
        lines = [line.strip() for line in page['text'].splitlines() if line.strip()]
        for index, caption in enumerate(lines[:-1]):
            if re.fullmatch(rf'(?:{_PEER_NUMBERING})?记账本位币', compact(caption)):
                findings.append((page, lines[index:index+4]))
    if len(findings) == 1:
        page, lines = findings[0]
        if _PEER_CURRENCY_NOTE.fullmatch(compact(lines[1])):
            return _basis(page, '\n'.join(lines[:2]))
        for end in range(2, len(lines)+1):
            if _PEER_PRESENTATION_CURRENCY.search(compact(''.join(lines[1:end]))):
                return _basis(page, '\n'.join(lines[:end]))
    return None


def peer_filing_cells(record, periods, *, revenue_bridge=False, bank_profit_bridge=False,
                      operating_drivers=False, profit_details=False):
    """Read comparable consolidation cells and optional revenue contributions."""
    fields = disclosure_fields(record)
    if not fields.get('issuer_code') or not half_year_filing_title(fields.get('title'), periods[-1]):
        return []
    snapshot = record['snapshot']
    material = snapshot.get('material', snapshot)
    if material.get('status') != '已解析' or (record.get('source') == 'official'
            and snapshot.get('official_origin', {}).get('format') != 'pdf'):
        return []
    prior, latest = (period[:4] for period in periods)
    years = latest, prior
    pages = [part for part in material.get('parts', ()) if not part.get('pdf_table')]
    interval = re.compile(rf'{latest}年0?1月0?1日(?:至|[-—－])(?:{latest}年)?0?6月30日')
    statement_interval = re.compile(rf'截至(?:{latest}年)?0?6月30日止(?:6|六)个月(?:期间)?\Z')
    half_year_interval = re.compile(rf'{latest}年0?1(?:月)?(?:至|[-—－–])0?6月\Z')
    period_basis = next((_basis(part, line) for part in pages[:12] for line in part['text'].splitlines()
                         if interval.search(compact(line))), None)
    rmb_terms = {}
    for part in pages[:12]:
        for line in part['text'].splitlines():
            meaning = _PEER_CURRENCY_TERMS.fullmatch(compact(line))
            if meaning and ['人民币'+scale for scale in meaning['scales'].split('、')] == meaning['currencies'].split('、'):
                rmb_terms.update({scale: _basis(part, line) for scale in meaning['scales'].split('、')})
    if operating_drivers and '元' not in rmb_terms and (currency_note := _peer_currency_note(pages)):
        rmb_terms['元'] = currency_note
    by_field = defaultdict(list)
    for page_index, page in enumerate(pages):
        lines = page['text'].splitlines()
        captions = [(i, match) for i, line in enumerate(lines)
                    if (match := _PEER_STATEMENT.fullmatch(compact(line))
                        or operating_drivers and _PEER_CASH_STATEMENT.fullmatch(compact(line)))]
        if len(captions) != 1:
            continue
        (index, caption), = captions
        cash = caption.re is _PEER_CASH_STATEMENT
        mixed = not cash and bool(caption['both'])
        start = index + 1
        comparison_page = next((_basis(page, line) for line in lines[start:start+16]
                                if interval.search(compact(line)) or statement_interval.fullmatch(compact(line))
                                or operating_drivers and half_year_interval.fullmatch(compact(line))), period_basis)
        stop = next((offset for offset in range(start, len(lines)) if _PEER_STATEMENT_END.fullmatch(compact(lines[offset]))
                     or _PEER_STATEMENT.fullmatch(compact(lines[offset]))), len(lines))
        if cash:
            lines_before_cash = lines[start:min(start+16, stop)]
            boundary = next((i for i, line in enumerate(lines_before_cash)
                             if '经营活动产生的现金流量' in compact(line)), len(lines_before_cash))
            preamble = lines_before_cash[:boundary]
            columns = [line for line in preamble if not half_year_interval.fullmatch(compact(line))]
            header = _peer_columns(columns, latest, prior, False)
            unit = _peer_table_currency(page, preamble, rmb_terms, False)
            if comparison_page is None and header and latest+'年' in compact(header):
                comparison_page = _basis(page, header)
            if not comparison_page or not header or not unit:
                continue
            regions = tuple(_peer_statement_regions(pages, page_index, start))
            if not _peer_current_regions(regions, years, unit[0], rmb_terms, False):
                continue
            footnotes = any('附注' in line for line in preamble)
            for actual_page, match, quote in _peer_cash_rows(regions):
                label = match['metric']
                field = _PEER_CASH_FIELDS[label]
                locator = dict(kind='peer_filing_cell', part=actual_page['locator'], quote=quote,
                               table_header=header, **unit[1], period_basis=comparison_page,
                               statement_basis=_basis(page, lines[index]), statement='合并报表',
                               restated=any('已重述' in line for line in preamble))
                amounts = _peer_amount_pair(match, years, 2, footnotes=footnotes)
                if amounts:
                    by_field[field].append(_peer_cells(fields, field, label, amounts, unit[0], locator))
            continue
        rows = list(_peer_total_lines(lines, start, stop))
        if operating_drivers:
            rows.extend((offset, match, lines[offset]) for offset in range(start, stop)
                        if (match := _PEER_OPERATING_LINE.fullmatch(lines[offset]))
                        and match['metric'] in ('营业收入', '营业成本'))
        bank_rows = [(offset, match, lines[offset]) for offset in range(start, stop)
                     if bank_profit_bridge and (match := _BANK_PROFIT_LINE.fullmatch(lines[offset]))]
        if not (rows or bank_rows or operating_drivers):
            continue
        first_row = min((offset for offset, _, _ in rows + bank_rows), default=stop)
        preamble = lines[start:min(start+18, first_row)]
        columns = ([line for line in preamble if not half_year_interval.fullmatch(compact(line))]
                   if operating_drivers else preamble)
        header = _peer_columns(columns, latest, prior, mixed)
        unit = _peer_table_currency(page, preamble, rmb_terms, mixed)
        if comparison_page is None and operating_drivers and header and latest+'年' in compact(header):
            comparison_page = _basis(page, header)
        if comparison_page is None or header is None or unit is None:
            continue
        footnotes = operating_drivers and any('附注' in line for line in preamble)
        if operating_drivers:
            regions = tuple(_peer_statement_regions(pages, page_index, start))
            if not _peer_current_regions(regions, years, unit[0], rmb_terms, mixed):
                continue
            located = _peer_operating_income_rows(regions, years, mixed, footnotes)
            if profit_details:
                for amount_page, label, quote, amounts in _peer_profit_rows(regions, years, 4 if mixed else 2, footnotes):
                    field = _PEER_PROFIT_IDS[label]
                    locator = dict(kind='peer_filing_cell', part=amount_page['locator'], quote=quote,
                                   table_header=header, **unit[1], period_basis=comparison_page,
                                   statement_basis=_basis(page, lines[index]), statement='合并报表',
                                   restated=any('已重述' in line for line in preamble))
                    by_field[field].append(_peer_cells(fields, field, label, amounts, unit[0], locator))
        else:
            located = [(page, match, quote) for _, match, quote in rows + bank_rows]
            income = next((offset for offset, match, _ in rows if match['metric'].startswith('营业')), None)
            if income is not None:
                if revenue_bridge:
                    located.extend((page, match, quote) for match, quote in _peer_revenue_lines(lines, start, income, stop))
                if not mixed and stop == len(lines) and not any(match['metric'].startswith('归属') for _, match, _ in rows):
                    located.extend(_peer_following_profit(pages, page_index))
        for amount_page, match, quote in located:
            ownership_only = compact(quote).startswith(('净利润归属于：母公司股东', '净利润归属于:母公司股东'))
            count = 4 if mixed and not ownership_only else 2
            amounts = _peer_amount_pair(match, years, count, footnotes=footnotes)
            if not amounts:
                continue
            metric = match['metric']
            if match.re is _BANK_PROFIT_LINE:
                field = BANK_CREDIT_IMPAIRMENT if metric == '信用减值损失' else BANK_INTEREST_INCOME
                label = BANK_PROFIT_COMPONENTS[field]
            elif match.re is _PEER_COMPONENT:
                label = ('公允价值变动收益' if metric.startswith('公允价值变动')
                         else '投资收益' if metric.startswith('投资') else metric)
                field = _PEER_COMPONENT_IDS[label]
            elif metric == '营业成本':
                field, label = PEER_OPERATING_FIELDS[0], metric
            else:
                field = 'ths_revenue_stock' if metric.startswith('营业') else 'ths_np_atoopc_stock'
                label = '营业收入' if field == 'ths_revenue_stock' else '归母净利润'
            locator = dict(kind='peer_filing_cell', part=amount_page['locator'], quote=quote,
                           table_header=header, **unit[1],
                           period_basis=comparison_page, statement_basis=_basis(page, lines[index]),
                           statement='合并报表', restated=any('已重述' in line for line in preamble))
            by_field[field].append(_peer_cells(fields, field, label, amounts, unit[0], locator))
    cells = {field: pairs[0] for field, pairs in by_field.items()
             if len({tuple((row['value'], row['unit']) for row in pair) for pair in pairs}) == 1}
    if revenue_bridge:
        adjusted = _peer_adjusted_profit(pages, cells.get('ths_np_atoopc_stock', []), rmb_terms)
        if adjusted:
            cells[PEER_ADJUSTED_PROFIT] = adjusted
        for key, expression in _PEER_BASIS_NOTES.items():
            field = 'ths_revenue_stock' if key == 'revenue_excludes_investment' else 'ths_np_atoopc_stock'
            if field not in cells:
                continue
            note = next((_basis(part, line) for part in pages[:64] for line in part['text'].splitlines()
                         if expression.search(compact(line))), None)
            if note:
                cells[field][0]['locator']['comparison_note'] = dict(note, kind=key)
    return [cell for pair in cells.values() for cell in pair]


def verified_filing_locations(content, rows, scope):
    """Recover only cells still identical to the report's archived original."""
    selected = defaultdict(list)
    for row in rows:
        if is_filing_amount(row):
            selected[row['artifact_id']].append(row)
    if not selected:
        return {}
    records = defaultdict(list)
    for record in content['source_records']:
        if (record.get('id') in selected and record.get('task_id') == content['task_id']
                and record.get('research_version') == content['research_version']):
            records[record['id']].append(record)
    verified = {}
    for ref, candidates in selected.items():
        owned = records[ref]
        if len(owned) != 1 or owned[0].get('snapshot_hash', owned[0].get('hash')) != fingerprint(owned[0].get('snapshot')):
            continue
        cells = filing_amounts(owned[0], scope, reader_version=content.get('draft_source_reader_version'))
        peers = [row for row in candidates if row['locator']['kind'] == 'peer_filing_cell']
        if peers:
            periods = sorted({row['period'].replace('-', '') for row in peers})
            if len(periods) == 2 and all(re.fullmatch(r'\d{4}0630', period) for period in periods):
                cells.extend(peer_filing_cells(owned[0], periods,
                    revenue_bridge=any(row['field'] in PEER_REVENUE_COMPONENTS for row in peers),
                    bank_profit_bridge=any(row['field'] in BANK_PROFIT_COMPONENTS for row in peers),
                    operating_drivers=any(row['field'] in (*PEER_OPERATING_FIELDS, *PEER_CASH_COMPONENTS.values())
                                          for row in peers)))
        comparative = [row for row in candidates if row['locator']['kind']=='filing_segment_comparison'
                       or row['locator'].get('segment_total')]
        if comparative:
            periods = sorted({row['period'].rsplit('至', 1)[-1].replace('-', '') for row in comparative})
            code = owned[0].get('directory', {}).get('issuer')
            if code and len(periods) == 2 and all(re.fullmatch(r'\d{4}0630', period) for period in periods):
                cells.extend(segment_revenue_cells(owned[0], dict(code=code, periods=periods)))
        for cell in cells:
            period = cell['period'].replace('/', '至')
            unit = '人民币元' if cell['unit'] == '元' else cell['unit']
            for row in candidates:
                if (row.get('code') == cell['code'] and row.get('field') == cell['field']
                        and row.get('period') == period and row.get('raw_value') == str(cell['value'])
                        and row.get('raw_unit') == cell['unit'] and row.get('unit') == unit
                        and row['locator'] == cell['locator']):
                    verified[row['id']] = cell['locator']
    return verified
