"""Explicit review of frozen judgments, separate from price or fact changes.

Analysts select the correspondence and assess each dimension. This module
checks identities, frozen conditions, references, timing and numeric claims;
it never infers an investment outcome from a changed record or an ordinal ID.
"""
import copy
import re

from .contracts import ContractError, context, fingerprint, market_date, timepoint, validate_evidence
from .numeric_integrity import bind_numeric_statement

VERSION = 1
DIMENSIONS = {'facts': '事实变化', 'mechanism': '机制复核', 'expectations': '预期差', 'price': '价格表现'}
STATES = {
    'facts': ('依据有变化', '留存依据未变', '证据不足', '不适用'),
    'mechanism': ('支持', '反对', '证据不足', '未到验证期', '不适用'),
    'expectations': ('支持', '反对', '证据不足', '未到验证期', '不适用'),
    'price': ('已观察', '证据不足', '未到验证期', '不适用'),
}
ASSESSMENTS = ('维持', '调整', '撤回', '证据不足', '不适用')
NEUTRAL = {'证据不足', '未到验证期', '不适用'}
EXPECTATION_KINDS = {'第三方预测': '第三方估计', '管理层指引': '管理层展望'}
PRICE_METHODS = {'observed_price_return_v1', 'observed_index_change_v1'}
FACT_FIELDS = ('object', 'source', 'text', 'value', 'raw_value', 'raw_unit', 'unit',
               'period', 'observation_date', 'nature', 'calculation_context', 'published_at')


def enabled(task):
    version = task.get('judgment_review_contract')
    if version is None:
        return False
    if type(version) is not int or version != VERSION:
        raise ContractError('判断复核契约未登记')
    return True


def baseline(report):
    from .reports import check_content
    content = report['content']
    check_content(content)
    if fingerprint(content) != report['digest']:
        raise ContractError('原判断交付哈希不一致，不能复核')
    facts = {item['id']: item for item in content['evidence']}
    anchors = []
    for thesis in content['theses']:
        conditions = [rule for rule in content['tracking'] if rule.get('thesis_id') == thesis['id']]
        refs = list(dict.fromkeys(thesis.get('support', []) + thesis.get('counter', [])))
        anchors.append(dict(id=thesis['id'], title=thesis['title'], judgment=thesis['judgment'],
            thesis_hash=fingerprint(thesis), question_ids=copy.deepcopy(thesis['question_ids']),
            investment_meaning=thesis.get('investment_meaning', ''), limitation=thesis.get('limitation', ''),
            conditions=copy.deepcopy(conditions), conditions_hash=fingerprint(conditions),
            prior_evidence_ids=refs, prior_evidence=[{key: copy.deepcopy(facts[ref][key])
                for key in FACT_FIELDS if key in facts[ref]} for ref in refs if ref in facts]))
    return dict(task_id=content['task_id'], prior_version=content['research_version'],
        prior_digest=report['digest'], prior_cutoff=content['cutoff'], anchors=anchors)


def _baseline(task):
    value, parent = task.get('judgment_review_baseline'), task.get('update_from', {})
    if (not isinstance(value, dict) or value.get('task_id') != task['id']
            or value.get('prior_version') != parent.get('version')
            or value.get('prior_digest') != parent.get('digest')
            or value.get('prior_version') != task['version'] - 1
            or not isinstance(value.get('anchors'), list)
            or len({item['id'] for item in value['anchors']}) != len(value['anchors'])
            or timepoint(value['prior_cutoff']) >= timepoint(task['cutoff'])):
        raise ContractError('原判断基准不属于本任务的冻结前版')
    return value


def contract(task, selected=None):
    if not enabled(task):
        return None
    value = _baseline(task)
    selected = set(selected if selected is not None else (q['id'] for q in task['questions']))
    return dict(version=VERSION, prior_version=value['prior_version'], prior_digest=value['prior_digest'],
        anchors=[copy.deepcopy(item) for item in value['anchors'] if set(item['question_ids']).issubset(selected)],
        assessments=list(ASSESSMENTS),
        dimensions={key: dict(label=DIMENSIONS[key], states=list(states)) for key, states in STATES.items()},
        current_thesis_indices='本次theses提案列表从0开始的位置，不是跨版本论点编号',
        statement='四维分别提供state、answer或numeric_segments.answer、refs及calculation_refs。',
        expectation_kinds=copy.deepcopy(EXPECTATION_KINDS),
        expectation_rule='支持或反对须显式区分expected_refs和observed_refs，分别是对应预测性质与已实现事实。'
                         '两者首发时间须已核验且预期先于结果；第三方预测不自动等于全市场共识。',
        price_rule='已观察须引用本版登记的价格区间计算及其全部输入，并含前版截止日之后的实际观测。'
                   '未复权价格变化不是投资总收益、超额收益或事件因果。',
        rule='复制原判断id、哈希、版本和条件哈希，显式选择本版提案位置。旧条件不改写，旧依据不作本版引用。'
             '留存依据未变仅比较本维度已引用依据，不证明全部事实或世界状态未变。'
             '原窗口没有可核验的未来日期时，不写未到验证期，保留证据不足。')


def validate_shape(items):
    if not isinstance(items, list) or len(items) > 50:
        raise ContractError('判断复核须为不超过50项的列表')
    required = {'prior_thesis_id', 'prior_thesis_hash', 'prior_version', 'conditions_hash',
                'assessment', 'current_thesis_indices', 'dimensions'}
    seen = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != required:
            raise ContractError('判断复核字段未登记')
        ident = item['prior_thesis_id']
        if (not isinstance(ident, str) or not ident or ident in seen
                or type(item['prior_version']) is not int or item['prior_version'] < 1
                or any(not isinstance(item[key], str) or not re.fullmatch('[0-9a-f]{64}', item[key])
                       for key in ('prior_thesis_hash', 'conditions_hash'))
                or item['assessment'] not in ASSESSMENTS):
            raise ContractError('原判断身份、条件或复核状态无效或重复')
        seen.add(ident)
        indices = item['current_thesis_indices']
        if (not isinstance(indices, list) or any(type(index) is not int or not 0 <= index < 50 for index in indices)
                or len(indices) != len(set(indices))):
            raise ContractError('本版论点提案位置无效')
        dimensions = item['dimensions']
        if not isinstance(dimensions, dict) or set(dimensions) != set(DIMENSIONS):
            raise ContractError('事实、机制、预期差和价格须分别复核')
        for key, dimension in dimensions.items():
            if (not isinstance(dimension, dict) or set(dimension) - {'state', 'answer', 'numeric_segments',
                    'refs', 'calculation_refs', 'expected_refs', 'observed_refs', 'comparison_kind'}
                    or dimension.get('state') not in STATES[key]
                    or not {'refs', 'calculation_refs'}.issubset(dimension)
                    or 'answer' in dimension and not isinstance(dimension['answer'], str)):
                raise ContractError('复核维度状态或字段无效')
            for field in ('refs', 'calculation_refs', 'expected_refs', 'observed_refs'):
                refs = dimension.get(field, [])
                if (not isinstance(refs, list) or len(refs) > 1000
                        or any(not isinstance(ref, str) or not ref for ref in refs)
                        or len(refs) != len(set(refs))):
                    raise ContractError('复核引用须为不重复的标识列表')
            if key != 'expectations' and any(field in dimension for field in
                    ('expected_refs', 'observed_refs', 'comparison_kind')):
                raise ContractError('预期对照字段不能混入其他维度')
            if 'comparison_kind' in dimension and dimension['comparison_kind'] not in EXPECTATION_KINDS:
                raise ContractError('预期对照口径未登记')
            if not dimension.get('answer', '').strip() and not dimension.get('numeric_segments'):
                raise ContractError('复核须说明依据或尚待验证事项')


def _references(item, kind):
    return sorted({ref for dimension in item['dimensions'].values() for ref in dimension.get(kind, [])})


def collect(task, result, selected, *, filter_scope=False, thesis_hashes=None):
    """Replace one complete analysis-node proposal, without adopting a verdict."""
    items = result.get('judgment_reviews', [])
    validate_shape(items)
    if not enabled(task):
        if items:
            raise ContractError('本任务没有主动复核契约')
        return
    anchors = {item['id'] for item in contract(task, selected)['anchors']}
    if filter_scope:
        items = [item for item in items if item['prior_thesis_id'] in anchors]
    if any(item['prior_thesis_id'] not in anchors for item in items):
        raise ContractError('复核原判断不属于本轮问题范围')
    evidence = {item['id']: item for item in task['evidence']}
    calculations = {item['id']: item for item in task['calculations']}
    refs = {ref for item in items for ref in _references(item, 'refs')}
    calc_refs = {ref for item in items for ref in _references(item, 'calculation_refs')}
    if thesis_hashes is None:
        thesis_hashes = [fingerprint(item) for item in result.get('theses', [])]
    if not isinstance(thesis_hashes, list) or any(not isinstance(value, str) or not re.fullmatch(
            '[0-9a-f]{64}', value) for value in thesis_hashes):
        raise ContractError('论点提案身份须绑定实际分析提交，不从合并后的编号推定')
    bundle = dict(binding=context(task), selected=sorted(selected), reviews=copy.deepcopy(items),
        thesis_hashes=copy.deepcopy(thesis_hashes),
        evidence={ref: fingerprint(evidence.get(ref)) for ref in refs},
        calculations={ref: fingerprint(calculations.get(ref)) for ref in calc_refs})
    previous = [entry for entry in task.get('judgment_review_proposals', []) if entry['selected'] != sorted(selected)]
    task['judgment_review_proposals'] = [*previous, bundle]


def _not_due(task, anchor):
    dates = [value for rule in anchor['conditions'] for value in re.findall(r'\d{4}-\d{2}-\d{2}', rule['window'])]
    try:
        for value in dates:
            timepoint(value)
    except ContractError:
        return False
    return bool(dates) and market_date(task['cutoff']) < min(dates)


def _dimension(task, key, proposal, anchor, evidence, calculations):
    refs, calc_refs = proposal['refs'], proposal['calculation_refs']
    related = set(anchor['question_ids'])
    for ref in refs:
        if ref not in evidence or not related.intersection(evidence[ref].get('question_ids', [])):
            raise ContractError('复核不能引用旧版、其他任务或无关问题的依据')
        validate_evidence(evidence[ref], task)
    for ref in calc_refs:
        if (ref not in calculations or calculations[ref].get('task_id') != task['id']
                or calculations[ref].get('research_version') != task['version']
                or not set(calculations[ref]['input_refs']).issubset(refs)
                or not related.intersection(calculations[ref].get('question_ids', []))):
            raise ContractError('复核计算及其全部输入须属于本版问题和引用')
    state = proposal['state']
    if state not in NEUTRAL and not refs:
        raise ContractError('确定性复核缺少本版依据')
    if state == '未到验证期' and not _not_due(task, anchor):
        raise ContractError('未知、无效或已开始的观察窗口不能写成尚未到期')
    if key == 'facts' and state not in NEUTRAL:
        prior = {fingerprint(item) for item in anchor['prior_evidence']}
        current = {fingerprint({field: evidence[ref][field] for field in FACT_FIELDS if field in evidence[ref]}) for ref in refs}
        if (state == '留存依据未变') != current.issubset(prior):
            raise ContractError('留存事实比较与本版依据不一致，不能只比较编号或新核验回执')
    expected, observed = proposal.get('expected_refs', []), proposal.get('observed_refs', [])
    if key == 'expectations' and state in ('支持', '反对'):
        nature = EXPECTATION_KINDS.get(proposal.get('comparison_kind'))
        if (nature is None or not expected or not observed or set(expected).intersection(observed)
                or not set(expected + observed).issubset(refs)
                or any(evidence[ref]['nature'] != nature for ref in expected)
                or any(evidence[ref]['nature'] != '已实现事实' for ref in observed)):
            raise ContractError('预期差须分别引用登记性质的预测和已实现事实，不把自有假设当市场预期')
        if any(not evidence[ref].get('published_at') for ref in expected + observed):
            raise ContractError('预期对照须独立核验首发先后，当前取得不能证明事前预期')
        if max(timepoint(evidence[ref]['published_at']) for ref in expected) >= min(
                timepoint(evidence[ref]['published_at']) for ref in observed):
            raise ContractError('预期对照的预测没有早于结果首次公开，不能使用事后预期')
    if key == 'price' and state == '已观察':
        price = [calculations[ref] for ref in calc_refs if calculations[ref]['method'] in PRICE_METHODS]
        prior_day = market_date(_baseline(task)['prior_cutoff'])
        dates = [evidence[ref].get('observation_date') or evidence[ref].get('calculation_context', {}).get('observation_date')
                 for calculation in price for ref in calculation['input_refs']]
        if not price or not any(isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value)
                and prior_day < value <= market_date(task['cutoff']) for value in dates):
            raise ContractError('价格表现缺少前版之后的本次已观测区间计算')
    bound = bind_numeric_statement(task, dict(proposal, question_ids=anchor['question_ids']), ['answer'], refs, calc_refs)
    return dict(copy.deepcopy(proposal), answer=bound['fields']['answer'], numeric_integrity=bound['record'])


def _review(task, item, base, anchors, accepted, evidence, calculations, hashes, binding):
    ident = item['prior_thesis_id']
    anchor = anchors.get(ident)
    if (anchor is None or item['prior_version'] != base['prior_version']
            or item['prior_thesis_hash'] != anchor['thesis_hash'] or item['conditions_hash'] != anchor['conditions_hash']):
        raise ContractError('原判断或发布条件不匹配，不按相同编号自动对应')
    current = []
    for index in item['current_thesis_indices']:
        candidates = accepted.get(hashes[index], []) if index < len(hashes) else []
        if len(candidates) != 1:
            raise ContractError('复核关联的本版论点未获接受或提案身份不唯一')
        thesis = candidates[0]
        if not set(thesis['question_ids']).issubset(anchor['question_ids']):
            raise ContractError('复核关联了另一问题的论点')
        current.append(dict(id=thesis['id'], title=thesis['title'], judgment=thesis['judgment'], hash=fingerprint(thesis)))
    assessment = item['assessment']
    if assessment in ('维持', '调整') and (not current or all(thesis['judgment'] == '证据不足' for thesis in current)):
        raise ContractError('维持或调整判断须关联本版已接受的研究论点')
    if assessment == '维持' and any(thesis['judgment'] != anchor['judgment'] for thesis in current):
        raise ContractError('本版判断方向已改变，不能标为维持')
    dimensions = {key: _dimension(task, key, value, anchor, evidence, calculations) for key, value in item['dimensions'].items()}
    if assessment == '不适用' and any(value['state'] != '不适用' for value in dimensions.values()):
        raise ContractError('整体不适用与分维度复核矛盾')
    if assessment == '证据不足' and not any(value['state'] in ('证据不足', '未到验证期') for value in dimensions.values()):
        raise ContractError('整体证据不足须说明尚待验证维度')
    record = dict(version=VERSION, binding=copy.deepcopy(binding), prior_version=base['prior_version'], prior_digest=base['prior_digest'],
        prior_thesis_id=ident, prior_thesis_hash=anchor['thesis_hash'], prior_title=anchor['title'],
        prior_judgment=anchor['judgment'], conditions_hash=anchor['conditions_hash'], question_ids=copy.deepcopy(anchor['question_ids']),
        assessment=assessment, current_theses=current, dimensions=dimensions)
    return dict(record, review_hash=fingerprint(record))


def evaluate(task):
    if not enabled(task):
        return [], []
    base = _baseline(task)
    anchors = {item['id']: item for item in base['anchors']}
    evidence = {item['id']: item for item in task['evidence']}
    calculations = {item['id']: item for item in task['calculations']}
    accepted = {}
    for item in task['theses']:
        if item.get('proposal_hash'):
            accepted.setdefault(item['proposal_hash'], []).append(item)
    candidates, issues = {}, []
    for bundle in task.get('judgment_review_proposals', []):
        try:
            binding = bundle['binding']
            current = context(task)
            if (any(binding.get(key) != value for key, value in current.items() if key != 'batch')
                    or type(binding.get('batch')) is not int or not 1 <= binding['batch'] <= current['batch']):
                raise ContractError('复核提案的任务、版本、截止时点、输入或生产批次不符')
            if (any(fingerprint(evidence.get(ref)) != value for ref, value in bundle['evidence'].items())
                    or any(fingerprint(calculations.get(ref)) != value for ref, value in bundle['calculations'].items())):
                raise ContractError('复核依赖已变化，须重新分析')
            validate_shape(bundle['reviews'])
            allowed = {item['id'] for item in contract(task, bundle['selected'])['anchors']}
            if any(item['prior_thesis_id'] not in allowed for item in bundle['reviews']):
                raise ContractError('复核提案不属于留存的分析节点范围')
            # A malformed later review invalidates this complete proposal, not
            # just its inconvenient row. Conflicts across proposals also reject
            # every competing result instead of retaining the first verdict.
            local = [_review(task, item, base, anchors, accepted, evidence, calculations, bundle['thesis_hashes'], binding)
                     for item in bundle['reviews']]
            for record in local:
                candidates.setdefault(record['prior_thesis_id'], []).append(record)
        except (ContractError, KeyError, TypeError, IndexError) as error:
            issues.append(str(error) if isinstance(error, ContractError) else '复核提案结构已变化')
    records = [candidates[ident][0] for ident in anchors if len(candidates.get(ident, [])) == 1]
    pending = [dict(id=ident, title=anchor['title'], reason='存在重复或冲突复核' if ident in candidates else '未取得四维有效复核')
               for ident, anchor in anchors.items() if len(candidates.get(ident, [])) != 1]
    if issues:
        pending.append(dict(id='', title='', reason='；'.join(dict.fromkeys(issues))))
    return records, pending


def validate_saved(task, prior_report=None):
    if not enabled(task):
        return
    if prior_report is not None and baseline(prior_report) != task.get('judgment_review_baseline'):
        raise ContractError('判断复核基准与实际原交付不一致')
    records, pending = evaluate(task)
    if task.get('judgment_reviews') != records or task.get('judgment_review_pending') != pending:
        raise ContractError('判断复核与本版依据、原条件或已接受论点不一致')
