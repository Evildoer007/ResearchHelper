"""Deterministic data products from retained half-year disclosures.

This module does not fetch, reason, approve evidence or modify a task. It
preserves original cells and locations for the later financial validator.
"""
from __future__ import annotations

import base64
import copy
from decimal import Decimal, InvalidOperation
import hashlib
import re
import unicodedata

from .contracts import ContractError, fingerprint
from .financial_sources import (financial_table_candidates, financial_context_cells,
    financial_shared_context_choices, financial_reporting_currency_choices,
    financial_reporting_standard_choices, context_binding)
from .evidence import current_original_reference

UNIT_FACTORS = {'元': Decimal(1), '千元': Decimal(1000), '万元': Decimal(10000),
                '百万元': Decimal(1000000), '亿元': Decimal(100000000)}
NUMBER = r'(?:[（(]\s*\d[\d,]*(?:\.\d+)?\s*[）)]|[+−-]?\d[\d,]*(?:\.\d+)?)'
DURATION = re.compile(r'截至\s*(\d{4})\s*年\s*6\s*月\s*30\s*日\s*止\s*6\s*个\s*月\s*期间')
CHAPTER = re.compile(r'第[一二三四五六七八九十]+节[\u4e00-\u9fff、]+')


class FinancialDataUnavailable(ContractError):
    pass


def compact(value):
    return re.sub(r'\s+', '', value)


def _validate_periods(periods):
    if (not isinstance(periods, list) or len(periods) != 2
            or any(not isinstance(period, str) or not re.fullmatch(r'\d{4}0630', period) for period in periods)
            or len(set(periods)) != 2):
        raise FinancialDataUnavailable('请明确两个不同年度的半年度期间')


def monetary_cell(value):
    """Exact printed sign, including accounting parentheses; never fill zero."""
    if not isinstance(value, str):
        raise FinancialDataUnavailable('金额单元格不是原文字符串')
    token = unicodedata.normalize('NFKC', value).strip().replace('−', '-')
    negative = token.startswith('(') and token.endswith(')')
    if negative:
        token = token[1:-1].strip()
    if not re.fullmatch(r'[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?', token):
        raise FinancialDataUnavailable('金额缺失或格式不明确，未补值')
    if negative and token[:1] in ('+', '-'):
        raise FinancialDataUnavailable('括号与显式正负号并存，未猜测符号')
    try:
        number = Decimal(token.replace(',', ''))
    except InvalidOperation:
        raise FinancialDataUnavailable('金额不是有限十进制数') from None
    if not number.is_finite():
        raise FinancialDataUnavailable('金额不是有限十进制数')
    return -number if negative else number


def _page(part):
    match = re.match(r'第(\d+)页', part['locator'])
    return int(match[1]) if match else -1


def _physical(record):
    return sorted((part for part in record['snapshot']['material']['parts']
                   if not part.get('pdf_table')), key=_page)


def _citation(record, part, quote):
    if quote not in part['text']:
        raise FinancialDataUnavailable('数据摘录不属于指定原件片段')
    return dict(artifact_id=record['id'], snapshot_hash=record['hash'],
                original_hash=record['snapshot']['material']['hash'],
                part=part['locator'], quote=quote)


def proven_disclosures(task, security):
    """Check actual acquisition and bytes; this is not financial adoption."""
    result = []
    for record in task.get('source_records', []):
        if record.get('kind') != 'filing' or record.get('directory', {}).get('issuer') != security:
            continue
        if record.get('task_id') != task['id'] or record.get('research_version') != task['version']:
            raise FinancialDataUnavailable('原件不属于本任务同版标的')
        acquisition = record.get('acquisition', {})
        if current_original_reference(task, record) is None or type(acquisition.get('dispatch_index')) is not int:
            raise FinancialDataUnavailable('原件没有实际取得记录')
        try:
            event = task['ledger']['dispatches'][acquisition['dispatch_index']]
        except (KeyError, IndexError):
            raise FinancialDataUnavailable('原件请求账本缺失') from None
        material = record['snapshot']['material']
        if (event.get('operation') != 'filing_original' or event.get('status') != '成功'
                or event.get('source') != 'ifind' or event.get('response_snapshot_hash') != fingerprint(record['snapshot'])
                or record['hash'] != fingerprint(record['snapshot'])
                or event.get('response_original_hash') != material['hash']):
            raise FinancialDataUnavailable('原件与实际响应或账本不一致')
        try:
            raw = base64.b64decode(material['original_base64'], validate=True)
        except (ValueError, KeyError):
            raise FinancialDataUnavailable('原件字节缺失') from None
        if hashlib.sha256(raw).hexdigest() != material['hash']:
            raise FinancialDataUnavailable('原件字节指纹不一致')
        result.append(record)
    return result


def _cfo_contexts(task, record, periods=None):
    material = record['snapshot']['material']
    view = dict(id=record['id'], kind=record['kind'], snapshot_hash=record['hash'], parts=material['parts'])
    result = {}
    for cell in financial_table_candidates(task, record, view, maximum=64)['cells']:
        if cell['metric'] != 'operating_cash_flow':
            continue
        fields = cell['proposal_fields']
        match = re.fullmatch(r'截至(\d{4})年6月30日止6个月期间', compact(fields['period']))
        period = match[1] + '0630' if match else None
        if period is None or periods is not None and period not in periods:
            continue
        if period in result:
            raise FinancialDataUnavailable('同一期间现金流列不唯一')
        result[period] = fields
    return result


def core_financial_values(task, security, periods):
    """Four requested statement fields, with independently located context.

    API numbers without a declared unit remain separate raw responses. This
    product uses explicitly labelled original cells and never repairs the API.
    Multiple originals may corroborate a cell; conflicting originals stay open.
    """
    _validate_periods(periods)
    metrics = ('revenue', 'operating_cost', 'net_profit', 'operating_cash_flow')
    matches = {(period, metric): [] for period in periods for metric in metrics}
    for record in proven_disclosures(task, security):
        parts = record['snapshot']['material']['parts']
        view = dict(id=record['id'], kind=record['kind'], snapshot_hash=record['hash'], parts=parts)
        candidates = financial_table_candidates(task, record, view, maximum=64)['cells']
        contexts = {item['financial_value_reference']: item
                    for item in financial_context_cells(task, record, view)}
        shared = financial_shared_context_choices(task, record, view)
        standards = shared['accounting_standard']
        entities = shared['entity_id']
        indexed = {part['locator']: part for part in parts}
        for cell in candidates:
            fields = cell['proposal_fields']
            duration = DURATION.fullmatch(fields['period'])
            period = duration[1]+'0630' if duration else None
            key = (period, cell['metric'])
            if key not in matches:
                continue
            context = contexts.get(cell['candidate_id'])
            locator = fields['record_locator']
            basis = {name: [_citation(record, indexed[item['basis']['part']], item['basis']['quote'])
                            for item in items] for name, items in
                     (('entity', entities), ('accounting_standard', standards))}
            complete = bool(context and context['context']['accounting_scope'] == 'consolidated'
                            and entities and len({item['value'] for item in standards}) == 1)
            matches[key].append(dict(period=period, metric=cell['metric'],
                value=str(monetary_cell(fields['raw_token'])), unit=fields['raw_unit'],
                raw_token=fields['raw_token'], period_literal=fields['period'],
                citation=_citation(record, indexed[locator['part']], fields['text']),
                unit_basis=copy.deepcopy(locator.get('financial_unit_scope')),
                context=copy.deepcopy(context), shared_context_basis=basis,
                accounting_standard=standards[0]['value'] if complete else None,
                issuer=security, context_complete=complete))
    rows, missing = [], []
    for (period, metric), choices in matches.items():
        signatures = {(item['value'], item['unit'], item['accounting_standard'],
                       item['context']['context']['currency'] if item['context'] else None,
                       item['context']['context']['accounting_scope'] if item['context'] else None)
                      for item in choices}
        if not choices:
            missing.append(dict(period=period, metric=metric, reason='未定位所需原件单元格'))
        elif len(signatures) != 1 or not all(item['context_complete'] for item in choices):
            missing.append(dict(period=period, metric=metric,
                reason='存在冲突金额或单位、币种、主体、准则依据不完整', candidates=choices))
        else:
            row = copy.deepcopy(choices[0])
            row['corroborating_original_cells'] = choices
            rows.append(row)
    return dict(status='decoded' if not missing else 'partial' if rows else 'incomplete',
        binding=context_binding(task), security=security, periods=list(periods), values=rows, missing=missing,
        financial_adoption=False, api_unit_inferred=False,
        consolidation_perimeter_comparison_performed=False)


def capital_expenditure(task, record, periods):
    contexts = _cfo_contexts(task, record, periods)
    parts = {part['locator']: part for part in record['snapshot']['material']['parts']}
    output, missing = [], []
    for period in periods:
        if period not in contexts:
            missing.append(dict(period=period, reason='缺少同表期间和单位定位'))
            continue
        context = contexts[period]; locator = context['record_locator']
        part = parts[locator['part']]
        rows = [line for line in part['text'].splitlines()
                if compact(line.split('\t')[0]) == '购建固定资产、无形资产和其他长期资产支付的现金']
        if len(rows) != 1:
            missing.append(dict(period=period, reason='合并现金流量表的资本开支行缺失或不唯一'))
            continue
        column = locator['financial_column']['column_index']; columns = rows[0].split('\t')
        if column >= len(columns):
            missing.append(dict(period=period, reason='资本开支列缺失'))
            continue
        try:
            value = monetary_cell(columns[column])
        except FinancialDataUnavailable as error:
            missing.append(dict(period=period, reason=str(error)))
            continue
        output.append(dict(period=period, value=str(value),
            raw_token=columns[column], unit=context['unit'], period_literal=context['period'],
            unit_basis=copy.deepcopy(locator.get('financial_unit_scope')),
            period_basis=_citation(record, parts[locator['financial_column']['header_part']],
                                   locator['financial_column']['header_quote']),
            citation=_citation(record, part, rows[0])))
    return dict(status='decoded' if not missing else 'partial' if output else 'incomplete',
                values=output, missing=missing, financial_adoption=False)


def cash_reconciliation(task, record, periods):
    contexts = _cfo_contexts(task, record, periods)
    if not contexts:
        raise FinancialDataUnavailable('现金流调节缺少同版合并现金流基准')
    missing = [period for period in periods if period not in contexts]
    proofs = [context['record_locator'].get('financial_unit_scope') for context in contexts.values()]
    if (any(not proof or '财务报表及附注' not in compact(proof.get('unit_quote', '')) for proof in proofs)
            or len({context['unit'] for context in contexts.values()}) != 1):
        raise FinancialDataUnavailable('财务附注没有明确的共同单位声明')
    unit = next(iter(contexts.values()))['unit']
    found = []
    pattern = re.compile(r'(?P<label>.+?)\s+(?P<left>'+NUMBER+r')\s+(?P<right>'+NUMBER+r')\s*$')
    for part in _physical(record):
        text = part['text']; start = text.find('将净利润调节为经营活动现金流量')
        end = text.find('不涉及现金的重大投资和筹资活动', start + 1)
        if start < 0 or end < 0:
            continue
        if '合并财务报表主要项目注释' not in compact(text[:start]):
            continue
        block = text[start:end]; dates = list(DURATION.finditer(block))
        if len(dates) != 2:
            continue
        columns = [match[1] + '0630' for match in dates]
        if len(set(columns)) != 2 or not set(contexts).issubset(columns):
            continue
        rows, active = [], False
        for line in block.splitlines():
            match = pattern.fullmatch(line.strip())
            if match is None:
                if active and compact(line):
                    raise FinancialDataUnavailable('现金流调节中存在未完整解析的项目行')
                continue
            label = match['label'].strip()
            if not active and compact(label) != '净利润':
                continue
            active = True
            rows.append(dict(label=label, values={period: str(monetary_cell(match[key]))
                for period, key in zip(columns, ('left', 'right')) if period in contexts},
                raw_tokens={period: match[key] for period, key in zip(columns, ('left', 'right')) if period in contexts},
                citation=_citation(record, part, line)))
            if compact(label) == '经营活动产生的现金流量净额':
                break
        if len(rows) < 2 or compact(rows[-1]['label']) != '经营活动产生的现金流量净额':
            raise FinancialDataUnavailable('现金流调节表不完整')
        checks = {}
        for period in periods:
            if period not in contexts:
                continue
            total = sum((Decimal(row['values'][period]) for row in rows[:-1]), Decimal(0))
            stated = Decimal(rows[-1]['values'][period]); benchmark = Decimal(str(contexts[period]['value']))
            checks[period] = dict(adjustment_sum=str(total), disclosed_cash_flow=str(stated),
                statement_cash_flow=str(benchmark), matches=total == stated == benchmark)
        found.append(dict(status='partial' if missing else 'decoded', unit=unit, rows=rows, checks=checks,
            missing_periods=missing,
            unit_basis=copy.deepcopy(proofs[0]),
            period_citations=[_citation(record, part, match[0]) for match in dates],
            data_checks_passed=all(check['matches'] for check in checks.values()), financial_adoption=False))
    if len(found) != 1:
        raise FinancialDataUnavailable('现金流调节完整区块缺失或不唯一')
    return found[0]


def _report_period(record):
    """Bind the report year to a literal six-month period in this original."""
    title = record['directory']['title']
    match = re.search(r'(\d{4})\s*年\s*半年度报告', title)
    if not match:
        raise FinancialDataUnavailable('原件不是明确年度的半年度报告')
    year = match[1]
    if not any(year+'年半年度报告' in compact(part['text']) for part in _physical(record)[:3]):
        raise FinancialDataUnavailable('目录与原件报告年度不一致')
    for part in _physical(record):
        for period in DURATION.finditer(part['text']):
            if period[1] == year:
                return year+'0630', _citation(record, part, period[0])
    raise FinancialDataUnavailable('半年度报告缺少明确的六个月期间原文')


def disclosure_context(record):
    """Read explicit report-level labels; do not infer an unchanged perimeter."""
    period, period_basis = _report_period(record)
    physical = _physical(record)
    parts = {part['locator']: part for part in physical}
    view = dict(parts=physical)
    currencies = financial_reporting_currency_choices(record, view)
    standards = financial_reporting_standard_choices(record, view)
    issuer_name = record['directory'].get('security_name')
    entities, units, statements = [], [], {}
    for part in physical:
        for line in part['text'].splitlines():
            if _page(part) <= 3 and issuer_name and issuer_name in compact(line):
                entities.append(_citation(record, part, line))
            unit = re.fullmatch(r'财务报表及附注的单位为[:：](百万元|千元|万元|亿元|元)', compact(line))
            if unit:
                units.append(dict(value=unit[1], citation=_citation(record, part, line)))
            caption = re.fullmatch(r'(?:[0-9一二三四五六七八九十]+[、.．])?(合并利润表|合并现金流量表)', compact(line))
            if caption:
                statements.setdefault(caption[1], []).append(_citation(record, part, line))
    missing = []
    for name, available in (('主体', bool(entities)),
        ('列报币种', len({item['value'] for item in currencies}) == 1),
        ('会计准则', len({item['value'] for item in standards}) == 1),
        ('报表与附注单位', len({item['value'] for item in units}) == 1),
        ('合并利润表及现金流量表标题', all(len(statements.get(caption, [])) == 1
          for caption in ('合并利润表', '合并现金流量表')))):
        if not available:
            missing.append(name)
    def cited(choices):
        return [dict(value=choice['value'], citation=_citation(record, parts[choice['part']], choice['quote']))
                for choice in choices]
    return dict(status='decoded' if not missing else 'partial', period=period,
        period_basis=period_basis, issuer=record['directory']['issuer'], issuer_basis=entities,
        currencies=cited(currencies), accounting_standards=cited(standards),
        statement_units=units, consolidated_statement_titles=statements, missing=missing,
        consolidation_perimeter_comparison_performed=False, financial_adoption=False)


def nonrecurring_items(record):
    period, period_basis = _report_period(record)
    physical = _physical(record)
    marker = re.compile(r'\s*'.join(map(re.escape, '非经常性损益项目及金额')))
    starts = [(part, marker.search(part['text'])) for part in physical if marker.search(part['text'])]
    if not starts:
        raise FinancialDataUnavailable('非经常性损益章节未定位')
    first, position = starts[0]
    unit_matches = list(re.finditer(r'单位\s*[:：]\s*(百万元|千元|万元|亿元|元)', first['text'][position.end():]))
    if len(unit_matches) != 1:
        raise FinancialDataUnavailable('非经常性损益单位缺失或不唯一')
    unit_match = unit_matches[0]; unit = unit_match[1]
    unit_basis = _citation(record, first, unit_match[0])
    tables = sorted((part for part in record['snapshot']['material']['parts']
        if part.get('pdf_table') and _page(part) >= _page(first)),
        key=lambda part: (_page(part), part['pdf_table'].get('bbox', [0, 0])[1]))
    rows, active, complete = [], False, False
    for part in tables:
        for line in part['text'].splitlines():
            columns = line.split('\t')
            if not active:
                if len(columns) >= 2 and compact(columns[0]) == '项目' and compact(columns[1]) == '金额':
                    active = True
                continue
            if len(columns) < 2 or not compact(columns[0]):
                raise FinancialDataUnavailable('非经常性损益表格存在不完整行')
            if compact(columns[0]) == '项目' and compact(columns[1]) == '金额':
                continue
            rows.append(dict(label=columns[0], value=str(monetary_cell(columns[1])),
                raw_token=columns[1], description='\t'.join(columns[2:]),
                citation=_citation(record, part, line)))
            if compact(columns[0]) == '合计':
                complete = True
                break
        if complete:
            break
    if not complete:
        raise FinancialDataUnavailable('非经常性损益表未取得完整合计')
    parent_profit, adjusted_profit = [], []
    for part in tables:
        if _page(part) != _page(first):
            continue
        lines = part['text'].splitlines()
        if not lines or len(lines[0].split('\t')) < 2 or compact(lines[0].split('\t')[1]) != '本报告期':
            continue
        for line in lines[1:]:
            columns = line.split('\t'); label = compact(columns[0])
            if len(columns) < 2 or not re.search(r'[（(]元[）)]', label):
                continue
            if label.startswith('归属于上市公司股东的净利润'):
                parent_profit.append(monetary_cell(columns[1]))
            elif label.startswith('归属于上市公司股东的扣除非经常性损益的净利润'):
                adjusted_profit.append(monetary_cell(columns[1]))
    if len(parent_profit) != 1 or len(adjusted_profit) != 1:
        raise FinancialDataUnavailable('非经常性损益缺少同期间归母与扣非归母交叉核对')
    total = Decimal(rows[-1]['value']) * UNIT_FACTORS[unit]
    difference = parent_profit[0] - adjusted_profit[0]
    return dict(status='decoded', period=period, unit=unit, rows=rows,
        unit_basis=unit_basis, period_basis=period_basis,
        checks=dict(disclosed_total_in_yuan=str(total), parent_profit_minus_adjusted_parent_profit=str(difference),
                    profit_scope='归属于上市公司股东，不是合并报表净利润', matches=total == difference),
        data_checks_passed=total == difference, financial_adoption=False)


def management_discussion(record):
    period, period_basis = _report_period(record)
    physical = _physical(record)
    start, end = None, None
    for index, part in enumerate(physical):
        offset = 0
        for line in part['text'].splitlines(keepends=True):
            heading = compact(line)
            if start is None and re.fullmatch(r'第[一二三四五六七八九十]+节(?:管理层讨论与分析|经营情况讨论与分析)', heading):
                start = (index, offset, line.rstrip('\n'))
            elif start is not None and CHAPTER.fullmatch(heading):
                end = (index, offset, line.rstrip('\n'))
                break
            offset += len(line)
        if end:
            break
    if start is None or end is None:
        raise FinancialDataUnavailable('管理层讨论缺少明确章节起止，未称全文取得')
    fragments = []
    for index in range(start[0], end[0]+1):
        part = physical[index]
        begin = start[1] if index == start[0] else 0
        stop = end[1] if index == end[0] else len(part['text'])
        quote = part['text'][begin:stop]
        if quote.strip():
            fragments.append(_citation(record, part, quote))
    return dict(status='decoded', period=period, period_basis=period_basis,
        start=_citation(record, physical[start[0]], start[2]),
        end=_citation(record, physical[end[0]], end[2]), fragments=fragments,
        complete_section=True, interpretation_performed=False, financial_adoption=False)


def half_year_financial_supplements(task, security, periods):
    """One raw data-module result; incomplete fields never hide usable ones."""
    _validate_periods(periods)
    records = proven_disclosures(task, security)
    by_period = {}
    for record in records:
        try:
            period, _ = _report_period(record)
        except FinancialDataUnavailable:
            continue
        by_period.setdefault(period, []).append(record)
    result = dict(version=1, binding=context_binding(task), security=security, periods=list(periods),
        fields={}, model_calls=0, data_requests=0, financial_adoption=False)
    def capture(name, operation):
        try:
            data = operation()
            result['fields'][name] = data
        except (ContractError, KeyError, IndexError) as error:
            result['fields'][name] = dict(status='incomplete', reason=str(error), financial_adoption=False)
    def original(period):
        choices = by_period.get(period, [])
        if len(choices) != 1:
            raise FinancialDataUnavailable('对应期间的原始报告缺失或不唯一')
        return choices[0]
    capture('capital_expenditure', lambda: capital_expenditure(task, original(max(periods)), periods))
    capture('cash_reconciliation', lambda: cash_reconciliation(task, original(max(periods)), periods))
    for period in periods:
        capture('nonrecurring_'+period, lambda period=period: nonrecurring_items(original(period)))
        capture('management_'+period, lambda period=period: management_discussion(original(period)))
    result['all_requested_fields_decoded'] = all(field['status'] == 'decoded' for field in result['fields'].values())
    result['all_data_checks_passed'] = result['all_requested_fields_decoded'] and all(
        field.get('data_checks_passed', True) for field in result['fields'].values())
    return result


def half_year_financial_data(task, security, periods):
    """A bounded data product for a two-period operating-results study.

    This is not a universal investment-data checklist. Other research
    questions may need prices, valuation, competitors or event-time evidence.
    """
    _validate_periods(periods)
    core = core_financial_values(task, security, periods)
    supplements = half_year_financial_supplements(task, security, periods)
    contexts = {}
    for record in proven_disclosures(task, security):
        try:
            context = disclosure_context(record)
        except FinancialDataUnavailable:
            continue
        if context['period'] in periods:
            contexts.setdefault(context['period'], []).append(context)
    missing_contexts = [period for period in periods
        if len(contexts.get(period, [])) != 1 or contexts[period][0]['status'] != 'decoded']
    ready = (core['status'] == 'decoded' and supplements['all_data_checks_passed'] and not missing_contexts)
    return dict(version=1, binding=context_binding(task), security=security, periods=list(periods), core=core,
        supplements=supplements, report_contexts=contexts, missing_context_periods=missing_contexts,
        status='ready_for_financial_validation' if ready else 'partial',
        requested_data_decoded=ready, financial_adoption=False,
        model_calls=0, data_requests=0, pdf_downloads=0, research_resumed=False)


def product_selection_enabled(task):
    version = task.get('financial_data_product_version')
    if version is None:
        return False
    if type(version) is not int or version != 1:
        raise FinancialDataUnavailable('财务数据产品选择版本未登记')
    return True


def statement_details_enabled(task):
    version=task.get('financial_statement_details_version')
    if version is None:return False
    if type(version) is not int or version!=1:
        raise FinancialDataUnavailable('财务报表明细版本未登记')
    return product_selection_enabled(task)


WORKING_CAPITAL_LABELS={'accounts_receivable':('应收账款',),'inventory':('存货',),
    'accounts_payable':('应付账款',),'contract_liabilities':('合同负债',)}
PROFIT_COMPONENT_LABELS={
    'tax_surcharge':('税金及附加',),'selling_expense':('销售费用',),
    'administrative_expense':('管理费用',),'research_development_expense':('研发费用',),
    'financial_expense':('财务费用',),'interest_expense':('其中:利息费用','利息费用'),
    'interest_income':('利息收入',),'other_income':('其他收益',),
    'investment_income':('投资收益','投资(损失)/收益'),
    'fair_value_income':('公允价值变动收益',),'credit_impairment':('信用减值损失',),
    'asset_impairment':('资产减值损失',),'asset_disposal':('资产处置收益/(损失)','资产处置损失'),
    'operating_profit':('营业利润',),'pretax_profit':('利润总额',),'income_tax_expense':('所得税费用',)}


def _statement_detail_label(text):
    text=compact(unicodedata.normalize('NFKC',text))
    return re.sub(r'^(?:[一二三四五六七八九十]+、|(?:加|减):)','',text)


def _statement_detail_values(record, *, point, labels, periods=None):
    """Read verified coordinate columns, not whitespace/note-number guesses."""
    from .pdf_material import EXTRACT_VERSION,TEXT_TABLE_VERSION
    material=record['snapshot']['material']
    if material.get('extract_version')!=EXTRACT_VERSION:
        raise FinancialDataUnavailable('该明细需要新解析版本，旧原件不自动改写')
    context=disclosure_context(record)
    if context['status']!='decoded':
        raise FinancialDataUnavailable('明细缺少明确的主体、单位、列报币种或准则')
    units=context['statement_units'];unit=units[0]['value'];unit_basis=units[0]['citation']
    originals={part['locator']:part for part in material['parts']}
    aliases={_statement_detail_label(alias):metric for metric,names in labels.items() for alias in names}
    matches={};offered_periods=set();ambiguous=set()
    for part in material['parts']:
        shape=part.get('pdf_table',{})
        if (shape.get('version')!=TEXT_TABLE_VERSION or shape.get('representation')!='aligned_words'
                or shape.get('geometry_only') is not True
                or (shape.get('point_columns') is True)!=point
                or not point and shape.get('duration_columns') is not True):continue
        lines=part['text'].splitlines();caption=shape.get('statement_caption')
        caption_text=caption['text'] if point and isinstance(caption,dict) else lines[0]
        family='合并资产负债表' if point else '合并利润表'
        if not re.fullmatch(r'(?:[一二三四五六七八九十\d]+[、.．])?'+family,compact(caption_text)):continue
        physical=originals.get(caption['part'] if point else '第'+str(shape['page'])+'页')
        captions=[line for line in physical['text'].splitlines() if compact(line)==compact(caption_text)] if physical else []
        if len(captions)!=1:raise FinancialDataUnavailable('报表明细缺少同原件合并报表标题')
        statement_basis=_citation(record,physical,captions[0])
        header_index=shape.get('header_row')
        if type(header_index) is not int or not 0<=header_index<len(lines):
            raise FinancialDataUnavailable('报表明细缺少实际表头行')
        header=lines[header_index];columns=header.split('\t')
        period_columns={}
        for index,literal in shape.get('period_columns',{}).items():
            column=int(index)
            if column>=len(columns):raise FinancialDataUnavailable('报表明细列结构不完整')
            if point:
                match=re.fullmatch(r'(\d{4})年(\d{1,2})月(\d{1,2})日',compact(columns[column]))
                if not match:continue
                from datetime import date
                value=date(*map(int,match.groups())).isoformat()
                if value!=literal:raise FinancialDataUnavailable('余额日期与实际列标题不一致')
                period=value.replace('-','');period_literal=value
            else:
                match=DURATION.fullmatch(compact(columns[column]))
                if not match or compact(literal)!=compact(columns[column]):continue
                period=match[1]+'0630';period_literal=compact(columns[column])
            if periods is not None and period not in periods:continue
            offered_periods.add(period);period_columns[column]=(period,period_literal)
        for row_index,line in enumerate(lines):
            cells=line.split('\t');metric=aliases.get(_statement_detail_label(cells[0]))
            if metric is None:continue
            coordinate_cells=shape.get('word_cells',{}).get(str(row_index))
            if len(cells)!=len(columns) or not isinstance(coordinate_cells,list) or len(coordinate_cells)!=len(columns):continue
            for column,(period,period_literal) in period_columns.items():
                key=(period,metric)
                if key in matches:ambiguous.add(key);continue
                if ' '.join(word['text'] for word in coordinate_cells[column])!=cells[column]:
                    raise FinancialDataUnavailable('报表明细与实际文字列不一致')
                try:value=monetary_cell(cells[column])
                except FinancialDataUnavailable:continue
                matches[key]=dict(metric=metric,period=period,period_literal=period_literal,
                    value=str(value),raw_token=cells[column],unit=unit,
                    citation=_citation(record,part,line),unit_basis=copy.deepcopy(unit_basis),
                    period_basis=_citation(record,part,columns[column] if point else header),statement_basis=statement_basis)
    expected=set(periods) if periods is not None else offered_periods
    missing=[dict(period=period,metric=metric,reason='明细单元缺失、非金额或不唯一')
             for period in sorted(expected) for metric in labels if (period,metric) not in matches or (period,metric) in ambiguous]
    if not expected:missing=[dict(reason='缺少明确的所需报表日期列')]
    values=[value for key,value in matches.items() if key not in ambiguous]
    return dict(status='decoded' if not missing else 'partial' if values else 'incomplete',
        values=values,missing=missing,financial_adoption=False,calculation_performed=False,
        limitation='原文金额及列位置，不把余额变化当作现金流，不把利润项目自动分类为非现金或经常性。')


def working_capital_balances(record):
    return _statement_detail_values(record,point=True,labels=WORKING_CAPITAL_LABELS)


def profit_components(record, periods=None):
    return _statement_detail_values(record,point=False,labels=PROFIT_COMPONENT_LABELS,periods=periods)


def _bounded_note_section(record, start_pattern, end_pattern):
    fragments=[];active=False;closed=False
    for part in _physical(record):
        lines=part['text'].splitlines(keepends=True);start=None
        for index,line in enumerate(lines):
            title=compact(line)
            if re.fullmatch(start_pattern,title):
                if active or closed:raise FinancialDataUnavailable('说明章节起点不唯一')
                active=True;start=index;continue
            if active and (re.fullmatch(start_pattern+r'[（(]续[）)]',title)
                    or '会计政策' in start_pattern and re.fullmatch(r'五、重要会计政策[和及]会计估计[（(]续[）)]',title)):
                continue
            if active and re.match(end_pattern,title):
                quote=''.join(lines[start if start is not None else 0:index])
                if quote:fragments.append(_citation(record,part,quote))
                active=False;closed=True;start=None;break
        if active:
            quote=''.join(lines[start if start is not None else 0:])
            if quote:fragments.append(_citation(record,part,quote))
    if not closed or not fragments:
        return dict(status='incomplete',fragments=fragments,complete_section=False,
                    absence_is_proof=False,financial_adoption=False)
    return dict(status='decoded',fragments=fragments,complete_section=True,
                absence_is_proof=False,financial_adoption=False)


def report_comparability_sections(record):
    """Locate change disclosures, without deciding that reports are comparable."""
    major=r'[一二三四五六七八九十]+、'
    return dict(consolidation_changes=_bounded_note_section(record,
        major+r'合并范围的(?:变动|变更)',major),
        accounting_changes=_bounded_note_section(record,
        r'\d+、(?:重要)?会计政策[和及]会计估计变更',r'(?:\d+、|'+major+r')'),
        comparability_approved=False)


def original_amount_cells(task, record):
    """Exact supplemental cells, before reading and applicability decisions."""
    if not product_selection_enabled(task) or record.get('kind') != 'filing':
        return []
    if record not in proven_disclosures(task, record.get('directory', {}).get('issuer')):
        raise FinancialDataUnavailable('财务数据产品缺少本任务实际原件')
    result = []
    periods = list(_cfo_contexts(task, record))
    def scoped_unit(proof):
        return dict(part=proof['unit_part'], quote=proof['unit_quote'])
    def append(field, period, position, value, raw_token, unit, citation, unit_basis, period_basis,
               *, period_literal=None, statement_basis=None):
        result.append(dict(key=field+'/'+period+'/'+str(position), field=field, period=period,
            period_literal=period_literal or (compact(period_basis['quote']) if DURATION.fullmatch(period_basis['quote'])
                else '截至'+period[:4]+'年6月30日止6个月期间'),
            value=value, raw_token=raw_token, unit=unit, citation=citation,
            unit_basis=unit_basis, period_basis=dict(part=period_basis['part'], quote=period_basis['quote'])))
        if statement_basis is not None:result[-1]['statement_basis']=copy.deepcopy(statement_basis)
    if periods:
        for value in capital_expenditure(task, record, periods)['values']:
            append('capital_expenditure', value['period'], 0, value['value'], value['raw_token'],
                   value['unit'], value['citation'], scoped_unit(value['unit_basis']), value['period_basis'])
        try:
            bridge = cash_reconciliation(task, record, periods)
        except FinancialDataUnavailable:
            bridge = None
        if bridge and bridge['data_checks_passed']:
            for index, row in enumerate(bridge['rows']):
                for period, value in row['values'].items():
                    basis = next(item for item in bridge['period_citations']
                                 if DURATION.fullmatch(item['quote'])[1]+'0630' == period)
                    append('cash_reconciliation', period, index, value, row['raw_tokens'][period],
                           bridge['unit'], row['citation'], scoped_unit(bridge['unit_basis']), basis)
    try:
        nonrecurring = nonrecurring_items(record)
    except FinancialDataUnavailable:
        nonrecurring = None
    if nonrecurring and nonrecurring['data_checks_passed']:
        for index, row in enumerate(nonrecurring['rows']):
            append('nonrecurring', nonrecurring['period'], index, row['value'], row['raw_token'],
                   nonrecurring['unit'], row['citation'], nonrecurring['unit_basis'], nonrecurring['period_basis'])
    if statement_details_enabled(task):
        for field,operation in (('working_capital',working_capital_balances),('profit_components',profit_components)):
            try:details=operation(record)
            except FinancialDataUnavailable:continue
            for row in details['values']:
                append(field,row['period'],row['metric'],row['value'],row['raw_token'],row['unit'],
                    row['citation'],row['unit_basis'],row['period_basis'],period_literal=row['period_literal'],
                    statement_basis=row['statement_basis'])
    return result


def original_amount_tables(task, record, view, *, offset=0):
    """Expose only cells whose row, unit and period have actually been read."""
    from .financial_sources import original_line_index
    cells = original_amount_cells(task, record)
    exposed = {}
    for part in view.get('parts', []):
        exposed.setdefault(part['locator'], []).append(part['text'])
    lines = {}
    for line in original_line_index(view):
        lines.setdefault((line['part'], line['text'].rstrip('\n')), []).append(line['index'])
    groups, unread = {}, []
    for cell in cells:
        proofs = [cell['citation'], cell['unit_basis'], cell['period_basis']]
        if cell.get('statement_basis'):proofs.append(cell['statement_basis'])
        absent = [proof['part'] for proof in proofs
                  if not any(proof['quote'] in text for text in exposed.get(proof['part'], []))]
        if absent:
            unread.append(dict(field=cell['field'], period=cell['period'], required_parts=list(dict.fromkeys(absent))))
            continue
        citation = cell['citation']; addresses = lines.get((citation['part'], citation['quote']), [])
        if len(addresses) != 1:
            continue
        group = groups.setdefault((cell['field'], citation['part']), dict(version=2,
            reference='amount_table_'+str(offset+len(groups)+1), part=citation['part'],
            header_part=cell['period_basis']['part'], header_quote=cell['period_basis']['quote'],
            unit_part=cell['unit_basis']['part'], unit_quote=cell['unit_basis']['quote'],
            unit=cell['unit'], columns=[], rows={}))
        reference = 'line_'+str(addresses[0]); column_reference = 'column_'+cell['period']
        row = group['rows'].setdefault(reference, dict(text=citation['quote'], line_index=addresses[0], cells={}))
        row['cells'][column_reference] = copy.deepcopy(cell)
        column = next((column for column in group['columns'] if column['reference'] == column_reference), None)
        if column is None:
            column = dict(reference=column_reference, column_index=len(group['columns'])+1,
                label=cell['period_literal'], period=cell['period_literal'], mode='disclosure_data',
                report_part=cell['period_basis']['part'], report_quote=cell['period_basis']['quote'],
                period_basis=copy.deepcopy(cell['period_basis']), row_references=[])
            group['columns'].append(column)
        column['row_references'].append(reference)
    return dict(tables=list(groups.values()), unread_cells=unread)


def original_amount_value(cell):
    decimal = monetary_cell(cell['raw_token'])
    value = int(decimal) if decimal == decimal.to_integral_value() else float(decimal)
    if Decimal(str(value)) != decimal or decimal != Decimal(cell['value']):
        raise FinancialDataUnavailable('财务明细原值不能无损表示，未舍入')
    return dict(raw_token=cell['raw_token'], raw_value=value, value=value,
                raw_unit=cell['unit'], unit=cell['unit'], conversion_factor=1)


def financial_reading_groups(task, record):
    """Keep a known financial section with its literal units and periods.

    This only selects already acquired original pages inside the reader's
    existing shared budget. No acquisition, evidence adoption or full-report
    reading is implied.
    """
    if not product_selection_enabled(task) or record.get('kind') != 'filing':
        return []
    from .contracts import FINANCIAL_PERIOD_METHODS
    methods = {method for question in task.get('questions', []) for method in question.get('method_needs', [])}
    if not methods.intersection(FINANCIAL_PERIOD_METHODS):
        return []
    cells = original_amount_cells(task, record)
    groups = {}
    for cell in cells:
        parts = groups.setdefault(cell['field'], [])
        proofs=[cell['citation'],cell['unit_basis'],cell['period_basis']]
        if cell.get('statement_basis'):proofs.append(cell['statement_basis'])
        for proof in proofs:
            if proof['part'] not in parts:
                parts.append(proof['part'])
    try:
        context = disclosure_context(record)
    except FinancialDataUnavailable:
        context = None
    if context:
        citations = context['issuer_basis']+[context['period_basis']]
        citations += [item['citation'] for key in ('currencies','accounting_standards','statement_units')
                      for item in context[key]]
        citations += [citation for values in context['consolidated_statement_titles'].values() for citation in values]
        groups['report_context'] = list(dict.fromkeys(citation['part'] for citation in citations))
    if statement_details_enabled(task):
        sections=report_comparability_sections(record)
        for name in ('consolidation_changes','accounting_changes'):
            section=sections[name]
            if section['complete_section']:
                groups[name]=list(dict.fromkeys(fragment['part'] for fragment in section['fragments']))
    return [dict(field=field,parts=parts) for field,parts in groups.items()]


def duration_observation_applies(item, record):
    """A labelled six-month figure may use its explicit ending observation.

    This does not turn an endpoint into a one-day fact or publication time.
    The period and its source address must already be fully explicit.
    """
    period = compact(str(item.get('period', '')))
    match = DURATION.fullmatch(period)
    if not match or item.get('observation_date') != match[1]+'-06-30':
        return False
    basis = item.get('reporting_period_basis') or item.get('calculation_context_basis', {}).get('period')
    if (not isinstance(basis, dict) or basis.get('kind') != 'material'
            or basis.get('artifact_id') != record.get('id')
            or basis.get('snapshot_hash') != record.get('snapshot_hash', record.get('hash'))):
        return False
    parts = record['snapshot']['material']['parts']
    quote = basis.get('quote')
    return bool(isinstance(quote, str) and period in compact(quote) and any(
        part['locator'] == basis.get('part') and quote in part['text'] for part in parts))


def validate_original_amount(item, record, task):
    """Recompute the selected cell; the short address grants no approval."""
    selector = item.get('record_locator', {}).get('financial_amount_column')
    if (not product_selection_enabled(task) or not isinstance(selector, dict)
            or set(selector) != {'version', 'cell_key', 'cell_binding', 'row_quote', 'header_part', 'header_quote',
                                'unit_quote', 'period_basis_part', 'period_basis_quote'}
            or type(selector['version']) is not int
            or selector['version'] != 2 or item.get('calculation_context') is not None):
        raise FinancialDataUnavailable('财务明细数据选择不属于本任务登记版本')
    choices = [cell for cell in original_amount_cells(task, record) if cell['key'] == selector['cell_key']]
    if len(choices) != 1 or fingerprint(choices[0]) != selector['cell_binding']:
        raise FinancialDataUnavailable('财务明细数据地址或原文绑定变化')
    cell = choices[0]
    expected = original_amount_value(cell)
    locator = item['record_locator']; basis = item.get('reporting_period_basis', {})
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}',cell['period_literal']):
        if basis.get('version')!=4 or item.get('observation_date')!=cell['period_literal']:
            raise FinancialDataUnavailable('余额日期须对应所选原列，不借其他日期或报告期间')
    if (expected is None or any(item.get(key) != value for key, value in expected.items())
            or item.get('period') != cell['period_literal'] or item.get('text') != cell['citation']['quote']
            or selector['row_quote'] != cell['citation']['quote']
            or selector['header_part'] != cell['period_basis']['part']
            or selector['header_quote'] != cell['period_basis']['quote']
            or selector['unit_quote'] != cell['unit_basis']['quote']
            or selector['period_basis_part'] != cell['period_basis']['part']
            or selector['period_basis_quote'] != cell['period_basis']['quote']
            or locator.get('part') != cell['citation']['part'] or locator.get('unit_part') != cell['unit_basis']['part']
            or basis.get('part') != cell['period_basis']['part'] or basis.get('quote') != cell['period_basis']['quote']):
        raise FinancialDataUnavailable('财务明细金额、期间或定位与已选原件单元格不一致')
