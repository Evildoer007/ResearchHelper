"""Registered NBS manufacturing-PMI level identities, not arbitrary numbers.

This adapter covers explicit narrative level statements only. Tables, changes,
weights and other releases retain their separate source contracts. Unmapped
material remains available; it is never filled with a convenient nearby value.
"""
import re
from decimal import Decimal

from .contracts import ContractError, fingerprint

POLICY = 'statistical_metric_identity_version'
METRICS = {
    'headline': ('制造业采购经理指数（PMI）', '制造业采购经理指数(PMI)', '制造业采购经理指数', '制造业PMI'),
    'production': ('制造业生产指数', '生产指数'),
    'new_orders': ('制造业新订单指数', '新订单指数'),
    'employment': ('制造业从业人员指数', '从业人员指数'),
    'raw_inventory': ('制造业原材料库存指数', '原材料库存指数'),
    'delivery': ('制造业供应商配送时间指数', '供应商配送时间指数'),
    'input_prices': ('主要原材料购进价格指数', '购进价格指数'),
    'output_prices': ('出厂价格指数',),
    'expectations': ('生产经营活动预期指数',),
    'export_orders': ('新出口订单指数',),
    'backlog': ('在手订单指数', '积压订单指数'),
    'finished_inventory': ('产成品库存指数',),
}
NON_LEVEL = re.compile(r'环比|变动|变化|增减|升降|涨跌|权重|权数|贡献|上升幅度|下降幅度')
SECTION = re.compile(r'^[一二三四五六七八九十]+[、.．]\s*中国(非制造业|制造业|综合PMI)')


def enabled(task):
    value = task.get(POLICY)
    if value is None: return False
    if type(value) is not int or value != 1:
        raise ContractError('统计指标身份版本未登记')
    return task.get('research_mode') == 'current' and task.get('intent') != '原件内容核对'


def metrics(text):
    found = {key for key, names in METRICS.items() if any(name in text for name in names)}
    if found - {'headline'} and not re.search(r'总体指数|总指数|采购经理指数', text):
        found.discard('headline')  # The prefix 制造业PMI is not another metric.
    return found


def narrative_rule(task, record):
    if not enabled(task) or record.get('source') != 'official' or record.get('operation') != 'release_original':
        return None
    origin = record.get('snapshot', {}).get('official_origin', {})
    title = re.fullmatch(r'(\d{4})年(\d{1,2})月中国采购经理指数运行情况', origin.get('title', ''))
    if title is None: return None
    from .official_sources import article_url
    article_url(origin.get('url'), channel='nbs_release')
    return dict(adapter='nbs_manufacturing_pmi_narrative_levels_v1', period=f'{title[1]}-{int(title[2]):02d}',
        rule='本篇制造业正文中明确写出的指标为某数值百分数才是该指标水平；合成权重、环比变动和其他指标不能贴成本项。表格或未明确匹配的文字保留原件，不用邻近数字补齐。',
        source_or_applicability_approved=False)


def level_identity(item, task, coverage):
    """Identify an explicitly linked narrative level after its existing checks.

    This is not source or financial approval. The report reader may use the
    registered identity only for an already adopted, hash-bound observation.
    """
    if not enabled(task) or 'value' not in item or item.get('source') != 'official': return
    artifact = item.get('artifact', {})
    rule = narrative_rule(task, artifact)
    if rule is None: return
    wanted, non_level_need = set(), False
    for question in task['questions']:
        links = coverage.get(question['id'], [])
        for text in question.get('evidence_needs', []):
            ident = fingerprint(text)
            if not {ident, question['id'] + ':' + ident}.intersection(links): continue
            selected = metrics(text)
            if selected and not NON_LEVEL.search(text): wanted.update(selected)
            elif selected: non_level_need = True
    named = metrics(str(item.get('object', '')))
    if not wanted:
        if non_level_need: return
        if NON_LEVEL.search(str(item.get('object', ''))): return
        wanted = named
    if not wanted: return  # No claim of coverage for an unregistered identity.
    if named and not named.issubset(wanted):
        raise ContractError('统计指标名称与关联需求不一致，不用别的指标覆盖本项')
    if len(wanted) != 1:
        raise ContractError('不同统计指标须分别定位，不将一个原值当成多个指标')
    if (item.get('unit') != '%' or item.get('raw_unit') != '%'
            or type(item.get('conversion_factor')) not in (int, float)
            or item.get('conversion_factor') != 1 or item.get('nature') != '已实现事实'):
        raise ContractError('PMI指数水平须保留原文百分数；变动、权重和预测不作水平值')
    period = rule['period']
    if item.get('period') != period or item.get('observation_period') != dict(frequency='month', value=period):
        raise ContractError('PMI叙述原值只对应本篇明确月份，不借另一月份的日期')
    parts = artifact['snapshot'].get('parts', [])
    scope, selected_part = None, None
    for part in parts:
        heading = SECTION.match(part['text'].strip())
        if heading: scope = heading[1]
        if re.match(r'^附注|^注[：:]', part['text'].strip()): scope = 'method'
        if part['locator'] == item.get('record_locator', {}).get('part'):
            selected_part = part
            break
    if scope != '制造业' or selected_part is None:
        raise ContractError('PMI水平须定位制造业正文；非制造业、方法权重和未定位材料不能代替')
    names = METRICS[next(iter(wanted))]
    pattern = re.compile(r'(?<![\u4e00-\u9fffA-Za-z])(?:' + '|'.join(re.escape(name) for name in names)
                         + r')\s*为\s*([+-]?\d+(?:\.\d+)?)\s*%')
    text = item.get('text', '')
    candidates = [match[1] for match in pattern.finditer(text)]
    raw_token = item.get('raw_token')
    if (not isinstance(raw_token, str) or raw_token not in candidates
            or type(item.get('raw_value')) not in (int, float) or type(item.get('value')) not in (int, float)
            or Decimal(raw_token) != Decimal(str(item.get('raw_value')))
            or Decimal(raw_token) != Decimal(str(item.get('value')))):
        raise ContractError('所选数字不是同一原文中该指标的水平值，不能借总体、变动或权重贴名')
    return dict(adapter=rule['adapter'], metric=next(iter(wanted)), period=period)


def validate_level(item, task, coverage):
    """Reject a true source number whose submitted metric identity is false."""
    level_identity(item, task, coverage)
