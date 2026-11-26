"""Separate independently answerable policy and fund requests in one task."""
import re

from .subject_scope import scope_question


POLICY = 'draft_parallel_study_version'
POLICY_GROUP = 'policy'
FUND_GROUP = 'funds'

_SPLIT = re.compile(r'\s*[；;]\s*|[。！？?]\s*(?=(?:另外|另请|顺便|再比较|同时|还要))')
_REPORT_TAIL = re.compile(r'[，,。]\s*(?:最后|并)?(?:请)?(?:写|生成|给)(?:我)?(?:一份)?(?:完整)?(?:投研|研究)?报告[。！!]*$')
_LEAD = re.compile(r'^(?:第一(?:个|项)?[、.．：:]\s*|第二(?:个|项)?[、.．：:]\s*|(?:另外|另请|顺便|再)\s*)')


def branch_question(task, group):
    """Expose the branch text without replacing the task or its budget."""
    if task.get(POLICY) != 1:
        return None
    return next((row for row in task.get('questions', ()) if row['group'] == group and row['required']), None)


def planned_questions(task):
    """Keep user order and admit only two fully recognized independent requests."""
    from .etf_comparison import comparison_scope
    from .official_sources import direct_fomc_rate_request

    if (task.get('draft_first_version') != 1 or task.get('research_mode') != 'current'
            or task.get('intent') not in ('综合研究', '比较对象') or task.get('question_origin') != 'default'
            or task.get('objects') or task.get('materials') or task.get('pending_materials')):
        return []
    text = _REPORT_TAIL.sub('', scope_question(task).strip())
    clauses = [_LEAD.sub('', part.strip()).strip('，,。！？!?') for part in _SPLIT.split(text)]
    if len(clauses) != 2 or not all(clauses):
        return []
    groups = []
    for clause in clauses:
        # Parsing view only. Acquisitions always act on the original task.
        candidate = dict(question=clause, kind='fact', required=True)
        view = dict(task, question=clause, questions=[candidate])
        view.pop(POLICY, None)
        view.pop('update_from', None)
        if view.get('subject_scope'):
            view['subject_scope'] = dict(view['subject_scope'], question_spans=[])
        if direct_fomc_rate_request(view):
            groups.append(dict(candidate, group=POLICY_GROUP))
        elif (scope := comparison_scope(view, direct=True, allow_fee_only=True)) and scope.get('fees_only'):
            groups.append(dict(candidate, kind='comparison', group=FUND_GROUP))
        else:
            return []
    return groups if {row['group'] for row in groups} == {POLICY_GROUP, FUND_GROUP} else []
