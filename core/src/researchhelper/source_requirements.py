"""Explicit catalogue alternatives, never issuer or financial equivalence.

Only a retained name query can offer alternatives. A caller must choose the
reference before I/O; unmarked requests keep their independent obligations.
"""
import copy

from .contracts import ContractError,fingerprint

POLICY='catalogue_alternative_version'
FIELD='catalogue_alternative_ref'
REGISTRY='catalogue_alternative_requests'
MARKET_WORDS=('A股','H股','港股','美股','沪市','深市','香港市场','上海市场','深圳市场')


def enabled(task):
    value=task.get(POLICY)
    if value is None:return False
    if type(value) is not int or value!=1:raise ContractError('目录备选关系版本未登记')
    return True


def binding(task):
    return {key:task[key] for key in ('id','version','input_hash','cutoff')}


def alternatives(task):
    if not enabled(task):return []
    from .evidence import identity_candidates,declared_security_codes
    if task.get('research_mode')!='current' or declared_security_codes(task):return []
    if any(word in task['question'] for word in MARKET_WORDS):return []
    questions=[q['id'] for q in task['questions'] if q.get('kind')=='fact' and not q.get('method_needs')
        and not any(word in str(q.get('question',''))+' '.join(q.get('evidence_needs',[])) for word in MARKET_WORDS)]
    if not questions:return []
    candidates=identity_candidates(task);result=[]
    for candidate in candidates:
        codes=candidate['exact_lookup_codes']
        if (not candidate.get('business_lookup_authorized') or len(codes)<2
                or any(peer['query']==candidate['query'] and peer['codes']!=candidate['codes'] for peer in candidates)):
            continue
        scope=dict(binding=binding(task),candidate={key:candidate[key] for key in
            ('artifact_id','query','snapshot_hash','codes')},question_ids=questions)
        result.append(dict(reference='rh_catalogue_alt_'+fingerprint(scope)[:24],
            name_query=candidate['query'],candidate_artifact_id=candidate['artifact_id'],
            candidate_snapshot_hash=candidate['snapshot_hash'],codes=list(codes),question_ids=list(questions),
            identity_approved=False,originals_equivalent=False,financial_approval=False,
            rule='仅为取得当前公司事实选择备选目录，显式选择后可由任一目录的适用原件完成来源路径。'
                 '不证明A/H主体或原件相同，不批准其中的数字，不用于明确市场或多对象比较。'))
    return result


def validate_choice(task,need):
    if FIELD not in need:return None
    if not enabled(task):raise ContractError('旧任务不接受目录备选引用')
    if (need.get('source','ifind'),need.get('operation'))!=('ifind','report_query'):
        raise ContractError('备选引用只附于公告目录请求，不传给供应商或其他接口')
    reference=need[FIELD]
    if reference is None:return None
    matches=[choice for choice in alternatives(task) if choice['reference']==reference]
    if len(matches)!=1:raise ContractError('目录备选引用过期或不属于本任务实际名称候选')
    choice=matches[0];questions=need.get('question_ids',[])
    if (need.get('parameters',{}).get('codes') not in choice['codes'] or not questions
            or not set(questions).issubset(choice['question_ids'])):
        raise ContractError('目录备选引用不能合并明确标的、市场或其他研究问题')
    return choice


def register(task,needs):
    """Persist chosen source-path intent before dispatch, without data approval."""
    entries=task.get(REGISTRY,{})
    if not isinstance(entries,dict):raise ContractError('目录备选登记格式无效')
    changed=False
    for need in needs:
        if validate_choice(task,need) is None:continue
        entry=dict(binding=binding(task),request=copy.deepcopy(need))
        key=fingerprint(entry)
        if key not in entries:entries[key]=entry;changed=True
    if changed:task[REGISTRY]=entries
    return changed


def checked_entries(task):
    entries=task.get(REGISTRY,{})
    if not enabled(task):return []
    if not isinstance(entries,dict):raise ContractError('目录备选登记格式无效')
    result=[]
    for key,entry in entries.items():
        if (not isinstance(entry,dict) or set(entry)!={'binding','request'}
                or entry['binding']!=binding(task) or key!=fingerprint(entry)):
            raise ContractError('目录备选登记与任务输入不一致，不继承旧来源要求')
        if validate_choice(task,entry['request']) is None:
            raise ContractError('目录备选登记缺少显式选择')
        result.append(entry)
    return result


def catalogue_scopes(task,item):
    """Return explicit question/group pairs, or None for the old strict scope."""
    if not enabled(task) or (item.get('source','ifind'),item.get('operation'))!=('ifind','report_query'):
        return None
    if 'kind' not in item:
        choice=validate_choice(task,item)
        return [(q,('ifind','catalogue_alternative',choice['reference'])) for q in item['question_ids']] if choice else None
    ids=item.get('data_need_ids',[])
    if not ids or not task.get(REGISTRY):return None
    from .evidence import current_query_reference
    if current_query_reference(task,item) is None:return None
    entries=[e['request'] for e in checked_entries(task) if e['request']['id'] in ids
        and fingerprint(dict(source='ifind',operation='report_query',parameters=e['request']['parameters']))==item['request_hash']]
    if set(ids)!={e['id'] for e in entries}:return None
    pairs={(q,('ifind','catalogue_alternative',e[FIELD])) for e in entries for q in e['question_ids']}
    if set(item.get('question_ids',[]))!={q for q,scope in pairs}:return None
    return sorted(pairs)


def frozen_requirements(task):
    if not enabled(task):return None
    return dict(version=1,requests=copy.deepcopy(checked_entries(task)),financial_approval=False,
        rule='只冻结本版显式选择的备选目录。原件内容、完整需求、单位、期间及金融适用性分别核验。')
