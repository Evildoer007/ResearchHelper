"""Exact, predeclared answer choices from complete submitted thesis fields.

This is not a prose repair. A reviewer must explicitly judge the whole choice
complete, and every donor field must independently pass the ordinary review.
"""
import copy

from .contracts import ContractError, fingerprint, evidence_applies_to_answer
from .numeric_integrity import bind_numeric_statement

POLICY = 'review_answer_choices_version'
STATUS = 'answered_from_thesis'


def enabled(task):
    version = task.get(POLICY)
    if version is None: return False
    if type(version) is not int or version != 1:
        raise ContractError('复核回答选择版本未登记')
    return task.get('intent') != '原件内容核对'


def answer(task, proposal, ident):
    """Copy full fields in declared order; never pick individual sentences."""
    if not enabled(task): return None
    question = next((q for q in task['questions'] if q['id'] == ident), None)
    if question is None or question.get('answer_scope') == 'general_definition': return None
    donors = [(index, thesis) for index, thesis in enumerate(proposal.get('theses', []))
              if ident in thesis.get('question_ids', [])]
    if not donors: return None
    parts, refs, calculations, knowledge, targets = [], [], [], [], []
    for index, thesis in donors:
        for field in ('title', 'investment_meaning', 'limitation'):
            units = thesis.get('numeric_segments', {}).get(field)
            if units is None:
                text = thesis.get(field)
                if not isinstance(text, str) or not text.strip(): return None
                units = [dict(text=text)]
            if not isinstance(units, list) or not units: return None
            if parts: parts.append(dict(text='\n\n'))
            parts.extend(copy.deepcopy(units))
            targets.append('thesis:'+str(index)+'.'+field)
        for values, bucket in [(thesis.get('support', [])+thesis.get('counter', []), refs),
                (thesis.get('calculation_refs', []), calculations), (thesis.get('knowledge_refs', []), knowledge)]:
            for value in values:
                if value not in bucket: bucket.append(copy.deepcopy(value))
    result = dict(numeric_segments=dict(answer=parts), refs=refs,
        calculation_refs=calculations, knowledge_refs=knowledge)
    # A multi-question thesis may cite facts valid only for another question.
    # Do not offer that union as a single-question answer. Ordinary completion
    # still checks requirements, dependencies and registered calculations.
    evidence = {item['id']: item for item in task['evidence']}
    if any(ref not in evidence or not evidence_applies_to_answer(task, evidence[ref], ident, calculations)
           for ref in refs):
        return None
    try:
        bind_numeric_statement(task, dict(result, question_ids=[ident]), ['answer'], refs, calculations)
    except ContractError:
        return None
    return dict(answer=result, donor_targets=targets, candidate_hash=fingerprint(result))


def alternatives(task, proposal):
    if not enabled(task): return {}
    choices = {}
    for question in task['questions']:
        ident = question['id']
        choice = answer(task, proposal, ident)
        if choice:
            choices[ident] = {key: copy.deepcopy(choice[key]) for key in ('donor_targets', 'candidate_hash')}
    return choices


def selected(question_review):
    return (isinstance(question_review, dict)
        and question_review.get('answer_origin', {}).get('kind') == 'reviewed_thesis_fields')


def normalize_question(item, request, assessments):
    if item['status'] != STATUS: return copy.deepcopy(item)
    choice = request['scope'].get('answer_choices', {}).get(item['question_id'])
    supported = {a['target_id'] for a in assessments if a['supported']}
    if not choice or not set(choice['donor_targets']).issubset(supported) or item['missing_evidence']:
        raise ContractError('候选回答未获完整论点支持或仍缺证据，不能标为已回答')
    result = copy.deepcopy(item)
    result['status'] = 'answered'
    result['answer_origin'] = dict(kind='reviewed_thesis_fields', **copy.deepcopy(choice))
    return result


def wire_question(item):
    result = copy.deepcopy(item)
    if selected(result):
        result.pop('answer_origin')
        result['status'] = STATUS
    return result


def selected_answer(task, proposal, request, result, ident):
    reviewed = next((q for q in result['question_assessments'] if q['question_id'] == ident), None)
    if not selected(reviewed): return None
    choice = answer(task, proposal, ident)
    expected = request['scope'].get('answer_choices', {}).get(ident)
    if (not choice or expected != {key: choice[key] for key in ('donor_targets', 'candidate_hash')}
            or reviewed['answer_origin'] != dict(kind='reviewed_thesis_fields', **expected)):
        raise ContractError('候选回答与复核绑定不一致，不继承批准')
    if not set(choice['donor_targets']).issubset({a['target_id'] for a in result['assessments'] if a['supported']}):
        raise ContractError('候选回答含未获支持的完整论点字段')
    return copy.deepcopy(choice['answer'])


def effective_assessments(request, result):
    replaced = {'answer:'+q['question_id']+'.answer' for q in result['question_assessments'] if selected(q)}
    return [item for item in result['assessments'] if item['target_id'] not in replaced]
