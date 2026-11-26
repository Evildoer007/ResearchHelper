"""Compose and verify the separate answers to a parallel research request."""
from .contracts import fingerprint
from .parallel_study import FUND_GROUP, POLICY, POLICY_GROUP, branch_question


def policy_source_view(task):
    """The committee passage reader has no need to reopen the fund PDFs."""
    catalogs = {row['id'] for row in task['source_records'] if row.get('source') == 'official'
                and row.get('operation') == 'release_catalog' and row['parameters'].get('channel') == 'fed_fomc'}
    rows = [row for row in task['source_records'] if row['id'] in catalogs
            or row.get('source') == 'official' and row.get('operation') == 'release_original'
            and row['parameters'].get('catalog_id') in catalogs]
    return dict(task, source_records=rows)


def study_readout(task, facts, calculations, excerpts):
    from .draft_readout import _section, etf_product_readout, fomc_readout

    policy_question = branch_question(task, POLICY_GROUP)
    fund_question = branch_question(task, FUND_GROUP)
    if not policy_question or not fund_question:
        return None
    rates = fomc_readout(task, excerpts)
    funds = etf_product_readout(task, facts, calculations)
    if not funds:
        return None

    answers = {}
    sections = []
    summary = ''
    summary_links = []
    for question in task['questions']:
        if question['group'] == POLICY_GROUP:
            rate_links = rates['links'] if rates else []
            sources = list(dict.fromkeys(link['document']['source_ref'] for link in rate_links))
            text = rates['text'] if rate_links else '可核对的会议声明尚未齐备。'
            section = _section('联邦基金目标利率', (), (), [text])
            section['source_refs'] = sources
            sections.append(section)
            if rate_links:
                link = rate_links[-1]
                quote = link['original']
                direction = '维持在' if rates['policy_direction'] == 'hold' else '为'
                summary += rates['latest_observation']+'，美联储联邦基金目标利率区间'+direction
                start = len(summary)
                summary += quote+'。'
                summary_links.append(dict(link, start=start, end=start+len(quote)))
                if not rates['notes']:
                    answers[question['id']] = dict(answer=text, source_refs=sources)
        elif question['group'] == FUND_GROUP:
            sections.extend(funds['sections'])
            summary += funds['conclusion']
            if funds['coverage']:
                source_refs = list(dict.fromkeys(ref for section in funds['sections'] for ref in section['source_refs']))
                answers[question['id']] = dict(answer=funds['conclusion'], source_refs=source_refs)

    return dict(title='美联储目标利率与'+funds['title'],
                conclusion=summary or '下文分别列示政策与基金比较的资料覆盖。',
                sections=sections, risks=funds['risks'], links=summary_links, answers=answers)


def verified_answers(task, draft):
    """Bind each answer to its own sources and the single frozen paper."""
    from .draft_analysis import analyze
    from .draft_sources import observations, retained_public_excerpts
    from .planning import input_fingerprint

    if (task.get(POLICY) != 1 or task.get('run_mode') not in ('direct', 'local_draft')
            or draft.get('origin') != 'data_outline' or input_fingerprint(task) != task['input_hash']):
        return {}
    facts = observations(task)
    calculations = analyze(task, facts)['results']
    if draft['facts'] != facts or draft['calculations'] != calculations:
        return {}
    readout = study_readout(task, facts, calculations, retained_public_excerpts(policy_source_view(task)))
    if not readout or readout['conclusion'] != draft['conclusion']:
        return {}
    keys = ('title', 'analysis', 'data_refs', 'calculation_refs', 'source_refs')
    expected, delivered = ([{key: section.get(key) for key in keys} for section in items]
                           for items in (readout['sections'], draft['logics']))
    if expected != delivered:
        return {}

    records = {row['id']: row for row in task['source_records']}
    result = {}
    for ident, answer in readout['answers'].items():
        refs = answer['source_refs']
        if not refs or any(ident not in records.get(ref, {}).get('question_ids', []) for ref in refs):
            continue
        result[ident] = dict(version=1, task_id=task['id'], research_version=task['version'],
                             input_hash=task['input_hash'], question_id=ident, source_refs=refs,
                             answer=answer['answer'], paper_hash=fingerprint(delivered))
    return result
