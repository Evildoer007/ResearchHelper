"""Explicit monthly source goals, separate from financial approval.

The Agent declares a document series and temporal relation before acquisition.
Only a registered series can resolve that relation; no need-text keyword,
publication date or guessed URL supplies an observation month. Other research
retains its ordinary, possibly partial, source and evidence paths.
"""
import copy
import re

from .contracts import ContractError, fingerprint

POLICY = 'source_period_targets_version'
FIELD = 'evidence_period_targets'
FAMILY = 'nbs_pmi_release'
PRIMARY_TITLE = re.compile(r'(\d{4})年(0?[1-9]|1[0-2])月中国采购经理指数运行情况\Z')
MONTH = re.compile(r'\d{4}-(?:0[1-9]|1[0-2])\Z')
GUIDANCE = (
    '不需要时省略period_target，不把所有研究变成月度原件任务。仅当前版本研究需要统计局PMI月度运行情况时，'
    '对相应数值需求显式登记family=nbs_pmi_release及relation=latest、previous或exact。'
    'latest/previous的period为null，exact的period为明确YYYY-MM；其他来源、元信息、历史时点及未登记系列不登记该字段。'
    '本期与上期分别登记，不能把另一篇同月解读当上期原件。latest仅定位实际已读目录中最近的完整观测月，'
    '不是全站最新性或历史首发保证；previous指该月的相邻上月，不按已取得文件的排序取上期。'
    '必须保留原研究需求，不因本期数据更容易获取而改写上期目标；原件可读也不代表数值已采用。'
)


def enabled(task):
    version = task.get(POLICY)
    if version is None: return False
    if type(version) is not int or version != 1:
        raise ContractError('来源观测期目标版本未登记')
    return task.get('research_mode') == 'current' and task.get('intent') != '原件内容核对'


def normalized(question):
    targets = question.get(FIELD)
    if targets is None: return {}
    if not isinstance(targets, dict) or not set(targets).issubset(question.get('evidence_needs', [])):
        raise ContractError('观测期目标须逐字绑定已登记需求')
    for text, target in targets.items():
        if (not isinstance(target, dict) or set(target) != {'family', 'relation', 'period'}
                or target['family'] != FAMILY or target['relation'] not in ('latest', 'previous', 'exact')):
            raise ContractError('观测期目标的系列或期间关系未登记')
        if (target['relation'] == 'exact' and (not isinstance(target['period'], str)
                or not MONTH.fullmatch(target['period']))
                or target['relation'] != 'exact' and target['period'] is not None):
            raise ContractError('明确月份须为YYYY-MM，相对期间不能预填猜测月份')
        spec = question.get('evidence_need_constraints', {}).get(text, {})
        if (spec.get('value_kind'), spec.get('unit_dimension'), spec.get('period_kind')) != ('number', 'percentage', 'month'):
            raise ContractError('当前登记系列的观测期目标只适用于月度百分比数值，不替代来源标注或文字需求')
    return copy.deepcopy(targets)


def validate_plan(task, questions):
    active = enabled(task)
    for question in questions:
        targets = normalized(question)
        if targets and not active:
            raise ContractError('当前任务不支持该来源观测期目标，不能升级旧任务或扩大历史适用性')
        if any(target['relation'] == 'exact' and target['period'] >= task['cutoff'][:7]
               for target in targets.values()):
            raise ContractError('月度目标须为截止前完整观测月，不把未来或未完成月份当作已披露事实')
    by_id = {question['id']: question for question in questions}
    for old in task.get('questions', []):
        previous = normalized(old)
        current = normalized(by_id[old['id']]) if old['id'] in by_id else {}
        if any(current.get(text) != target for text, target in previous.items()):
            raise ContractError('计划修订不能删除或改写既有观测期目标')


def _period(title):
    match = PRIMARY_TITLE.fullmatch(title or '')
    return f'{match[1]}-{int(match[2]):02d}' if match else None


def _previous(period):
    year, month = map(int, period.split('-'))
    return f'{year - (month == 1):04d}-{12 if month == 1 else month - 1:02d}'


def resolve(task):
    """Resolve declared relationships from verified catalogue bytes only.

    Returned months are navigation metadata, not verified financial periods.
    A later catalogue may reveal a newer month; evidence gates re-resolve the
    same declarations rather than retaining a stale accidental anchor.
    """
    if not enabled(task): return []
    declarations = [(question, text, target) for question in task['questions']
                    for text, target in normalized(question).items()]
    if not declarations: return []
    from .official_sources import release_choices, selection_references
    choices = selection_references(task) or release_choices(task)
    catalogs = {record['id'] for record in task.get('source_records', [])
                if record.get('source') == 'official' and record.get('operation') == 'release_catalog'
                and record.get('parameters', {}).get('channel') == 'nbs_release'}
    primary = [dict(choice, observation_period=_period(choice['title'])) for choice in choices
               if choice['catalog_id'] in catalogs and _period(choice['title'])
               and _period(choice['title']) < task['cutoff'][:7]]
    anchor = max((choice['observation_period'] for choice in primary), default=None)
    result = []
    for question, text, target in declarations:
        period = (target['period'] if target['relation'] == 'exact' else
                  _previous(anchor) if anchor and target['relation'] == 'previous' else anchor)
        candidates = [copy.deepcopy(choice) for choice in primary if choice['observation_period'] == period]
        result.append(dict(question_id=question['id'], need_id=fingerprint(text), text=text,
            required=question.get('required', True), target=target, resolved_period=period,
            anchor_period=anchor if target['relation'] != 'exact' else None, candidates=candidates))
    return result


def with_coverage(task, views, coverage):
    """Do not let a same-month commentary discharge another month's goal."""
    goals = resolve(task)
    if not goals: return coverage
    from .evidence import current_original_reference
    from .official_sources import catalogue_pagination
    readable = {view['id']: view for view in views if view.get('kind') == 'material'}
    originals = []
    for record in task.get('source_records', []):
        if record['id'] not in readable or record.get('source') != 'official' or record.get('operation') != 'release_original':
            continue
        origin = record.get('snapshot', {}).get('official_origin', {})
        period = _period(origin.get('title'))
        if not period or current_original_reference(task, record) is None: continue
        # Directory title alone is not a readable original. This registered
        # series also requires its own literal page heading in the read view.
        if not any(part.get('text') == origin['title'] + ' - 国家统计局'
                   for part in readable[record['id']].get('parts', [])): continue
        originals.append(dict(source_id=record['id'], observation_period=period,
                              question_ids=record.get('question_ids', [])))
    rows = []
    for goal in goals:
        available = [item['source_id'] for item in originals
                     if item['observation_period'] == goal['resolved_period']
                     and goal['question_id'] in item['question_ids']]
        rows.append(dict(goal, source_path_ready=bool(available), source_ids=available,
                         financial_approval=False, numeric_values_approved=False))
    pending = [row for row in rows if row['required'] and not row['source_path_ready']]
    result = dict(coverage, ready=coverage['ready'] and not pending,
        source_period_targets_version=1, source_period_targets=rows,
        period_catalogue_scope='仅实际取得目录，未保证全站最新或完整',
        period_catalogue_pagination=catalogue_pagination(task),
        period_navigation_rule='按已解析目标月份选已取得目录的原文引用；没有候选时按需查询登记目录页，'
            '不猜URL、不把同月解读当上期、不自动遍历、不增加预算；缺项保留且不批准金融事实。')
    result['pending_original_question_ids'] = list(dict.fromkeys(
        coverage.get('pending_original_question_ids', []) + [row['question_id'] for row in pending]))
    result['required_original_question_ids'] = list(dict.fromkeys(
        coverage.get('required_original_question_ids', []) + [row['question_id'] for row in rows if row['required']]))
    return result


def validate_links(task, item, mapping):
    for goal in resolve(task):
        selected = mapping.get(goal['question_id'], [])
        if not {goal['need_id'], goal['question_id'] + ':' + goal['need_id']}.intersection(selected): continue
        if goal['resolved_period'] is None or item.get('period') != goal['resolved_period']:
            raise ContractError('所选数值的观测月份不满足本条登记目标，不能用本期代替上期或猜测未定位月份')


def selection_period(task, question_id, text):
    """None means no declared goal; an unresolved goal is deliberately empty."""
    return next((row for row in resolve(task) if row['question_id'] == question_id and row['text'] == text), None)


def wire_schema():
    from .output_schema import object_schema
    relation = object_schema(dict(family={'type': 'string', 'enum': [FAMILY]},
        relation={'type': 'string', 'enum': ['latest', 'previous']}, period={'type': 'null'}))
    exact = object_schema(dict(family={'type': 'string', 'enum': [FAMILY]},
        relation={'type': 'string', 'enum': ['exact']}, period={'type': 'string', 'pattern': r'^\d{4}-(0[1-9]|1[0-2])$'}))
    return dict(anyOf=[relation, exact])
