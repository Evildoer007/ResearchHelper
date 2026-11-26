"""First research delivery from acquired data, independent of review completion.

Raw observations stay distinct from approved evidence. Review may refine a
later version; missing fields do not prevent a useful first paper.
"""
import copy
import html
import json
import math
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
import re

from .contracts import ContractError, canonical, fingerprint, unique_json_object, validate_tracking
from .layout import resource_directory
from .publication_content import (source_label, publication_source_labels, calculation_title, draft_logic_heading,
                                  draft_logic_data_refs, draft_logic_reading, draft_risks_at_end, observation_unit,
                                  DRAFT_EDITORIAL_POLICY, DRAFT_EDITORIAL_VERSION, DRAFT_READING_POLICY,
                                  draft_editorial_enabled, draft_reading_enabled)


def source_binding(task):
    return fingerprint(dict(task_id=task['id'], version=task['version'], input_hash=task['input_hash'],
        records=[dict(id=r.get('id'), snapshot=fingerprint(r.get('snapshot')))
                 for r in task.get('source_records', []) + task.get('materials', [])]))


# Public compatibility alias; source decoding belongs to EvidenceCollector.
from .draft_sources import observations, _sequence_day


# Question classification is shared with acquisition and the Skill host.
from .planning import QUESTION_ARGUMENT_POLICY, draft_direct_scope as _direct_scope, draft_research_scope, question_driven_arguments


def client_financial_value(label):
    """Use readable precision without turning small moves into zero."""
    matched = re.fullmatch(r'([+-]?\d{1,12}\.\d{2,8})(%|倍)', label)
    if matched is None:
        return label
    reference = Decimal(matched[1])
    if abs(reference) < 1:
        return label
    rounded = reference.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)
    return format(rounded.normalize(), 'f') + matched[2]


def _report_organization(task, value):
    scope = draft_research_scope(task)
    if scope is None:
        from .economic_sources import direct_economic_readout, direct_economic_scope

        return 'fact' if direct_economic_readout(task) or direct_economic_scope(task) else None
    selected = value.get('report_scope')
    if isinstance(selected, str) and selected in scope['allowed_report_scopes']:
        return selected
    return scope['mode'] if scope['mode'] in ('fact', 'comparison', 'mechanism') else None


def _direct_outline(kind, refs, calculation_refs=(), *, concise=False):
    comparison = kind == 'comparison'
    title, analysis, risk = '原始观测', '', ''
    if comparison:
        title = '数据对照' if concise else '同口径对照'
    if not concise:
        analysis = ('按对象、日期和单位逐项对照已取得的数据。' if comparison
                    else '列示已取得的原始数值及其日期、单位和来源。')
        risk = '期间、统计范围或计量口径不同会影响可比性。'
    return [dict(title=title, analysis=analysis, implication='', risk=risk, status='待验证',
        data_refs=refs, source_refs=[], calculation_refs=list(calculation_refs), knowledge_refs=[])]


def _latest_report_title(scopes, *, points=(), calculations=()):
    """Name a direct market report from its requested metrics and subjects."""
    from .market_series import SERIES_LABELS, series_family

    groups = _market_point_labels(points)
    for scope in scopes:
        codes = scope['as_of_by_code']
        subject = scope.get('name') or '、'.join(codes)
        close_label = '收盘点位' if codes and all(series_family(code) == '指数' for code in codes) else '收盘价'
        label = SERIES_LABELS.get(scope['indicator'], close_label)
        labels = groups.setdefault(subject, [])
        if label not in labels:
            labels.append(label)
    names = {'daily_return': '日涨跌幅', 'close_pair_return': '区间涨跌幅', 'daily_nav_premium': '收盘折溢价率'}
    for code, _, _, measure in sorted(calculations):
        labels = groups.setdefault(code, [])
        if names[measure] not in labels:
            labels.append(names[measure])
    comparison = len(groups) == 1 and scopes and all(len(scope['as_of_by_code']) > 1 for scope in scopes)
    suffix = '对照' if comparison else '核对'
    return _market_readout_title(groups, suffix)


def _market_readout_title(groups, suffix):
    if not groups:
        return ''
    fields = list(groups.values())
    if len(groups) > 1 and all(labels == fields[0] for labels in fields):
        title = '与'.join(groups) + '的' + '与'.join(fields[0]) + suffix
    else:
        title = '、'.join(subject + '与'.join(labels) for subject, labels in groups.items()) + suffix
    return title if len(title) <= 36 else '多标的行情与指标' + suffix


def _market_point_labels(points):
    from .market_series import series_family

    groups = {}
    for code, _, field in sorted(points):
        label = '单位净值' if field == 'unit_nav' else '收盘点位' if series_family(code) == '指数' else '收盘价'
        labels = groups.setdefault(code, [])
        if label not in labels:
            labels.append(label)
    return groups


def _market_calculation_scope(calculation):
    """Match a verified result to the exact market request it can answer."""
    from .subject_scope import subject_key

    method = calculation.get('method')
    if method == 'etf_daily_nav_premium_v1':
        measure = 'daily_nav_premium'
        start = end = calculation['period']
    elif method == 'raw_change_v1' and calculation.get('comparison_kind') == 'previous_session':
        measure = 'daily_return'
        start = end = calculation['window_end']
    elif method == 'raw_change_v1' and calculation.get('label') == '区间收盘涨跌幅':
        measure = 'close_pair_return'
        start, end = calculation['window_start'], calculation['window_end']
    else:
        return None
    return subject_key(calculation['code']).upper(), start, end, measure


def _dated_market_report_title(task, *, index_names=None):
    from .market_calendar import stated_close_pairs, stated_close_returns, stated_direct_market_day, stated_market_points

    if not stated_direct_market_day(task, for_report=True):
        return ''
    groups = _market_point_labels((code, day, field) for code, day, field, _ in stated_market_points(task))
    for code, _, _ in sorted(stated_close_returns(task)):
        labels = groups.setdefault(code, [])
        if '日涨跌幅' not in labels:
            labels.append('日涨跌幅')
    pairs = stated_close_pairs(task)
    for code in sorted(pairs):
        groups.setdefault(code, []).append('区间涨跌幅')
    names = index_names or {}
    comparison = bool(pairs) or len(groups) > 1 and bool(re.search(r'对比|比较|对照', task['question']))
    return _market_readout_title({names.get(code, code): labels for code, labels in groups.items()},
                                 '对照' if comparison else '核对')


def _market_point_gap(task, gap, *, nav=False):
    from .market_series import daily_market_as_of, series_family

    code, day = gap['code'], gap['window_start']
    label = '单位净值' if nav else '收盘点位' if series_family(code) == '指数' else '收盘价'
    if day > daily_market_as_of(task):
        return f'{code}的指定日期{day}晚于本报告的日频数据截止日，{label}暂缺。'
    if gap.get('no_sessions'):
        return f'{day}交易所休市，{code}当天无{label}。'
    if gap.get('unit_conflict'):
        return f'{code}在{day}的{label}单位口径不一致，待核对。'
    if gap.get('conflicting'):
        return f'{code}在{day}的{label}原值不一致，待核对。'
    source = {'official': '上交所申赎清单', 'ifind': 'iFinD'}.get(gap.get('requested_source')) if nav else None
    lead = f'未从{source}取得' if source else '未取得'
    note = f'{lead}{code}在{day}的{label}。'
    return note + ('当天交易日历尚未核对。' if gap.get('calendar_unverified') else '')


def _close_return_gap(gap):
    previous = gap.get('previous_date')
    reason = {
        'calendar_unverified': '上一交易日尚未核对',
        'previous_missing': f'上一交易日{previous}缺少收盘读数',
        'previous_conflict': f'上一交易日{previous}的收盘读数不一致',
        'incompatible_basis': '两日收盘读数口径不一致',
        'invalid_close': '收盘读数不适用于计算',
    }[gap['reason']]
    return f'{gap["code"]}在{gap["day"]}的日涨跌幅暂缺，{reason}。'


def _retained_report_title(task, observations, original_excerpts):
    """Name a first report from the material actually used on the paper."""
    from .draft_filing_amounts import is_filing_amount
    from .evidence import business_payload
    from .source_catalogue import disclosure_fields

    def heading(value):
        value = _prose(value)
        if (0 < len(value) <= 36 and not re.search(r'[\x00-\x1f\x7f/\\:*?"<>|：]', value)
                and business_payload(value) == value):
            return value
        return ''

    rows = [row for row in observations if row.get('value') is not None]
    if rows and all(is_filing_amount(row) for row in rows):
        subjects = list(dict.fromkeys((row['code'], row['object_label']) for row in rows))
        if len(subjects) > 1:
            return '多标的财务数据核对'
        code, name = subjects[0]
        subject = heading(name) or heading(code)
        periods = {row['period'] for row in rows}
        if len(periods) != 1:
            return heading(subject + '多期财务核对') or '多期财务核对'
        start, separator, end = next(iter(periods)).partition('至')
        period = ''
        if separator and start[:4] == end[:4] and start[4:] == '-01-01':
            label = {'-03-31': '一季度', '-06-30': '上半年',
                     '-09-30': '前三季度', '-12-31': '全年'}.get(end[4:])
            if label:
                period = start[:4] + '年' + label
        return (heading(subject + period + '财务核对') or heading(subject + '财务核对')
                or heading(code + '财务核对') or '财务数据核对')
    economic_sources = {record['id'] for record in task.get('source_records', [])
                        if (record.get('source'), record.get('operation')) == ('ifind', 'edb_service')
                        and record.get('task_id') == task['id'] and record.get('research_version') == task['version']}
    if rows and all(row['artifact_id'] in economic_sources and row['field'] == 'value' and row.get('axis_role') == 'time'
                    for row in rows):
        from .draft_sources import _economic_metadata

        metadata = _economic_metadata(task)
        codes = {row['code'] for row in rows}
        regions = {metadata.get(code, {}).get('country', '') for code in codes}
        country = next(iter(regions)) if len(regions) == 1 else ''
        name_parts = {tuple(part.strip() for part in row['label'].replace('：', ':').split(':') if part.strip())
                      for row in rows if row['label'] != row['code']}
        name_parts.discard(())
        measures_known = {'当月同比', '当月环比', '累计同比', '累计环比', '季度同比', '年度同比'}
        subjects = {parts[0] for parts in name_parts}
        measures = {parts[-1] for parts in name_parts}
        subject = '宏观指标'
        if len(codes) == len(name_parts) == 1:
            subject = ''.join(next(iter(name_parts)))
        elif len(name_parts) == len(codes) and len(subjects) == len(measures) == 1 and country and measures <= measures_known:
            subject = next(iter(subjects)) + next(iter(measures))
        prefix = country if country not in subject else ''
        return heading(prefix + subject + '观察') or '宏观指标观察'
    if rows or not original_excerpts:
        return ''

    refs = {row['source_ref'] for row in original_excerpts}
    records = [record for record in task.get('source_records', []) + task.get('materials', [])
               if record.get('id') in refs]
    if not records:
        return ''
    if len(refs) > 1:
        return '多份原件核对'
    if len(records) != 1:
        return ''
    fields = disclosure_fields(records[0])
    title = fields.get('title', '')
    if not title:
        return '材料原文核对'
    name = fields.get('issuer_name', '')
    if name:
        for separator in ('：', ':'):
            title = title.removeprefix(name + separator).strip()
    subject = name if name and name not in title else ''
    return (heading(subject + title + '原文核对') or heading(title + '原文核对')
            or heading(subject + '披露原文核对') or '公开原文核对')


def _latest_readout(scope, facts):
    """State only the current request's readings in the client conclusion."""
    from .market_series import NAV_INDICATOR, SERIES_LABELS
    from .publication_content import observation_presentation

    codes = list(scope['as_of_by_code'])
    compared = len(codes) > 1
    label = SERIES_LABELS.get(scope['indicator'], '收盘价' if scope.get('local_sources_only') else '收盘行情')
    if scope.get('needs_subject'):
        from .draft_sources import stated_latest_subject_prompt

        return label + '核对', stated_latest_subject_prompt(scope), 'fact', []
    subject = scope.get('name') or (codes[0] if len(codes) == 1 else '')
    title = subject + label + ('对照' if compared else '核对')
    by_id = {row['id']: row for row in facts}
    refs = scope['current_observation_refs']
    rows = [by_id[refs[code]] for code in codes if refs.get(code) in by_id]
    sentences, links = [], []
    offset = 0
    unconfirmed = set(scope.get('unconfirmed_session_codes', ()))
    for row in rows[:4]:
        reading = observation_presentation(row)
        unit = reading['unit']
        amount = reading['value']
        announced = scope.get('official_announcements', {}).get(row['code'])
        if announced:
            lead = f'上交所{announced}申赎清单载明，{row["code"]}在{reading["period"]}的{row["label"]}为'
        else:
            lead = f'{reading["period"]}，{row["code"]}{row["label"]}为'
        if unit == '人民币元':
            lead += '人民币'
        suffix = '，单位待核对' if unit == '未确认' else '元' if unit == '人民币元' else unit
        start = offset + len(lead)
        links.append(dict(kind='observed_readout', start=start, end=start+len(amount), original=amount,
                          data_refs=[row['id']], calculation_refs=[], row_hash=fingerprint(row)))
        sentence = lead + amount + suffix + '。'
        sentences.append(sentence)
        offset += len(sentence)
    if len(rows) > 4:
        sentences.append('其他标的见数据表。')
    if not rows and not scope.get('suppress_missing_summary'):
        if scope.get('local_sources_only'):
            from .draft_sources import stated_latest_notes

            sentences.extend(stated_latest_notes(dict(stated_latest_observation=scope)))
        elif len(codes) == 1 and (day := scope.get('latest_sessions', {}).get(codes[0])):
            missing = '收盘读数' if scope['indicator'] == 'close' else label
            sentences.append(f'{codes[0]}在最近一个交易日{day}的{missing}暂缺。')
        elif scope['indicator'] == NAV_INDICATOR and unconfirmed:
            sentences.append('最近交易日尚未核实，本次未取得可用的单位净值。')
        else:
            sentences.append(f'本次未取得可用的{label}。')
        if any(row.get('value') is not None and not row.get('previous_close_for') for row in facts):
            sentences.append('留存读数按原观测日期列示。')
    elif rows and scope['missing_codes']:
        sentences.append('其余标的读数暂缺。')
    if unconfirmed and rows:
        names = '、'.join(code for code in codes if code in unconfirmed) if compared else ''
        available = '收盘读数' if scope['indicator'] == 'close' else label
        sentences.append(f'{names}最近交易日尚未核实，以上为已取得的{available}。')
    return title, ''.join(sentences), 'comparison' if compared else 'fact', links


def _mechanism_outline(explanations, *, summarize):
    logics, summary = [], ''
    for entry, channels in explanations:
        title, risk = entry['title'], entry['counterexample']
        analysis = '\n'.join(channels) or entry['content']
        focused = len(channels) < len(entry.get('channels', ()))
        if focused:
            labels = [line.partition('：')[0].removesuffix('路径') for line in channels]
            title = '物价变化的' + '与'.join(labels) + '传导'
            risk = ''  # The selected path already includes its own counterexample.
        if summarize and len(explanations) == 1:
            if focused and len(channels) == 1:
                premise, delimiter, limitation = analysis.partition('：')[2].partition('；')
                if premise and delimiter and limitation:
                    summary, analysis = premise + '。', limitation
            elif entry['id'] != 'rh_k_inflation_transmission' and channels:
                summary = entry['content']
        logics.append(dict(title=title, analysis=analysis, implication='', risk=risk, status='待验证',
            data_refs=[], source_refs=[], calculation_refs=[],
            knowledge_refs=[dict(id=entry['id'], version=entry['version'], entry_hash=fingerprint(entry))]))
    return logics, summary


def _restrict_historical_citations(proposal, restricted):
    """Keep other sections and bound data when a submitted claim cites an excluded original."""
    if not restricted:
        return proposal, []
    logics = proposal.get('logics', proposal.get('strategies'))
    if not isinstance(logics, list):
        return proposal, []
    adjusted, changes = [], []
    for index, logic in enumerate(logics):
        refs = logic.get('source_refs') if isinstance(logic, dict) else None
        if not isinstance(refs, list) or not any(isinstance(ref, str) and ref in restricted for ref in refs):
            adjusted.append(logic)
            continue
        replacement = '所引材料的历史可得性待核对，本节保留已绑定的可用数据。'
        row = {key: logic[key] for key in ('calculation_refs', 'knowledge_refs', 'workpaper_notes') if key in logic}
        row.update(title='历史披露待核对', analysis=replacement, data_refs=draft_logic_data_refs(logic),
                   source_refs=[ref for ref in refs if isinstance(ref, str) and ref not in restricted])
        adjusted.append(row)
        changes.append(dict(path=f'logics.{index}', original=canonical(logic), replacement=replacement,
                            reason='draft_historical_original_unavailable', financial_approval=False))
    if not changes:
        return proposal, []
    result = dict(proposal, logics=adjusted)
    for key, text in (('title', '历史时点研究'),
                      ('conclusion', '以下保留可用的研究资料。部分引文在研究时点的原件版本仍待核对。')):
        if proposal.get(key) != text:
            changes.append(dict(path=key, original=proposal.get(key), replacement=text,
                                reason='draft_historical_original_unavailable', financial_approval=False))
            result[key] = text
    return result, changes


def draft_conditions(value):
    """Project saved report premises and optional observation rules, not outcomes.

    A draft's business premise is useful even before a complete observation rule
    exists. Neither is inserted into the accepted-thesis tracking ledger.
    """
    draft = value.get('draft_report', value.get('draft', {}))
    if not isinstance(draft, dict) or not isinstance(draft.get('logics'), list):
        return []
    result = []
    for index, logic in enumerate(draft['logics'], 1):
        if not isinstance(logic, dict) or not isinstance(logic.get('title'), str):
            continue
        argument = logic.get('argument')
        condition = argument.get('condition') if isinstance(argument, dict) else ''
        condition = condition.strip() if isinstance(condition, str) else ''
        tracking = None
        if type(draft.get('tracking_version')) is int and draft['tracking_version'] == 1:
            proposed = logic.get('tracking')
            try:
                validate_tracking(proposed)
            except ContractError:
                pass
            else:
                if proposed is not None:
                    tracking = {key: text.strip() for key, text in proposed.items()}
        if condition or tracking:
            result.append(dict(logic_index=index, title=logic['title'], condition=condition, tracking=tracking))
    return result


_JSON_STRING = r'"(?:[^"\\]|\\.)*"'
_ARGUMENT_STRING_FIELD = rf'"(?:claim|evidence|reasoning|condition)"\s*:\s*{_JSON_STRING}'
_LOGIC_SIBLING_FIELD = (r'"(?:title|analysis|logic|implication|risk|tracking|mechanism|data_refs|'
                        r'source_refs|calculation_refs|knowledge_refs|workpaper_notes)"\s*:')
_UNCLOSED_ARGUMENT = re.compile(
    rf'(?P<argument>"argument"\s*:\s*{{\s*{_ARGUMENT_STRING_FIELD}(?:\s*,\s*{_ARGUMENT_STRING_FIELD})*)'
    rf'(?=\s*,\s*{_LOGIC_SIBLING_FIELD})|{_JSON_STRING}')


def _recover_draft_arguments(body, repairs):
    """Close an argument before its known sibling without changing any value."""
    count = 0

    def close_argument(match):
        nonlocal count
        if match['argument'] is None:
            return match[0]
        count += 1
        return match[0] + '}'

    if not body.startswith('{'):
        return None
    repaired = _UNCLOSED_ARGUMENT.sub(close_argument, body)
    if not count:
        return None
    try:
        result = json.loads(repaired, object_pairs_hook=unique_json_object)
    except (ValueError, RecursionError):
        return None
    logics = result.get('logics', result.get('strategies')) if isinstance(result, dict) else None
    if (not isinstance(logics, list)
            or sum(isinstance(row, dict) and bool(_written_argument(row)) for row in logics) < count):
        return None
    if repairs is not None:
        repairs.append(dict(kind='argument_boundary', objects=count))
    return result


def report_proposal(value, *, repairs=None):
    """Read structured report fields or an ordinary prose reply."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    reply = value.strip()
    fence = re.match(r'^```(?:json)?(?=\s|\{|\[|$)', reply, re.IGNORECASE)
    body = reply[fence.end():].strip().removesuffix('```').strip() if fence else reply
    try:
        decoded = json.loads(body, object_pairs_hook=unique_json_object)
    except json.JSONDecodeError:
        if (recovered := _recover_draft_arguments(body, repairs)) is not None:
            return recovered
        # Preserve genuine prose. Never put an unfinished object, array of
        # objects or JSON code block in the core conclusion.
        if fence or body.startswith('{') or re.match(r'^\[\s*[\[{"]', body):
            return {}
        return dict(conclusion=reply)
    except (ValueError, RecursionError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _prose(value):
    return value.strip() if isinstance(value, str) else ''


def _written_argument(item):
    argument = item.get('argument')
    return {key: text for key in ('claim', 'evidence', 'reasoning', 'condition')
            if (text := _prose(argument.get(key)))} if isinstance(argument, dict) else {}


def _edit_new_draft(task):
    # Existing deliveries keep their frozen policy. A new composition from
    # retained materials also uses the current editor, even for an older task.
    return draft_editorial_enabled(task) or task.get('draft_first_version') == 1


def editorial_report_proposal(proposal, task):
    """Assess the actual body before generic filler can suppress data-backed writing."""
    if not _edit_new_draft(task) or not proposal:
        return proposal
    from .publication_content import remove_client_filler

    calculations = {row['id'] for row in (task.get('draft_analysis') or {}).get('results', ())
                    if isinstance(row, dict) and isinstance(row.get('id'), str)}
    strip_ids = task.get(DRAFT_EDITORIAL_POLICY) == DRAFT_EDITORIAL_VERSION and calculations

    def client_prose(value):
        value = remove_client_filler(value)
        if strip_ids and isinstance(value, str):
            # A calculation alias may accompany the value in model output.
            # Remove the alias before binding the still-unchanged number.
            value = re.sub(r'(?<![A-Za-z0-9_])(A\d+)\s*(?:为|[：:=])\s*(?=[+−-]?\d)',
                           lambda match: '' if match[1] in calculations else match[0], value)
        return value

    def clean(row, fields):
        return dict(row, **{key: client_prose(row[key]) for key in fields if key in row})

    result = clean(proposal, ('conclusion',))
    key = 'logics' if 'logics' in proposal else 'strategies'
    if isinstance(proposal.get(key), list):
        result[key] = []
        for row in proposal[key]:
            if isinstance(row, dict):
                row = clean(row, ('analysis', 'logic', 'implication', 'risk'))
                if isinstance(row.get('argument'), dict):
                    row['argument'] = clean(row['argument'], ('claim', 'evidence', 'reasoning', 'condition'))
            result[key].append(row)
    if isinstance(proposal.get('risks'), list):
        result['risks'] = [remove_client_filler(risk) for risk in proposal['risks']]
    return result


def has_report_prose(proposal):
    if _prose(proposal.get('conclusion')):
        return True
    rows = proposal.get('logics', proposal.get('strategies'))
    return isinstance(rows, list) and any(
        isinstance(row, dict) and (_prose(row.get('analysis', row.get('logic'))) or _written_argument(row))
        for row in rows)


def normalize_report(value, task, facts, *, retained_data_only=False, original_excerpts=()):
    """Accept useful prose fields independently; formatting errors are advisory."""
    value = editorial_report_proposal(report_proposal(value), task)
    editorial = _edit_new_draft(task)
    from .publication_content import draft_logic_title, prepare_draft_client_copy, requested_strategy_count
    from .draft_sources import CLIENT_COPY_POLICY, CLIENT_COPY_PROSE_VERSION, CLIENT_COPY_VERSION
    from .draft_sources import direct_compound_scope, direct_financial_observations, historical_restricted_sources
    from .financial_sources import direct_financial_quota_reason, direct_financial_scope
    from .official_sources import research_fomc_rate_request
    from .parallel_study import POLICY as PARALLEL_POLICY
    concise = draft_reading_enabled(task)
    if task.get('run_mode') in ('direct', 'offline', 'local_draft') and not has_report_prose(value):
        from .evidence import filing_directory_scope
        if (parameters := filing_directory_scope(task)) is not None:
            from .draft_readout import filing_directory_report

            result = filing_directory_report(task, parameters)
            if concise:
                result[DRAFT_READING_POLICY] = task[DRAFT_READING_POLICY]
            return result
    restricted = historical_restricted_sources(task)
    value, historical_adjustments = _restrict_historical_citations(value, restricted)
    quantity_links = {}
    logic_exclusions = {}
    requested_organization = _report_organization(task, value)
    value,copy_adjustments=prepare_draft_client_copy(task,value,facts,requested_organization,
                                                   quantity_links=quantity_links, logic_exclusions=logic_exclusions)
    data_readout = not has_report_prose(value)
    parallel = data_readout and task.get(PARALLEL_POLICY) == 1
    direct_finance = direct_financial_scope(task) if data_readout else None
    compound = direct_compound_scope(task) if data_readout and direct_finance is None else None
    compound_finance = compound['financial'] if compound else None
    directory_reports = []
    if compound and compound['directories']:
        from .draft_readout import filing_directory_report

        directory_reports = [(parameters, filing_directory_report(task, parameters))
                             for parameters in compound['directories']]
    text = _prose
    notes = lambda values: list(dict.fromkeys(text(item) for item in values if text(item))) if isinstance(values,list) else []

    def display_links(parts, displayed):
        links = []
        offset = 0
        for path, paragraph in parts:
            checked = quantity_links.get(path)
            if checked and checked['text'] == paragraph:
                links.extend(dict(item, start=item['start']+offset, end=item['end']+offset) for item in checked['links'])
            offset += len(paragraph) + 1
        return dict(text_hash=fingerprint(displayed), links=links) if links else None

    logics = []
    direct_scope = _direct_scope(task)
    from .knowledge import ResearchKnowledge
    from .pdf_material import pdf_reparse_replacements
    knowledge = ResearchKnowledge()
    source_ids = {row['id'] for row in task.get('source_records', []) + task.get('materials', [])
                  if row.get('task_id') == task.get('id') and row.get('research_version') == task.get('version')}
    source_ids.difference_update(set(pdf_reparse_replacements(task)) | restricted)
    if task.get('draft_disclosure_scope_version') == 1:
        from .draft_sources import _source_issue
        source_ids.difference_update(record['id'] for record in task.get('source_records', []) + task.get('materials', [])
            if record['id'] in source_ids and record.get('kind') in ('filing', 'material')
            and _source_issue(task, record))
    fact_ids = {row['id'] for row in facts if row.get('artifact_id') in source_ids}
    coverage = task.get('draft_acquisition_coverage') or {}
    binding = dict(task_id=task.get('id'), research_version=task.get('version'), input_hash=task.get('input_hash'))
    from .market_series import STATED_LATEST_POLICY, STATED_LATEST_VERSIONS
    latest = (coverage.get('stated_latest_observation') if task.get(STATED_LATEST_POLICY) in STATED_LATEST_VERSIONS
              and coverage.get('binding') == binding else None)
    mixed_latest = coverage.get('stated_latest_observations', []) if coverage.get('binding') == binding else []
    from .market_calendar import (latest_session_calculation_sessions, named_index_point_subjects,
                                  stated_latest_session_calculation)
    from .subject_scope import scope_question

    index_names = named_index_point_subjects(task, scope_question(task))
    direct_market_title = ''
    session_calculation = stated_latest_session_calculation(task, for_report=True) if data_readout else None
    sessions = latest_session_calculation_sessions(task, session_calculation) if session_calculation else None
    session_calculations = []
    if data_readout:
        if session_calculation:
            for key, label in (('daily_return', '日涨跌幅'), ('daily_nav_premium', '收盘折溢价率')):
                if key in session_calculation['fields']:
                    session_calculations.append(label)
            direct_market_title = session_calculation['code'] + '最近交易日' + '与'.join(
                label.removeprefix('日') for label in session_calculations) + '核对'
        elif mixed_latest and coverage.get('direct_latest_readout'):
            direct_market_title = _latest_report_title(mixed_latest)
        else:
            direct_market_title = _dated_market_report_title(task, index_names=index_names)
    readout = (_latest_readout(latest, [row for row in facts if row['id'] in fact_ids])
               if latest and 'current_observation_refs' in latest and not has_report_prose(value) else None)
    local_readout = (not has_report_prose(value) and bool(
        (readout and latest.get('local_sources_only')) or
        (mixed_latest and all(row.get('local_sources_only') for row in mixed_latest))))
    from .draft_analysis import current_analysis,interpretation_context
    checked = current_analysis(task, facts) if task.get('id') and task.get('version') else {}
    calculations = {row['id']: row for row in checked.get('results', [])}
    endpoint_gaps = checked.get('quote_interval_gaps', [])
    nav_gaps = checked.get('nav_interval_gaps', [])
    rows = value.get('logics', value.get('strategies', []))
    for index, item in enumerate(rows if isinstance(rows, list) else []):
        if not isinstance(item, dict):
            continue
        prefix = f'logics.{index}.'
        legacy_field = 'analysis' if 'analysis' in item else 'logic'
        legacy_analysis = text(item.get(legacy_field))
        argument = _written_argument(item)
        # The two forms are alternative ways of submitting the same argument.
        # Keep the original submission frozen and render one complete body.
        structured = (task.get(DRAFT_EDITORIAL_POLICY) == DRAFT_EDITORIAL_VERSION
                      and requested_organization in (None, 'research')
                      and all(key in argument for key in ('claim', 'evidence', 'reasoning')))
        parts = [(prefix+legacy_field, legacy_analysis)] if legacy_analysis and not structured else []
        for key, paragraph in argument.items():
            if '\n'+paragraph+'\n' not in '\n'+'\n'.join(value for _, value in parts)+'\n':
                parts.append((prefix+'argument.'+key, paragraph))
        analysis = '\n'.join(paragraph for _, paragraph in parts)
        if analysis:
            title = text(item.get('title'))
            if task.get(CLIENT_COPY_POLICY) in (CLIENT_COPY_PROSE_VERSION, CLIENT_COPY_VERSION):
                title = draft_logic_title(title, argument.get('claim'))
            logic = dict(title=title or '研究逻辑', analysis=analysis,
                implication=text(item.get('implication')), risk=text(item.get('risk')),
                status='待验证', workpaper_notes=notes(item.get('workpaper_notes',[])))
            if argument:
                logic['argument'] = argument
            if type(task.get('draft_tracking_version')) is int and task['draft_tracking_version'] == 1:
                proposed = item.get('tracking')
                try:
                    validate_tracking(proposed)
                except ContractError:
                    logic['workpaper_notes'].append('跟踪规则缺少指标、来源、窗口或判断条件。')
                else:
                    if proposed is not None:
                        logic['tracking'] = {key: value.strip() for key, value in proposed.items()}
            analysis_links = display_links(parts, analysis)
            implication_links = display_links([(prefix+'implication', logic['implication'])], logic['implication'])
            links = {key: binding for key, binding in [('analysis', analysis_links), ('implication', implication_links)] if binding}
            if detail := text(item.get('detail_analysis')):
                logic['detail_analysis'] = detail
                if binding := display_links([(prefix+'detail_analysis', detail)], detail):
                    links['detail_analysis'] = binding
            if links:
                logic['quantity_links'] = links
            for name, allowed in (('data_refs', fact_ids), ('source_refs', source_ids), ('calculation_refs', calculations)):
                refs = item.get(name, [])
                if not isinstance(refs, list):
                    refs = []
                logic[name] = list(dict.fromkeys(ref for ref in refs if isinstance(ref, str) and ref in allowed))
                if any(not isinstance(ref, str) or ref not in allowed for ref in refs):
                    logic.setdefault('reference_notes', []).append('部分引用未对应本任务资料，正文保留，待底稿补充。')
            logic['knowledge_refs'], knowledge_notes = knowledge.draft_references(task, item.get('knowledge_refs', []))
            if knowledge_notes:
                logic.setdefault('reference_notes', []).extend(knowledge_notes)
            mechanism=item.get('mechanism')
            if isinstance(mechanism,dict) and isinstance(mechanism.get('steps'),list):
                steps=[]
                for step in mechanism['steps']:
                    if not isinstance(step,dict) or not text(step.get('label')):continue
                    supplied=step.get('data_refs',[]) if isinstance(step.get('data_refs',[]),list) else []
                    refs=[ref for ref in supplied if isinstance(ref,str) and ref in fact_ids]
                    if any(not isinstance(ref,str) or ref not in fact_ids for ref in supplied):
                        logic.setdefault('reference_notes',[]).append('部分引用未对应本任务资料，正文保留，待底稿补充。')
                    if re.search(r'\d',step['label']) and not refs:
                        logic.setdefault('reference_notes',[]).append('示意标签中的数字未关联资料，未生成该路径图。');steps=[];break
                    kind = step.get('kind') if step.get('kind') in ('observation', 'hypothesis', 'condition') else 'hypothesis'
                    if kind == 'observation' and not refs:
                        kind = 'hypothesis'
                        logic.setdefault('reference_notes', []).append('未关联本任务数据的路径节点按假设展示。')
                    steps.append(dict(label=text(step['label']),data_refs=list(dict.fromkeys(refs)),kind=kind))
                if len(steps)>=2:logic['mechanism']=dict(steps=steps)
            logic['data_refs']=draft_logic_data_refs(logic)
            if index in logic_exclusions:
                excluded = logic_exclusions[index]
                logic['off_scope_data_refs'] = excluded
                inputs = set(logic['data_refs'])
                inputs.update(ref for ident in logic['calculation_refs'] for ref in calculations[ident]['data_refs'])
                if not inputs.isdisjoint(excluded):
                    logic.setdefault('reference_notes', []).append('部分引用与本节论证口径不一致，原值见完整数据。')
            if logic.get('reference_notes'):logic['reference_notes']=list(dict.fromkeys(logic['reference_notes']))
            logics.append(logic)
    from .draft_analysis import VALUATION_SCENARIO

    scenario_refs = {ref for calc in calculations.values() if calc.get('comparison_kind') == VALUATION_SCENARIO
                     and set(calc['data_refs']) <= fact_ids for ref in calc['data_refs']}
    organization = readout[2] if readout else requested_organization
    if compound:
        organization = compound_finance['kind'] if compound_finance else 'fact'
    elif direct_finance:
        kind = direct_finance['kind']
        organization = kind if kind == 'research' else organization or kind
    elif scenario_refs and organization is None:
        # A latest-price input belongs to the valuation research. It cannot
        # turn the entire report into a direct snapshot lookup.
        organization = 'research'
    if (local_readout or direct_market_title) and organization is None:
        organization = ('comparison' if direct_market_title and direct_market_title.endswith('对照') else 'fact')
    point_gaps = {(gap['code'], gap['window_start'], field)
                  for field, gaps in (('close', endpoint_gaps), ('unit_nav', nav_gaps))
                  for gap in gaps if gap['window_start'] == gap['window_end']}
    owned_observations = [row for row in facts if row['id'] in fact_ids and not row.get('previous_close_for')]
    from .index_sources import stated_weight_sheets

    weight_sheets = stated_weight_sheets(task)
    compound_sections = []
    compound_point_refs = set()
    if compound:
        from .market_series import NAV_INDICATOR
        from .subject_scope import subject_key

        financial_rows = direct_financial_observations(task, owned_observations, compound_finance) if compound_finance else []
        admitted_points = compound['points'] - point_gaps
        compound_point_refs = {row['id'] for row in owned_observations if (
            subject_key(row['code']).upper(), row['period'],
            'unit_nav' if row['field'] == NAV_INDICATOR else row['field']) in admitted_points}
        latest_refs = {ref for item in mixed_latest for ref in item['current_observation_refs'].values()}
        market_refs = compound_point_refs | latest_refs
        market_rows = [row for row in owned_observations if row['id'] in market_refs]
        weight_rows = [row for row in owned_observations if row['field'] == 'weight'] if compound.get('weights') else []
        economic_source_ids = {row['id'] for row in task.get('source_records', [])
                               if (row.get('source'), row.get('operation')) == ('ifind', 'edb_service')}
        economic_rows = ([row for row in owned_observations if row['artifact_id'] in economic_source_ids]
                         if compound.get('economics') else [])
        owned_observations = financial_rows + market_rows + economic_rows + weight_rows
        market_calculations = compound.get('market_calculations', set())
        for title, rows, requested in (('财务数据', financial_rows, set()),
                                       ('行情数据', market_rows, market_calculations),
                                       ('宏观数据', economic_rows, set())):
            refs = {row['id'] for row in rows if row['value'] is not None}
            if refs:
                comparisons = []
                for ident, calc in calculations.items():
                    inputs = set(calc['data_refs'])
                    if inputs and (organization == 'comparison' or rows is economic_rows) and inputs <= refs:
                        comparisons.append(ident)
                    elif (requested and inputs & refs and inputs <= fact_ids
                          and _market_calculation_scope(calc) in requested):
                        comparisons.append(ident)
                section, = _direct_outline(organization, [row['id'] for row in rows], comparisons, concise=True)
                compound_sections.append(dict(section, title=title))
        seen_weight_sections = set()
        for sheet in weight_sheets:
            member_positions = {position for position, _ in sheet['rows']}
            refs = tuple(row['id'] for row in weight_rows if row['artifact_id'] == sheet['source_ref']
                         and row.get('locator', {}).get('member') in member_positions)
            if not refs or refs in seen_weight_sections:
                continue
            seen_weight_sections.add(refs)
            results = [ident for ident, calc in calculations.items() if calc.get('comparison_kind') == 'index_weight_total'
                       and set(calc['data_refs']) == set(refs)]
            section, = _direct_outline('fact', list(refs), results, concise=True)
            rank = f'前{sheet["count"]}大' if sheet['count'] is not None else ''
            title = sheet['name']+rank+'成分权重'
            if len(weight_sheets) > 1:
                title += '（'+sheet['day']+'）'
            compound_sections.append(dict(section, title=title))
    elif direct_finance:
        owned_observations = direct_financial_observations(task, owned_observations, direct_finance)
    retained_observations = owned_observations
    if mixed_latest:
        from .market_series import NAV_INDICATOR

        # Recent questions use their own cutoff/session/source checks. Their
        # legacy readings still appear in the workpaper at the original dates.
        recent_fields = {(code, field) for scope in mixed_latest for code in scope['as_of_by_code']
                         for field in ([NAV_INDICATOR, 'unit_nav'] if scope['indicator'] == NAV_INDICATOR else [scope['indicator']])}
        retained_observations = [row for row in owned_observations
                                 if (row['code'], row['field']) not in recent_fields or row['id'] in compound_point_refs]
    usable_observations = any(row['value'] is not None for row in owned_observations)
    original_only = bool(original_excerpts) and not usable_observations
    excerpt_refs = {row['source_ref'] for row in original_excerpts}
    includes_uploads = bool(excerpt_refs) and any(
        item.get('id') in excerpt_refs and item.get('source') == '用户提供材料' for item in task.get('materials', []))
    data_outline = not logics and usable_observations
    submitted_conclusion = text(value.get('conclusion'))
    from .draft_readout import (etf_product_readout, fomc_readout, index_weight_readout, industry_collection_readout,
                                market_query_readout, peer_financial_readout, policy_transmission_sections, retained_argument_sections,
                                retained_readout, segment_attribution_readout, valuation_research_readout)
    from .provenance import collection_coverage
    collections = collection_coverage(task)
    raw_sources = {item['reference']['artifact_id'] for item in collections}
    analytical_observations = [row for row in retained_observations if row['artifact_id'] not in raw_sources]
    if point_gaps:
        from .market_series import NAV_INDICATOR
        from .subject_scope import subject_key

        analytical_observations = [row for row in analytical_observations if
            (subject_key(row['code']).upper(), row['period'], 'unit_nav' if row['field']==NAV_INDICATOR else row['field'])
            not in point_gaps]
    notes_for_readout = []
    policy_research = research_fomc_rate_request(task) if not parallel else None
    event = (fomc_readout(task, original_excerpts) if policy_research
             or not parallel and not submitted_conclusion and (not logics or original_excerpts) else None)
    policy_basis = bool(policy_research and event and event['links'])
    from .chain_readout import industry_chain_readout
    from .lpr_readout import lpr_readout
    from .macro_readout import macro_demand_readout, pmi_market_readout
    from .stock_quality_readout import stock_quality_readout
    observed_comparison = None
    valuation_readout = None
    if parallel:
        from .parallel_readout import study_readout

        observed_comparison = study_readout(task, analytical_observations, calculations.values(), original_excerpts)
    elif not logics and (not submitted_conclusion or editorial and data_outline):
        pmi_comparison = pmi_market_readout(task, analytical_observations, calculations.values())
        if pmi_comparison:
            direct_market_title = ''
        observed_comparison = (pmi_comparison
                               or market_query_readout(task, analytical_observations)
                               or stock_quality_readout(task, analytical_observations, calculations.values())
                               or macro_demand_readout(task, analytical_observations, calculations.values())
                               or lpr_readout(task, analytical_observations, calculations.values())
                               or industry_chain_readout(task, analytical_observations, calculations.values())
                               or etf_product_readout(task, analytical_observations, calculations.values())
                               or segment_attribution_readout(task, analytical_observations, calculations.values())
                               or peer_financial_readout(task, analytical_observations, calculations.values()))
    if observed_comparison is None and data_outline and not submitted_conclusion and scenario_refs:
        admitted = {row['id'] for row in analytical_observations}
        valuation_rows = [*analytical_observations, *(row for row in owned_observations if row['id'] in scenario_refs-admitted)]
        valuation_readout = valuation_research_readout(task, valuation_rows, calculations.values())
        observed_comparison = valuation_readout
    collection = (industry_collection_readout(collections)
                  if collections and not logics and not submitted_conclusion and not observed_comparison else None)
    retained_title, retained_conclusion, retained_links = '', '', []
    financial_quota = direct_financial_quota_reason(task) if direct_finance or compound_finance else ''
    if financial_quota:
        notes_for_readout.append(financial_quota)
    summary_rows = analytical_observations if data_outline else []
    if editorial and logics and not submitted_conclusion:
        # Existing arguments constrain a recovered summary to the evidence
        # those arguments actually used. Discard unrelated archive readings.
        cited = {ref for logic in logics for ref in logic['data_refs']}
        cited.update(ref for logic in logics for ident in logic['calculation_refs']
                     for ref in calculations[ident]['data_refs'])
        summary_rows = [row for row in analytical_observations if row['id'] in cited]
    if summary_rows and not readout and not submitted_conclusion:
        retained_title, retained_conclusion, retained_links = retained_readout(
            task, summary_rows, calculations.values(), nav_readings=checked.get('nav_point_readings', ()),
            index_names=index_names)
    if observed_comparison:
        retained_title, retained_conclusion = observed_comparison['title'], observed_comparison['conclusion']
        retained_links = observed_comparison.get('links', [])
        if not observed_comparison.get('workpaper_only_gaps'):
            notes_for_readout.extend(observed_comparison.get('gaps', ()))
    if readout and (event or collection) and not submitted_conclusion:
        retained_conclusion, retained_links = readout[1], readout[3]
    if event:
        offset = len(event['text'])
        retained_links = event['links'] + [dict(item, start=item['start'] + offset, end=item['end'] + offset)
                                          for item in retained_links]
        retained_conclusion = event['text'] + retained_conclusion
        from .event_market import requested_markets
        from .treasury_rates import requested_series as requested_treasury_series

        if policy_basis:
            markets = requested_markets(task)
            retained_title = ('美联储议息与美债期限结构' if requested_treasury_series(task) and not markets else
                              '美联储议息与美债及权益市场' if requested_treasury_series(task) else
                              '美联储议息与A股港股首日反应' if len(markets) == 2 else
                              'FOMC利率决议与港股估值' if '港股' in task['question'] else 'FOMC利率决议与资产定价')
        else:
            retained_title = event['title']
        notes_for_readout.extend(event['notes'])
    if collection:
        retained_conclusion += collection['text']
        retained_title = retained_title or collection['title']
    linked_weight_totals = set()
    for sheet in weight_sheets:
        weights = index_weight_readout(sheet, calculations.values())
        notes_for_readout.extend(weights['notes'])
        if not submitted_conclusion:
            totals = {ref for link in weights['links'] for ref in link['calculation_refs']}
            if totals and totals <= linked_weight_totals:
                continue
            linked_weight_totals.update(totals)
            offset = len(retained_conclusion)
            retained_links.extend(dict(item, start=item['start']+offset, end=item['end']+offset)
                                  for item in weights['links'])
            retained_conclusion += weights['text']
            retained_title = retained_title or weights['title']
            if not any(row['field'] != 'weight' for row in owned_observations) and not event and not collection:
                organization = organization or 'fact'
    if mixed_latest and not submitted_conclusion and not valuation_readout:
        cited_points = {ref for link in retained_links if not link.get('calculation_refs')
                        for ref in link['data_refs'] if ref in compound_point_refs}
        for scope in mixed_latest:
            reading_scope = dict(scope, missing_codes=[], unconfirmed_session_codes=[], suppress_missing_summary=True)
            if cited_points:
                reading_scope['current_observation_refs'] = {
                    code: ref for code, ref in scope['current_observation_refs'].items() if ref not in cited_points}
            _, reading, _, reading_links = _latest_readout(reading_scope, owned_observations)
            if not reading_links:
                continue
            if retained_conclusion and not retained_conclusion.endswith(('。', '！', '？', '\n')):
                retained_conclusion += '。'
            offset = len(retained_conclusion)
            retained_links.extend(dict(item, start=item['start']+offset, end=item['end']+offset) for item in reading_links)
            retained_conclusion += reading
    observed_sections = []
    pending_judgment = False
    pending_mechanism = False
    mechanism_title, mechanism_summary = '', ''
    if not logics:
        organization = organization or direct_scope
        explanations = []
        if not (submitted_conclusion or original_only or event or collection):
            scope = draft_research_scope(task) or {}
            if organization == 'mechanism' or (organization is None and scope.get('mode') == 'by_question'
                    and 'mechanism' in scope['allowed_report_scopes']):
                explanations = knowledge.draft_mechanisms(task)
                if explanations:
                    organization = 'mechanism'
        refs = [row['id'] for row in owned_observations]
        if (data_outline and not (observed_comparison or compound or event or collection)
                and (not mixed_latest or scenario_refs)
                and organization not in ('fact', 'comparison', 'mechanism')):
            observed_rows = list(analytical_observations)
            admitted = {row['id'] for row in observed_rows}
            observed_rows.extend(row for row in owned_observations if row['id'] in scenario_refs - admitted)
            observed_sections = retained_argument_sections(task, observed_rows, calculations.values())
        if observed_comparison:
            if parallel:
                organization = 'comparison'
            elif question_driven_arguments(task):
                organization = observed_comparison.get('organization') or organization or 'research'
            else:
                organization = 'research'
            logics = observed_sections = observed_comparison['sections']
        elif event and policy_research:
            organization = 'mechanism'
            logics = policy_transmission_sections(task, event, analytical_observations, calculations.values())
            from .event_market import event_reading_enabled

            market_summary = ''.join(item['event_session_summary'] for item in logics if item.get('event_session_summary'))
            if event_reading_enabled(task):
                retained_conclusion = market_summary + retained_conclusion
                retained_links = [dict(link, start=link['start']+len(market_summary), end=link['end']+len(market_summary))
                                  for link in retained_links]
            else:
                retained_conclusion += market_summary
            pending_mechanism = not market_summary
        elif original_only or (event and not usable_observations):
            organization = 'comparison' if event and organization == 'comparison' else 'fact'
        elif observed_sections:
            logics = observed_sections
            if question_driven_arguments(task):
                for index, logic in enumerate(logics, 1):
                    logic['display_heading'] = draft_logic_heading('comparison', index, logic['title'])
                mode = (draft_research_scope(task) or {}).get('mode')
                pending_judgment = not submitted_conclusion and (organization == 'research' or mode == 'research')
        elif compound:
            logics = compound_sections
        elif collection and organization in (None, 'fact', 'comparison'):
            organization = organization or 'fact'
            rest = [row['id'] for row in owned_observations if row['artifact_id'] not in raw_sources]
            if rest:
                logics = _direct_outline(organization, rest, calculations, concise=concise)
        elif organization in ('fact', 'comparison'):
            if usable_observations or not (concise or local_readout or direct_market_title):
                calculation_refs = calculations if data_outline else ()
                if direct_finance and data_outline:
                    allowed = set(refs)
                    calculation_refs = [ident for ident, calc in calculations.items() if allowed.issuperset(calc['data_refs'])]
                logics = _direct_outline(organization, refs, calculation_refs, concise=concise)
        elif organization == 'mechanism':
            pending_mechanism = not submitted_conclusion and not event and not collection
            logics, mechanism_summary = _mechanism_outline(explanations, summarize=not retained_conclusion and not readout)
            if len(logics) == 1:
                mechanism_title = logics[0]['title']
            if data_outline:
                observed = _direct_outline('fact', refs, calculations, concise=concise)
                observed[0]['display_heading'] = draft_logic_heading('fact', 1, observed[0]['title'])
                for index, logic in enumerate(logics, 1):
                    logic['display_heading'] = draft_logic_heading('mechanism', index, logic['title'])
                logics[:0] = observed
        else:
            pending_judgment = not submitted_conclusion
            if data_outline:
                logics = _direct_outline('fact', refs, calculations, concise=concise)
                logics[0]['display_heading'] = draft_logic_heading('fact', 1, logics[0]['title'])
    if organization in (None, 'research'):
        from .draft_readout import attach_valuation_scenario

        if scenario_refs:
            attach_valuation_scenario(logics, calculations.values())
        count = requested_strategy_count(task.get('question', ''))
        if count is not None and len(logics) > count:
            logics = logics[:count]
    explicit_original = re.search(r'(?:保留|列出|附上|逐字核对)(?:两次声明的|完整)?原文', task['question'])
    show_original_excerpts = bool(original_excerpts and (not policy_basis or explicit_original) and not observed_comparison)
    if show_original_excerpts:
        excerpt_title = '材料原文' if includes_uploads else 'FOMC声明原文' if event and event['links'] else '公开原文'
        logics.append(dict(title=excerpt_title, analysis='', implication='', risk='', status='待验证',
            document_excerpts=list(original_excerpts), data_refs=[], calculation_refs=[], knowledge_refs=[],
            source_refs=list(dict.fromkeys(row['source_ref'] for row in original_excerpts))))
    if task.get('draft_question_scope_version') == 2:
        for index, logic in enumerate(logics, 1):
            if 'display_heading' not in logic:
                logic['display_heading'] = draft_logic_heading(organization, index, logic['title'])
    risks = value.get('risks', [])
    risks = [text(item) for item in risks if text(item)] if isinstance(risks, list) else []
    if observed_comparison:
        risks.extend(observed_comparison.get('risks', ()))
    retained_subject = ''
    if (pending_judgment or pending_mechanism) and not task.get('objects'):
        from .evidence import declared_security_codes

        codes = declared_security_codes(task)
        if len(codes) == 1:
            retained_subject = codes[0] + ('影响分析' if pending_mechanism else '研究')
    compound_title = ''
    if compound:
        if compound_finance or directory_reports or compound.get('weights') or compound.get('economics'):
            from .evidence import declared_security_codes

            subjects = declared_security_codes(task)
            label = index_names.get(subjects[0], subjects[0]) if len(subjects) == 1 else ''
        if compound.get('weights') or compound.get('economics'):
            parts = []
            if compound.get('weights'):
                parts.append('成分权重')
            if directory_reports:
                parts.append('公告目录')
            if compound_finance:
                parts.append('财务')
            if compound['latest'] or compound['points']:
                parts.append('行情')
            if compound.get('economics'):
                parts.append('宏观数据')
            compound_title = label + '与'.join(parts) + '核对'
        elif directory_reports:
            parts = ['公告目录']
            if compound_finance:
                parts.append('财务')
            if compound['latest'] or compound['points']:
                parts.append('行情')
            compound_title = label + '与'.join(parts) + '核对'
        elif compound_finance:
            if compound['latest'] or compound['points']:
                compound_title = label + '财务与行情核对'
            else:
                compound_title = label + ('财务对照' if organization == 'comparison' else '财务核对')
        else:
            compound_title = _latest_report_title(mixed_latest, points=compound['points'],
                calculations=compound.get('market_calculations', ())) or '行情核对'
    title = (text(value.get('title')) or compound_title or direct_market_title or (readout[0] if readout else '')
             or (direct_finance['title'] if direct_finance and direct_finance['kind'] == 'research' else '')
             or (retained_title if event or collection or observed_comparison else '')
             or _retained_report_title(task, retained_observations, original_excerpts)
             or retained_title
             or (_dated_market_report_title(task) if organization in ('fact', 'comparison') else '')
             or (direct_finance['title'] if direct_finance else '')
             or retained_subject
             or mechanism_title
             or (task['objects'][0] + '研究' if task.get('objects') else '研究报告'))
    result = dict(title=title,
        logics=logics, risks=risks, knowledge_context=knowledge.draft_context(task),
        interpretation_context=interpretation_context(task,facts),
        workpaper_notes=notes(value.get('workpaper_notes',[])),
        reader_notes=notes(value.get('reader_notes',[])))
    if concise:
        result[DRAFT_READING_POLICY] = task[DRAFT_READING_POLICY]
    if editorial:
        result[DRAFT_EDITORIAL_POLICY] = task.get(DRAFT_EDITORIAL_POLICY, 1)
    if collections:
        result['collection_coverage'] = collections
    if directory_reports:
        result['filing_directories'] = [dict(report['filing_directory'], code=params['codes'],
            start_date=params['beginrDate'], end_date=params['endrDate']) for params, report in directory_reports]
        result['reader_notes'].extend(dict.fromkeys(
            note for _, report in directory_reports for note in report['reader_notes']))
    submitted_note_count = len(result['reader_notes'])
    result['reader_notes'].extend(notes_for_readout)
    from .market_calendar import market_date_reference
    if market_date_reference(task) == {}:
        result['reader_notes'].append('未能确定所指日期区间，请明确起止日期。')
    from .economic_sources import disconnected_economic_series, draft_period_summary
    period_notes, incomplete_series = draft_period_summary(task, facts)
    result['reader_notes'].extend(period_notes)
    if incomplete_series:
        result['incomplete_economic_series'] = incomplete_series
    if disconnected_series := disconnected_economic_series(task):
        result['disconnected_economic_series'] = disconnected_series
    from .market_calendar import quote_interval_enabled, stated_close_returns, unresolved_market_points
    if unresolved_market_points(task):
        result['reader_notes'].append('请明确每个指定日要核对的标的和指标。')
    return_dates = {(code, day.isoformat()) for code, day, _ in stated_close_returns(task)}
    for gap in endpoint_gaps:
        if gap['window_start'] == gap['window_end']:
            note = _market_point_gap(task, gap)
            if (gap['code'], gap['window_end']) in return_dates:
                note = note.removesuffix('。') + ('，无当日涨跌幅。' if gap.get('no_sessions') else '，日涨跌幅暂缺。')
            result['reader_notes'].append(note)
            continue
        days = '、'.join(gap['missing_close_dates'])
        if quote_interval_enabled(task):
            period = gap['window_start']+'至'+gap['window_end']
            if gap.get('no_sessions'):
                reason = '没有交易日'
            elif gap.get('calendar_unverified'):
                reason = '首末交易日及对应收盘读数未能核对'
            elif gap.get('conflicting'):
                reason = f'{days}的收盘读数不一致'
            elif gap.get('incompatible_basis'):
                reason = '两日收盘读数口径不同'
            elif gap.get('invalid_close'):
                reason = '收盘读数不适用于计算'
            else:
                reason = f'缺少{days}收盘读数'
            result['reader_notes'].append(f'{gap["code"]}在{period}{reason}，区间收盘涨跌幅暂缺。')
        else:
            result['reader_notes'].append(f'{gap["code"]}缺少{days}收盘读数，所请求区间的收盘涨跌幅暂缺。')
    from .draft_sources import quote_session_summary
    quote_notes, incomplete_quotes = quote_session_summary(task, facts, endpoint_gaps=endpoint_gaps)
    result['reader_notes'].extend(quote_notes)
    result['reader_notes'].extend(_close_return_gap(gap) for gap in checked.get('close_return_gaps', []))
    if session_calculation and not sessions:
        reason = '最近交易日尚未核实' if sessions is None else '回看窗口内无已结束的交易日'
        note = reason + '，' + '、'.join(session_calculations) + '暂缺。'
        if usable_observations:
            note += '可用读数按原日期列示。'
        result['reader_notes'].append(note)
    for gap in checked.get('daily_nav_premium_gaps', []):
        result['reader_notes'].append(f'{gap["period"]}，{gap["code"]}收盘折溢价率暂缺，同日可比收盘价或官方单位净值缺失。')
    if nav_gaps:
        from .draft_analysis import NAV_FIELDS
        nav_codes = {gap['code'] for gap in nav_gaps}
        incomplete_quotes.extend(dict(code=code, field=field) for code, field in sorted({
            (row['code'], row['field']) for row in facts if row['code'] in nav_codes and row['field'] in NAV_FIELDS}))
        for gap in nav_gaps:
            if gap['window_start'] == gap['window_end']:
                result['reader_notes'].append(_market_point_gap(task, gap, nav=True))
                continue
            days = '、'.join(gap['missing_dates'])
            reason = f'缺少{days}的可用单位净值' if days else '两端单位净值口径不同'
            period = gap['window_start']+'至'+gap['window_end']
            result['reader_notes'].append(f'{gap["code"]}在{period}{reason}，区间净值变化暂缺。')
    if incomplete_quotes:
        result['incomplete_quote_series'] = incomplete_quotes
    if checked.get('disconnected_closes'):
        result['disconnected_closes'] = checked['disconnected_closes']
    from .draft_sources import financial_period_scope
    period_scope=financial_period_scope(task,facts)
    if period_scope and period_scope['notes']:
        result['financial_period_scope']=period_scope
    mixed_notes = []
    if mixed_latest:
        from .draft_sources import stated_latest_notes
        for scope in mixed_latest:
            mixed_notes.extend(stated_latest_notes(dict(stated_latest_observation=scope)))
            for code in scope.get('unconfirmed_session_codes', ()):
                if session_calculation and code == session_calculation['code']:
                    continue
                outcome = ('，已取得读数按实际日期列示' if code in scope['current_observation_refs']
                           else '，本次未取得相应读数')
                mixed_notes.append(f'{code}的最近交易日尚未核实{outcome}。')
        mixed_notes = list(dict.fromkeys(mixed_notes))
        if mixed_notes:
            result['latest_observation_notes'] = dict(binding=binding, facts_hash=fingerprint(facts), notes=mixed_notes)
    gaps = list(dict.fromkeys(result['reader_notes'][submitted_note_count:]
                             + (period_scope or {}).get('notes', []) + mixed_notes))
    if gaps:
        result['readout_gaps'] = [note.removesuffix('。') for note in gaps] if local_readout else gaps
    if policy_basis and submitted_conclusion:
        offset = len(event['text'])
        conclusion = event['text'] + submitted_conclusion
        authored_links = display_links([('conclusion', submitted_conclusion)], submitted_conclusion)
        if authored_links:
            retained_links.extend(dict(link, start=link['start'] + offset, end=link['end'] + offset)
                                  for link in authored_links['links'])
    else:
        conclusion = submitted_conclusion or retained_conclusion or (readout[1] if readout else '') or mechanism_summary
    if local_readout and not conclusion and mixed_notes:
        conclusion = mixed_notes[0]
    if original_only and not conclusion:
        conclusion = '本次核对以留存材料的原文为依据。' if includes_uploads else '本次核对限于公开原文所载内容。'
    elif not conclusion:
        if gaps and (not usable_observations or mixed_notes and not retained_observations
                       or point_gaps and not analytical_observations):
            conclusion = gaps[0]
        elif organization in ('fact', 'comparison') and not directory_reports:
            conclusion = ('以下列示本次已取得的数据及适用口径。' if usable_observations else
                          '现有资料不足以完成本次核对，未取得的项目保留为待核对事项。')
        elif usable_observations:
            conclusion = '已取得的读数、比较结果及适用口径见下文。'
    if directory_reports:
        conclusion += ''.join(report['conclusion'] for _, report in directory_reports)
    result['conclusion'] = conclusion
    if type(task.get('draft_tracking_version')) is int and task['draft_tracking_version'] == 1:
        result['tracking_version'] = 1
    if any(record.get('id') in restricted and record.get('kind') in ('material', 'filing')
           for record in task.get('source_records', []) + task.get('materials', [])):
        result['reader_notes'].append('部分原件的历史可得性待核对，相关内容留在研究底稿。')
    if historical_adjustments or copy_adjustments:
        result['copy_adjustments'] = historical_adjustments + copy_adjustments
    if retained_links and (not submitted_conclusion or policy_basis):
        conclusion_links = dict(text_hash=fingerprint(result['conclusion']), links=retained_links)
    elif readout and readout[3] and task[STATED_LATEST_POLICY] >= 3 and not submitted_conclusion:
        conclusion_links = dict(text_hash=fingerprint(result['conclusion']), links=readout[3])
    else:
        conclusion_links = display_links([('conclusion', result['conclusion'])], result['conclusion'])
    if return_dates:
        from .subject_scope import subject_key

        links = list((conclusion_links or {}).get('links', ()))
        cited = {ident for link in links for ident in link['calculation_refs']}
        for calc in calculations.values():
            if (calc.get('comparison_kind') != 'previous_session' or calc['id'] in cited
                    or (subject_key(calc['code']).upper(), calc['window_end']) not in return_dates):
                continue
            lead = f'{calc["window_end"]}，{calc["code"]}'
            if conclusion.startswith(lead + '收盘') and conclusion.count('。') == 1 and conclusion.endswith('。'):
                conclusion = conclusion[:-1] + '，'
            else:
                if not conclusion.endswith(('。', '！', '？', '\n')):
                    conclusion += '。'
                conclusion += lead
            conclusion += '较上一交易日的涨跌幅为'
            number = calc['display']
            links.append(dict(start=len(conclusion), end=len(conclusion)+len(number), original=number,
                              code=calc['code'], date=calc['window_end'], data_refs=calc['data_refs'],
                              calculation_refs=[calc['id']]))
            conclusion += number + '%。'
        if links:
            result['conclusion'] = conclusion
            conclusion_links = dict(text_hash=fingerprint(conclusion), links=links)
    if pending_mechanism:
        boundary = '具体对象的实际影响需结合相应证据判断。'
        if not result['conclusion'] and not logics:
            boundary = ('本次未取得可核验的事件依据，所问事件及影响仍待核对。' if task.get('entry') == '事件'
                        else '现有资料不足以解释所问变化，成因及影响仍待核对。')
        if result['conclusion']:
            if result['conclusion'] in gaps:
                result['reader_notes'] = [note for note in result['reader_notes'] if note != result['conclusion']]
            ending = '' if result['conclusion'].endswith(('。', '！', '？')) else '。'
            result['conclusion'] += ending + boundary
        else:
            result['conclusion'] = boundary
        if conclusion_links:
            conclusion_links['text_hash'] = fingerprint(result['conclusion'])
    if pending_judgment and (market_date_reference(task) != {} or result['conclusion'] not in gaps):
        mode = (draft_research_scope(task) or {}).get('mode')
        judgment = '投资判断' if organization == 'research' or mode == 'research' else '研究判断'
        if result['conclusion']:
            if result['conclusion'] in gaps:
                result['reader_notes'] = [note for note in result['reader_notes'] if note != result['conclusion']]
            ending = '' if result['conclusion'].endswith(('。', '！', '？')) else '。'
            result['conclusion'] += ending + f'现有资料不足以形成{judgment}。'
        else:
            result['conclusion'] = f'本次未取得足以形成{judgment}的资料。'
        if conclusion_links:
            conclusion_links['text_hash'] = fingerprint(result['conclusion'])
    if conclusion_links:
        result['quantity_links'] = {'conclusion': conclusion_links}
    if latest:
        from .draft_sources import stated_latest_notes
        result['reader_notes'].extend(note for note in stated_latest_notes(coverage) if note not in result['conclusion'])
        if task[STATED_LATEST_POLICY] >= 3 and readout:
            from .draft_sources import stated_latest_gap_messages
            pending = stated_latest_gap_messages(latest)
            if session_calculation:
                pending = list(dict.fromkeys([*result.get('readout_gaps', ()), *pending]))
            else:
                pending.extend(f'{code}的最近交易日尚未核实' for code in latest.get('unconfirmed_session_codes', ()))
            if pending:
                result['readout_gaps'] = pending
    from .draft_visuals import ARGUMENT_VISUAL_SCOPE_VERSION, FILING_CAPTION_VERSION, VISUAL_LAYOUT_VERSION
    from .draft_filing_amounts import is_filing_amount
    from .visual_catalog import VISUAL_DESIGN_VERSION
    result['visual_layout_version']=VISUAL_LAYOUT_VERSION
    result['visual_design_version']=VISUAL_DESIGN_VERSION
    from .publication_content import READER_FORMAT_VERSION
    result['reader_format_version']=READER_FORMAT_VERSION
    result['argument_visual_scope_version']=ARGUMENT_VISUAL_SCOPE_VERSION
    if any(is_filing_amount(row) for row in owned_observations):
        result['filing_caption_version'] = FILING_CAPTION_VERSION
    from .draft_sources import request_field_context,observation_context
    observed_context=observation_context(task,facts)
    if observed_context:
        result['observation_context']=dict(version=1,task_id=task['id'],research_version=task['version'],
            input_hash=task.get('input_hash'),facts_hash=fingerprint(facts),**observed_context)
    request_context = request_field_context(task)
    if request_context:
        result['request_context'] = dict(version=1, task_id=task['id'], research_version=task['version'],
            input_hash=task.get('input_hash'), source_binding=source_binding(dict(task, input_hash=task.get('input_hash'))),
            request_hash=fingerprint([dict(id=row['id'], parameters=row.get('parameters'))
                for row in task.get('source_records', []) if row['id'] in {item['source_ref'] for item in request_context['notices']}]),
            **request_context)
    if organization:
        result['organization'] = organization
    if show_original_excerpts:
        result['reader_notes'].append('原文中的计划、估计和判断均按材料表述引用，实际影响仍需独立核对。'
            if includes_uploads else '原文中的计划、估计和判断均按原文引用。首发时点及实际影响以独立证据为准。')
    elif retained_data_only and not pending_judgment and not observed_comparison and not policy_basis:
        result['reader_notes'].append('本次依据已取得的数据整理，缺项及未验证判断仍待补充。')
    if data_outline:
        result['data_outline'] = True
    if observed_sections:
        result['observed_argument_sections'] = True
    if retained_data_only:
        result['retained_data_only'] = True
    return result


def compose(task):
    draft = task.get('draft_report')
    if not isinstance(draft, dict) or draft.get('source_binding') != source_binding(task):
        raise ContractError('报告对应的任务或原始资料已变化，请保存当前版本后重新生成')
    content = dict(schema='researchhelper.report-content', schema_version=1,
        report_kind='research_draft', task_id=task['id'], research_version=task['version'],
        cutoff=task['cutoff'], input_hash=task['input_hash'], title=draft['title'], question=task['question'],
        entry=task['entry'], intent=task['intent'], objects=task['objects'], synthetic=task['synthetic'],
        scope=(('公告目录核对。' if draft['filing_directory'].get('official') else 'iFinD公告目录核对。')
               if 'filing_directory' in draft
               else '研究草稿，原始取数与初步判断尚未完成独立复核。'),
        draft=copy.deepcopy(draft), questions=copy.deepcopy(task['questions']),
        evidence=copy.deepcopy(task['evidence']), calculations=copy.deepcopy(task['calculations']),
        theses=[], tracking=[], limitations=copy.deepcopy(task.get('gaps', [])),
        source_records=copy.deepcopy(task.get('source_records', [])),
        materials=copy.deepcopy(task.get('materials', [])),
        knowledge=copy.deepcopy(task.get('knowledge', {})), template_version='rh-draft-9' if type(task.get('draft_tracking_version')) is int and task['draft_tracking_version'] == 1 else 'rh-draft-8' if task.get('draft_question_scope_version') == 2 else 'rh-draft-7' if task.get('draft_question_scope_version') == 1 else 'rh-draft-6' if task.get('draft_direct_scope_version') == 1 else 'rh-draft-5',
        changes=task.get('changes'), update_from=task.get('update_from'),
        optional_findings=[], reasoning_state=copy.deepcopy(task.get('reasoning_state', {})))
    from .knowledge import CATALOGUE_POLICY, DRAFT_DEFINITION_POLICY, GENERAL_DEFINITION_SCOPE, draft_definitions_enabled
    if draft_definitions_enabled(task) and any(q.get('answer_scope') == GENERAL_DEFINITION_SCOPE for q in task['questions']):
        for key in (DRAFT_DEFINITION_POLICY, CATALOGUE_POLICY, 'definition_projection_version'):
            if key in task:
                content[key] = task[key]
    if 'draft_tracking_version' in task:
        content['draft_tracking_version'] = task['draft_tracking_version']
    if DRAFT_EDITORIAL_POLICY in draft or DRAFT_EDITORIAL_POLICY in task:
        content[DRAFT_EDITORIAL_POLICY] = draft.get(DRAFT_EDITORIAL_POLICY, task.get(DRAFT_EDITORIAL_POLICY))
    if 'draft_comparison_version' in task:
        content['draft_comparison_version'] = task['draft_comparison_version']
    if 'draft_client_copy_version' in task:
        content['draft_client_copy_version']=task['draft_client_copy_version']
    if 'original_quantity_qualifier_version' in task:
        content['original_quantity_qualifier_version'] = task['original_quantity_qualifier_version']
    if 'draft_disclosure_scope_version' in task:
        content['draft_disclosure_scope_version'] = task['draft_disclosure_scope_version']
    if 'market_observation_selection_version' in task:
        content['market_observation_selection_version']=task['market_observation_selection_version']
    if 'draft_quote_scope_version' in task:
        content['draft_quote_scope_version'] = task['draft_quote_scope_version']
    if 'draft_historical_daily_version' in task:
        content['draft_historical_daily_version'] = task['draft_historical_daily_version']
    if 'research_mode' in task:
        content['research_mode'] = task['research_mode']
    from .parallel_study import POLICY as PARALLEL_POLICY

    if PARALLEL_POLICY in task:
        content[PARALLEL_POLICY] = task[PARALLEL_POLICY]
    if 'draft_source_reader_version' in task:
        content['draft_source_reader_version'] = task['draft_source_reader_version']
    if 'draft_tinyshare_reader_version' in task:
        content['draft_tinyshare_reader_version'] = task['draft_tinyshare_reader_version']
    if 'draft_direct_scope_version' in task:
        content['draft_direct_scope_version'] = task['draft_direct_scope_version']
    if 'draft_question_scope_version' in task:
        content['draft_question_scope_version'] = task['draft_question_scope_version']
    if question_driven_arguments(task):
        content[QUESTION_ARGUMENT_POLICY] = task[QUESTION_ARGUMENT_POLICY]
    from .intraday_quotes import POLICY as INTRADAY_POLICY
    from .market_calendar import (LATEST_SESSION_CALCULATION_POLICY, LIQUIDITY_POLICY, POLICY as CALENDAR_POLICY,
                                  QUOTE_ENDPOINT_POLICY, freeze_calendar_replay, market_date_reference,
                                  quote_interval_enabled, stated_latest_session_calculation, stated_quote_enabled)
    from .market_series import BATCH_POLICY, NAV_INTERVAL_POLICY, NAV_SOURCE_POLICY, STATED_LATEST_POLICY
    from .economic_sources import CATALOGUE_READOUT_POLICY, DRAFT_PERIOD_POLICY, economic_period_scope
    from .index_sources import DRAFT_WEIGHT_POLICY, PROFILE_POLICY, weight_readout_enabled
    if weight_readout_enabled(task):
        for key in (DRAFT_WEIGHT_POLICY, PROFILE_POLICY, 'official_index_sources_version'):
            if key in task:
                content[key] = task[key]
        if 'security_declaration_encoding' in task:
            content['security_declaration_encoding'] = task['security_declaration_encoding']
        if 'subject_scope' in task:
            content['subject_scope'] = copy.deepcopy(task['subject_scope'])
    if economic_scope := economic_period_scope(task):
        content[DRAFT_PERIOD_POLICY] = task[DRAFT_PERIOD_POLICY]
        content['economic_discovery_version'] = task['economic_discovery_version']
        content['draft_acquisition_coverage'] = economic_scope
    for policy in (QUOTE_ENDPOINT_POLICY, CATALOGUE_READOUT_POLICY, INTRADAY_POLICY, LIQUIDITY_POLICY):
        if policy in task:
            content[policy] = task[policy]
    if (referenced_window := market_date_reference(task)) is not None:
        content['market_date_reference'] = copy.deepcopy(referenced_window)
    if NAV_INTERVAL_POLICY in task:
        content[NAV_INTERVAL_POLICY] = task[NAV_INTERVAL_POLICY]
        if BATCH_POLICY in task:
            content[BATCH_POLICY] = task[BATCH_POLICY]
    if NAV_SOURCE_POLICY in task:
        content[NAV_SOURCE_POLICY] = task[NAV_SOURCE_POLICY]
    from .draft_analysis import (DAILY_NAV_PREMIUM_METHOD, PEER_NET_AFTER_GROSS, PEER_PROFIT_POLICY,
                                 VALUATION_ATTRIBUTION, VALUATION_POSITION, VALUATION_SCENARIO)
    from .market_series import VALUATION_ATTRIBUTION_POLICY, VALUATION_HISTORY_POLICY, valuation_history_scope
    kinds = {calc.get('comparison_kind') for calc in draft.get('calculations', ())}
    if any(calc['method'] == PEER_NET_AFTER_GROSS for calc in draft.get('calculations', ())):
        content[PEER_PROFIT_POLICY] = task[PEER_PROFIT_POLICY]
    if kinds.intersection((VALUATION_POSITION, VALUATION_ATTRIBUTION, VALUATION_SCENARIO)):
        from .evidence import current_query_reference, current_snapshot_reference
        from .market_calendar import CALENDAR_EVENT_FIELDS

        history_scope = valuation_history_scope(task)
        dispatches = task['ledger']['dispatches']
        readers = {'date_sequence': current_query_reference}
        if VALUATION_ATTRIBUTION in kinds:
            readers['cmd_history_quotation'] = current_snapshot_reference
            content[VALUATION_ATTRIBUTION_POLICY] = task[VALUATION_ATTRIBUTION_POLICY]
        if VALUATION_SCENARIO in kinds:
            content[VALUATION_HISTORY_POLICY] = task[VALUATION_HISTORY_POLICY]
        content['valuation_history_replay'] = dict(scope=history_scope, created_at=task['created_at'], events={
            row['id']: {key: dispatches[row['acquisition']['dispatch_index']][key] for key in CALENDAR_EVENT_FIELDS}
            for row in task['source_records'] if row.get('source') == 'ifind' and row.get('operation') in readers
            and history_scope['question_id'] in row.get('question_ids', ()) and readers[row['operation']](task, row)})
    premium_refs = {ref for calc in draft.get('calculations', []) if calc['method'] == DAILY_NAV_PREMIUM_METHOD
                    for ref in calc['data_refs']}
    if premium_refs:
        from .etf_sources import POLICY as ETF_PCF_POLICY
        from .market_calendar import CALENDAR_EVENT_FIELDS

        source_ids = {row['artifact_id'] for row in draft['facts'] if row['id'] in premium_refs}
        events = task['ledger']['dispatches']
        content['daily_nav_premium_sources'] = dict(created_at=task['created_at'], events={
            record['id']: {key: events[record['acquisition']['dispatch_index']][key] for key in CALENDAR_EVENT_FIELDS}
            for record in task['source_records'] if record['id'] in source_ids})
        for key in ('daily_nav_date_roles_version', ETF_PCF_POLICY):
            content[key] = task[key]
    from .etf_comparison import CELL_KIND as ETF_INTERIM_CELL, HOLDING_POLICY, comparison_scope as etf_comparison_scope
    from .etf_trading import CELL_KIND as ETF_TRADING_CELL, DAILY, NAV_HISTORY, POLICY as ETF_TRADING_POLICY
    etf_holding = task.get(HOLDING_POLICY) == 1 and (etf_comparison_scope(task) or {}).get('holding')
    if etf_holding or any(row.get('locator', {}).get('kind') in (ETF_INTERIM_CELL, ETF_TRADING_CELL)
                          for row in draft['facts']):
        from .etf_listing import OPERATION as ETF_LISTING, POLICY as ETF_LISTING_POLICY
        from .evidence import current_query_reference
        from .market_calendar import CALENDAR_EVENT_FIELDS

        events = task['ledger']['dispatches']
        content['etf_trading_replay'] = dict(created_at=task['created_at'], events={
            record['id']: {key: events[record['acquisition']['dispatch_index']][key] for key in CALENDAR_EVENT_FIELDS}
            for record in task['source_records'] if record.get('source') == 'official'
            and record.get('operation') in (ETF_LISTING, DAILY, NAV_HISTORY, 'etf_pcf')
            and current_query_reference(task, record)})
        for key in (ETF_TRADING_POLICY, ETF_LISTING_POLICY, 'official_source_expansion_version',
                    'security_declaration_encoding', 'subject_scope'):
            if key in task:
                content[key] = copy.deepcopy(task[key])
        if etf_holding:
            content[HOLDING_POLICY] = task[HOLDING_POLICY]
    if STATED_LATEST_POLICY in task:
        content[STATED_LATEST_POLICY] = task[STATED_LATEST_POLICY]
        content['question_origin'] = task.get('question_origin')
    if 'security_declaration_encoding' in task and stated_quote_enabled(task):
        content['security_declaration_encoding'] = task['security_declaration_encoding']
    if 'subject_scope' in task and stated_quote_enabled(task):
        content['subject_scope'] = copy.deepcopy(task['subject_scope'])
    from .event_market import READING_POLICY as EVENT_READING_POLICY, event_reading_enabled
    from .monthly_statistics import POLICY as MONTHLY_POLICY, RETAIL_STUDY, study_scope as monthly_scope
    from .stock_quality import (CELL_KIND as STOCK_QUALITY_CELL, COMPANY_SCOPE_VERSION,
                                OPEN_COMPANY_POLICY, POLICY as STOCK_QUALITY_POLICY, STRATEGY_POLICY as STOCK_STRATEGY_POLICY)

    if event_reading_enabled(task) and any(item.get('event_session_summary') or item.get('event_observation_only')
                                           for item in draft['logics']):
        content[EVENT_READING_POLICY] = task[EVENT_READING_POLICY]
    from .treasury_rates import FIELD as H15_FIELD, POLICY as H15_POLICY, study_scope as treasury_scope

    if any(row['field'] == H15_FIELD for row in draft['facts']) and (scope := treasury_scope(task)):
        content[H15_POLICY] = task[H15_POLICY]
        content['fomc_treasury_scope'] = scope
    from .lpr_rates import FIELD as LPR_FIELD, POLICY as LPR_POLICY, study_scope as lpr_scope

    if any(row['field'] == LPR_FIELD for row in draft['facts']) and (scope := lpr_scope(task)):
        content[LPR_POLICY] = task[LPR_POLICY]
        content['lpr_study'] = scope

    if STOCK_QUALITY_POLICY in task and any(row.get('locator', {}).get('kind') == STOCK_QUALITY_CELL for row in draft['facts']):
        content[STOCK_QUALITY_POLICY] = task[STOCK_QUALITY_POLICY]
        if task.get(STOCK_STRATEGY_POLICY) == 1:
            content[STOCK_STRATEGY_POLICY] = 1
        if task.get(OPEN_COMPANY_POLICY) == COMPANY_SCOPE_VERSION:
            content[OPEN_COMPANY_POLICY] = COMPANY_SCOPE_VERSION
    if (scope := monthly_scope(task)) and scope.get('study') == RETAIL_STUDY:
        from .official_sources import freeze_catalogue_root_dispatch

        content['monthly_retail_scope'] = scope
        for policy in (MONTHLY_POLICY, 'official_channels_version', 'official_catalogue_pagination_version'):
            if policy in task:
                content[policy] = task[policy]
        if dispatch := freeze_catalogue_root_dispatch(task):
            content['monthly_catalogue_dispatch'] = dispatch
    latest_session_scope = stated_latest_session_calculation(task, for_report=True)
    if latest_session_scope or task.get(QUOTE_ENDPOINT_POLICY) == 1 or quote_interval_enabled(task) or any(
            row.get('session_basis', {}).get('kind') == 'exchange_calendar' for row in draft.get('calculations', [])):
        replay = freeze_calendar_replay(task)
        if replay:
            content['calendar_replay'] = replay
            if latest_session_scope:
                content[CALENDAR_POLICY] = task[CALENDAR_POLICY]
                content[LATEST_SESSION_CALCULATION_POLICY] = task[LATEST_SESSION_CALCULATION_POLICY]
    content['content_hash'] = fingerprint(content)
    return content


def _chart_period(value):
    """A display label and precision for a literal observation period."""
    day = _sequence_day(value)
    if day is not None:
        return day.isoformat(), 'day'
    month = re.fullmatch(r'([0-9]{4})-?(0[1-9]|1[0-2])', value) if isinstance(value, str) else None
    if month and int(month[1]) > 0:
        return '-'.join(month.groups()), 'month'
    return str(value or ''), None


def draft_figures(facts, *, incomplete_economic_series=(), incomplete_quote_series=()):
    """Choose actual series or cross-sections; keep gaps and independent bases."""
    facts = [row for row in facts if not row.get('previous_close_for')]
    groups = defaultdict(list)
    multiple_objects = len({row['code'] for row in facts if row.get('axis_role', 'time') == 'time'}) > 1
    for row in facts:
        role = row.get('axis_role', 'time')
        groups[(row.get('series_basis', ''), row['field'], row['unit'], role,
                row['period'] if role == 'cross_section' else row['code'],
                row.get('artifact_id') if role=='cross_section' else None)].append(row)
    specs = []
    for (_, field, unit, role, _, _), rows in groups.items():
        if role == 'cross_section':
            points = [row for row in rows if type(row['value']) in (int, float) and math.isfinite(row['value'])]
            if len(points) < 2:
                continue
            points = sorted(points, key=lambda row: row['value'], reverse=True)[:15]
            specs.append(dict(name=rows[0]['label'] + ('，' + rows[0]['period'] if rows[0]['period'] else '')
                + ('，前15项' if len(rows) > 15 else ''), field=field,
                unit=unit, dates=[row.get('object_label') or row['code'] for row in points],
                values=[row['value'] for row in points], horizontal=True, type='bar',
                data_refs=[row['id'] for row in points],axis_role='cross_section',
                context=rows[0]['period'],series_basis=rows[0].get('series_basis',''),source_ref=rows[0].get('artifact_id'),
                original_rows=len(rows)))
            continue
        periods = {value: _chart_period(value) for value in {row['period'] for row in rows}}
        def label_for(row):
            return periods[row['period']][0]

        by_date = defaultdict(list)
        for row in rows:
            by_date[label_for(row)].append(row)
        # Identical overlapping observations can share a plotted point. A
        # conflicting revision remains in an independent source series.
        conflict = any(len({row['value'] for row in batch}) > 1 for batch in by_date.values())
        batches = defaultdict(list)
        if conflict:
            for row in rows:
                batches[row['artifact_id']].append(row)
        else:
            batches['same_basis'] = [batch[0] for batch in by_date.values()]
        for values in batches.values():
            if len(values) < 2 or len({label_for(row) for row in values}) != len(values):
                continue
            if sum(type(row['value']) in (int, float) and math.isfinite(row['value']) for row in values) < 2:
                continue
            values = sorted(values, key=label_for)
            precision = {periods[row['period']][1] for row in values}
            temporal = len(precision)==1 and None not in precision
            title = ((values[0].get('object_label') or values[0]['code']) if multiple_objects and field!='value' else '') + values[0]['label']
            code = values[0]['code']
            incomplete_market_path = (code, field) in incomplete_quote_series
            connected = (temporal and not incomplete_market_path
                         and not (field == 'value' and code in incomplete_economic_series))
            specs.append(dict(name=title, field=field, unit=unit,
                dates=[label_for(row) for row in values], values=[row['value'] for row in values],
                horizontal=False, type='line' if len(values) >= 3 and connected else 'bar', original_rows=len(rows),
                incomplete_market_path=incomplete_market_path,
                data_refs=[row['id'] for row in values]))
    for index, spec in enumerate(specs):
        spec['target'] = 'draft-chart-' + str(index + 1)
        if spec['unit'] == '原始单位待核对':
            spec['unit'] = '接口原值'
    return specs


def _figure_keywords(spec):
    aliases = {
        'ths_revenue_stock': ['收入', '营收'], 'ths_operating_cost_stock': ['成本'],
        'ths_np_stock': ['净利润', '净利率'], 'ths_np_atoopc_stock': ['归母', '归属母公司'],
        'ths_ncf_from_oa_stock': ['现金流', '现金兑现'],
        'close': ['股价', '收盘', '价格走势', '指数走势'], 'unit_nav': ['净值'], 'weight': ['权重', '成分'],
    }
    return spec.get('keywords') or aliases.get(spec['field'], [spec['name']])


def logic_basis(content, item):
    """Resolve optional draft references only inside this frozen task/version."""
    ident = content.get('task_id', content.get('id'))
    version = content.get('research_version', content.get('version'))
    records = {row['id']: row for row in content.get('source_records', []) + content.get('materials', [])
               if row.get('task_id') == ident and row.get('research_version') == version}
    facts = (content.get('draft') or content.get('draft_report') or {}).get('facts', [])
    calculations=(content.get('draft') or content.get('draft_report') or {}).get('calculations',[])
    refs=set(draft_logic_data_refs(item))|{ref for value in calculations if value['id'] in item.get('calculation_refs',[]) for ref in value['data_refs']}
    selected = [row for row in facts if row.get('id') in refs
                and row.get('artifact_id') in records]
    sources = set(item.get('source_refs', [])) | {row['artifact_id'] for row in selected}
    for row in selected:
        sources.update(citation.get('artifact_id') for citation in row.get('unit_reference',{}).get('citations',[])
            if citation.get('artifact_id') in records and citation.get('snapshot_hash')==fingerprint(records[citation['artifact_id']].get('snapshot')))
    return selected, [row for key, row in records.items() if key in sources]


def logic_knowledge(content, item):
    """Resolve explicit same-version knowledge for reading, never as company facts."""
    from .knowledge import ResearchKnowledge
    context = (content.get('draft') or {}).get('knowledge_context', {})
    package = content.get('knowledge') or {}
    if not isinstance(context, dict) or not isinstance(package, dict):
        return []
    expected = dict(task_id=content.get('task_id', content.get('id')),
        research_version=content.get('research_version', content.get('version')),
        input_hash=content.get('input_hash'), knowledge_hash=fingerprint(package))
    if context.get('binding') != expected:
        return []
    references, _ = ResearchKnowledge().draft_references(content, item.get('knowledge_refs', []))
    selected = {ref['id'] for ref in references}
    return [entry for entry in package.get('entries', []) if entry['id'] in selected]


def render(content, resources, visual_profile=None, report_profile='standard', *, figure_plan=None):
    """Use the existing client-paper style; workpaper diagnostics stay outside."""
    from .market_calendar import DAILY_CLOSE_RETURN_VERSIONS, QUOTE_ENDPOINT_POLICY
    from .reports import check_content, research_date_label, READER_VERSION
    from .visual import inspection_link, inspection_surface, knowledge_sources, INSPECTION_SCRIPT
    check_content(content)
    draft = content['draft']
    concise = draft_reading_enabled(draft)
    escape = lambda value: html.escape(str(value), quote=True)
    paragraph = lambda value: ''.join('<p>' + escape(line) + '</p>' for line in str(value).splitlines() if line.strip())
    facts = draft['facts']
    scoped_points = content.get(QUOTE_ENDPOINT_POLICY) in DAILY_CLOSE_RETURN_VERSIONS
    visible_facts = [row for row in facts if not row.get('previous_close_for')] if scoped_points else facts
    source_labels = publication_source_labels(content)
    internal = report_profile == 'detailed'
    risks_at_end = draft_risks_at_end(content)
    from .event_market import asks_first_event_session, event_reading_enabled
    from .tinyshare_sources import analysis_field

    event_endpoint_report = (event_reading_enabled(content) and asks_first_event_session(content) and facts
                             and all(analysis_field(row) == 'close' for row in facts)
                             and any(row.get('comparison_kind') == 'event_close_return'
                                     for row in draft.get('calculations', [])))
    from .draft_sources import (CLIENT_COPY_MARKET_LEVEL_VERSION, CLIENT_COPY_POLICY, CLIENT_COPY_PROSE_VERSION,
                                CLIENT_COPY_VERSIONS, CLIENT_COPY_VERSION)
    from .publication_content import client_research_text
    project_paper = (not internal and content.get(CLIENT_COPY_POLICY) in (CLIENT_COPY_PROSE_VERSION, CLIENT_COPY_VERSION)
                     and draft.get('origin') in ('model', 'host'))
    protect_amounts = project_paper and content.get(CLIENT_COPY_POLICY) == CLIENT_COPY_VERSION
    editorial = not internal and draft_editorial_enabled(content)
    from .draft_visuals import (UNKNOWN_FINANCIAL_UNITS, financial_peer_tables, logic_figure_plan,
                               _calculation_dependencies, _observed_series_rows, _market_row_key,
                               _observed_financial_rows, _financial_row_key)
    from .visual_selection import ledger_html
    chart_specs = figure_plan if figure_plan is not None else logic_figure_plan(content, report_profile=report_profile)
    drawn = set()
    inspection_records = []
    displayed_data = {ref for spec in chart_specs if spec.get('coverage_kind') != 'mechanism'
                      for ref in spec.get('data_refs', [])}
    graphed_changes = {ref for spec in chart_specs if spec.get('kind') == 'changes'
                       for ref in spec.get('calculation_refs', [])}

    quantity_targets = set()
    has_quantity_links = draft.get('quantity_links') or any(item.get('quantity_links') for item in draft['logics'])
    show_calculation_table = draft.get('data_outline') or draft.get('retained_data_only')
    needs_dependencies = has_quantity_links or bool(draft.get('calculations'))
    originals = _observed_series_rows(content) if needs_dependencies else set()
    dependencies = _calculation_dependencies(content, originals) if needs_dependencies else {}
    financial_originals = None
    facts_by_id = {row['id']: row for row in facts}
    from .draft_analysis import FINANCIAL
    research_paper = not internal and draft.get('organization') not in ('fact', 'comparison')
    withheld_amounts = ({row['id'] for row in facts if analysis_field(row) in FINANCIAL
                         and observation_unit(row) in UNKNOWN_FINANCIAL_UNITS}
                        if protect_amounts or editorial and research_paper else set())
    published_calculations = {ref for spec in chart_specs if spec.get('coverage_kind') != 'mechanism'
                              for ref in spec.get('calculation_refs', ())}
    periods = {_sequence_day(row['period']) for row in facts} if not internal and draft.get('observed_argument_sections') else set()
    client_financial = (len(periods) == 2 and None not in periods and
        all(analysis_field(row) in FINANCIAL for row in facts) and
        len({(row.get('code'), row.get('series_basis')) for row in facts}) == 1)
    if client_financial:
        older, newer = sorted(periods)
        client_financial = newer.year == older.year + 1 and (newer.month, newer.day) == (older.month, older.day)
    unified_amounts = (client_financial and draft.get('origin') == 'data_outline' and
        dependencies and all(row.get('conditional_equal_scale') for row in dependencies.values()) and
        draft['conclusion'].startswith('若两期单位一致，'))
    cited_in_summary = set()
    official_nav_sources = {row['id']: row for row in content.get('source_records', [])
                            if row.get('source') == 'official' and row.get('operation') == 'etf_pcf'
                            and row.get('task_id') == content['task_id']
                            and row.get('research_version') == content['research_version']}
    from .draft_filing_amounts import verified_filing_locations
    from .draft_sources import _draft_original_scope
    from .etf_comparison import frozen_cell_locations
    from .industry_chain import CELL_KIND as INDUSTRY_CELL_KIND
    from .monthly_statistics import CELL_KIND as MONTHLY_CELL_KIND
    filing_locations = verified_filing_locations(content, facts, _draft_original_scope(content))
    etf_locations = frozen_cell_locations(content, facts) if internal else {}
    from .stock_quality import CELL_KIND as STOCK_QUALITY_CELL_KIND, frozen_locations as frozen_stock_locations

    stock_locations = frozen_stock_locations(content, facts) if internal else {}
    from .publication_content import READER_FORMAT_VERSION
    format_version = draft.get('reader_format_version')
    readable_format = type(format_version) is int and format_version == READER_FORMAT_VERSION

    def field_links(item, field):
        bindings = item.get('quantity_links')
        return bindings.get(field) if isinstance(bindings, dict) else None

    def disclosure_caption(record, fallback):
        if content.get('draft_disclosure_scope_version') != 1:
            return fallback, ''
        from .source_catalogue import disclosure_fields
        fields = disclosure_fields(record)
        if not fields:
            return fallback, ''
        digest = record.get('snapshot_hash', record.get('hash'))
        if not digest or fingerprint(record['snapshot']) != digest:
            return fallback, ''
        issuer = ' '.join(fields[key] for key in ('issuer_name', 'issuer_code') if key in fields)
        date = ('目录日期' + fields['directory_date'] if 'directory_date' in fields
                else '目录定位日期' + fields['path_date_hint'] if 'path_date_hint' in fields else '')
        return fields.get('title', fallback), '，'.join(part for part in (issuer, date) if part)

    def document_basis(location, *, literal=False):
        from .draft_sources import disclosure_excerpt_hash
        from .numeric_integrity import DRAFT_CLAUSE_BOUNDARY

        if not isinstance(location, dict) or not isinstance(location.get('source_ref'), str):
            return None
        ref = location['source_ref']
        records = [row for row in content['source_records'] + content.get('materials', []) if row.get('id') == ref]
        if (len(records) != 1 or records[0].get('task_id') != content['task_id']
                or records[0].get('research_version') != content['research_version']):
            return None
        record = records[0]
        snapshot = record.get('snapshot', record)
        if (literal or 'snapshot_hash' in location) and (
                location.get('snapshot_hash') != record.get('snapshot_hash', record.get('hash'))
                or location.get('snapshot_hash') != fingerprint(snapshot)):
            return None
        material = snapshot.get('material', snapshot) if isinstance(snapshot, dict) else None
        if not isinstance(material, dict) or not isinstance(material.get('parts'), list):
            return None
        parts = material['parts']
        index, start, end = (location.get(key) for key in ('part_index', 'start', 'end'))
        if (type(index) is not int or not 0 <= index < len(parts)
                or not isinstance(parts[index], dict) or not isinstance(parts[index].get('text'), str)
                or type(start) is not int or type(end) is not int or not 0 <= start < end <= len(parts[index]['text'])):
            return None
        part = parts[index]
        if not literal and end < len(part['text']) and not DRAFT_CLAUSE_BOUNDARY.match(part['text'], end):
            return None
        name = material.get('name')
        if disclosure_excerpt_hash(ref, name, index, part.get('locator'), start, end, part['text'][start:end]) != location.get('excerpt_hash'):
            return None
        locator = part.get('locator')
        if isinstance(locator, dict):
            locator = f'第{locator["page"]}页' if locator.get('page') else ''
        if not isinstance(locator, str) or not locator.strip():
            return None
        title, description = disclosure_caption(record, name or source_label(record) or '留存资料')
        if literal:
            # Legacy task snapshots may predate the richer citation captions.
            from .source_catalogue import disclosure_fields
            title = disclosure_fields(record).get('title', title)
            if publisher := source_labels.get(ref):
                description = '，'.join(filter(None, (publisher, description)))
        return dict(title='材料原文' if record.get('source') == '用户提供材料' else '披露原文',
                    document=location, source_name=title,
                    locator='，'.join(part for part in (description, locator) if part),
                    excerpt=part['text'][start:end])

    def document_inspection(target, basis):
        return ('<section id="' + target + '" data-inspection-title="披露原文"><h3>'
                + escape(basis['source_name']) + '</h3><p class="figure-source">' + escape(basis['locator'])
                + '</p>' + paragraph(basis['excerpt']) + '</section>')

    def retained_original_paragraphs(item):
        paragraphs = []
        for location in item.get('document_excerpts', []):
            basis = document_basis(location, literal=True)
            if not basis:
                continue
            target = 'draft-original-' + fingerprint(location)[:16]
            inspection_records.append(document_inspection(target, basis))
            label = basis['source_name'] + '，' + basis['locator']
            paragraphs.append('<p><q>' + escape(basis['excerpt']) + '</q></p><p class="figure-source">'
                + inspection_link(target, label, '查看' + label + '的原文定位') + '</p>')
        return ''.join(paragraphs)

    def original_series_value(row):
        if row.get('source') in ('ifind', 'tinyshare'):
            return _market_row_key(row) in originals
        if row.get('source') != 'official' or row.get('field') != 'unit_nav':
            return False
        from .etf_sources import validate_snapshot
        record = official_nav_sources.get(row['artifact_id'])
        if record is None:
            return False
        try:
            original, = validate_snapshot(record, content['cutoff'])['data']
        except (ContractError, KeyError, TypeError, ValueError):
            return False
        return (row['code'], row['period'], row['raw_value'], row['unit'], row['locator']) == (
                original['ts_code'], original['trade_date'], original['unit_nav'], '元',
                dict(row=0, field='unit_nav'))

    def quantity_basis(link):
        nonlocal financial_originals
        if link.get('kind') == 'document_excerpt':
            return document_basis(link.get('document'))
        if link.get('kind') == 'statement_readout':
            return document_basis(link.get('document'), literal=True)
        refs = link.get('data_refs')
        calc_refs = link.get('calculation_refs')
        if not isinstance(refs, list) or not isinstance(calc_refs, list):
            return None
        if len(calc_refs) == 1 and isinstance(calc_refs[0], str):
            calc = dependencies.get(calc_refs[0])
            if link.get('kind') == 'index_weight_total':
                from .draft_analysis import WEIGHT_TOTAL_METHOD
                if (not calc or calc['method'] != WEIGHT_TOTAL_METHOD or calc['data_refs'] != refs
                        or fingerprint({key: value for key, value in calc.items() if key != 'rows'})
                        != link.get('calculation_hash')):
                    return None
                return dict(title=calc['label']+'计算依据', data_refs=refs, calculation_refs=calc_refs,
                            source_refs=[calc['rows'][0]['artifact_id']])
            from .draft_analysis import (PE_ATTRIBUTION_METHODS, PE_EARNINGS_SCENARIO, VALUATION_ATTRIBUTION,
                                         VALUATION_POSITION, VALUATION_POSITION_METHOD, VALUATION_SCENARIO)
            valuation_methods = {VALUATION_POSITION: (VALUATION_POSITION_METHOD,),
                                 VALUATION_ATTRIBUTION: PE_ATTRIBUTION_METHODS,
                                 VALUATION_SCENARIO: (PE_EARNINGS_SCENARIO,)}
            if link.get('kind') in valuation_methods:
                if (not calc or calc['method'] not in valuation_methods[link['kind']] or calc['data_refs'] != refs
                        or fingerprint({key: value for key, value in calc.items() if key != 'rows'})
                        != link.get('calculation_hash')
                        or not all(original_series_value(row) for row in calc['rows'])):
                    return None
                if link['kind'] == VALUATION_SCENARIO and link['original'] != calc['display'].lstrip('-')+'%':
                    return None
                return dict(title=calc['label'], data_refs=refs, calculation_refs=calc_refs,
                            period_heading='日期')
            if link.get('kind') == 'economic_points':
                if (not calc or calc['method'] != 'raw_percentage_points_v1' or calc['data_refs'] != refs
                        or fingerprint({key: value for key, value in calc.items() if key != 'rows'})
                        != link.get('calculation_hash')
                        or any(_market_row_key(row) not in originals for row in calc['rows'])):
                    return None
                return dict(title=calc['rows'][-1]['label']+'两期读数差', data_refs=refs,
                            calculation_refs=calc_refs, comparison_roles=dict(zip(refs, ('基期', '本期'))))
            if link.get('kind') == 'daily_nav_premium':
                from .draft_analysis import DAILY_NAV_PREMIUM_METHOD

                if (not calc or calc['method'] != DAILY_NAV_PREMIUM_METHOD or calc['data_refs'] != refs
                        or calc['period'] != link.get('date') or calc['code'] != link.get('code')
                        or fingerprint({key: value for key, value in calc.items() if key != 'rows'})
                        != link.get('calculation_hash') or not all(original_series_value(row) for row in calc['rows'])):
                    return None
                return dict(title='收盘折溢价率计算依据', data_refs=refs, calculation_refs=calc_refs,
                            comparison_roles=dict(zip(refs, ('收盘价', '单位净值'))), period_heading='日期')
            if link.get('kind') == 'financial_yoy':
                if (not calc or calc['method'] != 'raw_change_v1' or calc.get('comparison_kind')
                        or calc['data_refs'] != refs
                        or fingerprint({key: value for key, value in calc.items() if key != 'rows'})
                        != link.get('calculation_hash')):
                    return None
                if financial_originals is None:
                    financial_originals = _observed_financial_rows(content)
                if any(_financial_row_key(row) not in financial_originals for row in calc['rows']):
                    return None
                return dict(title=calc['rows'][-1]['label']+'同比计算依据', data_refs=refs, calculation_refs=calc_refs,
                            comparison_roles=dict(zip(refs, ('基期', '本期'))))
            if link.get('kind') == 'series_change':
                if (not calc or calc['method'] != 'raw_change_v1' or calc.get('comparison_kind')
                        or calc['data_refs'] != refs
                        or fingerprint({key: value for key, value in calc.items() if key != 'rows'})
                        != link.get('calculation_hash')
                        or not all(original_series_value(row) for row in calc['rows'])):
                    return None
                return dict(title=calc['rows'][-1]['label']+'两期读数变化', data_refs=refs,
                            calculation_refs=calc_refs, comparison_roles=dict(zip(refs, ('起点', '终点'))))
            if (calc and calc.get('comparison_kind') == 'previous_session' and calc['data_refs'] == refs
                    and calc['code'] == link.get('code') and calc['window_end'] == link.get('date')):
                basis = dict(title='日涨跌幅计算依据', data_refs=refs, calculation_refs=calc_refs,
                             source_refs=calc['session_basis']['source_refs'])
                if scoped_points:
                    basis.update(comparison_roles=dict(zip(refs, ('上一交易日', '指定日'))), period_heading='日期')
                return basis
        elif not calc_refs and len(refs) == 1 and isinstance(refs[0], str):
            from .draft_analysis import _day
            row = facts_by_id.get(refs[0], {})
            if link.get('kind') in ('financial_level', 'economic_level'):
                if row.get('value') is None or fingerprint(row) != link.get('row_hash'):
                    return None
                if link['kind'] == 'financial_level':
                    if financial_originals is None:
                        financial_originals = _observed_financial_rows(content)
                    if _financial_row_key(row) not in financial_originals:
                        return None
                elif row.get('field') != 'value' or _market_row_key(row) not in originals:
                    return None
                return dict(title=row['label']+'原始读数', data_refs=refs)
            if link.get('kind') == 'observed_readout':
                from .publication_content import observation_presentation
                if row.get('value') is None or row.get('previous_close_for') or fingerprint(row) != link.get('row_hash'):
                    return None
                if observation_presentation(row)['value'] != link.get('original'):
                    return None
                if original_series_value(row):
                    return dict(title=row['label']+'原始读数', data_refs=refs)
                return None
            if link.get('kind') == 'filing_amount':
                if refs[0] in filing_locations and fingerprint(row) == link.get('row_hash'):
                    return dict(title=row['label']+'原表', data_refs=refs, source_refs=[row['artifact_id']])
                return None
            date = _day(row.get('period'))
            if link.get('kind') == 'market_level':
                if (row.get('value') is not None and not row.get('previous_close_for')
                        and row.get('code') == link.get('code') and date and date.isoformat() == link.get('date')
                        and fingerprint(row) == link.get('row_hash') and original_series_value(row)):
                    return dict(title=row['label']+'原始读数', data_refs=refs)
                return None
            if (row.get('source') == 'tinyshare' and row.get('source_operation') in ('daily', 'fund_daily')
                    and row.get('field') == 'pct_chg' and row.get('unit') == '%' and row.get('code') == link.get('code')
                    and date and date.isoformat() == link.get('date') and _market_row_key(row) in originals):
                return dict(title='披露涨跌幅', data_refs=refs)
        return None

    def prose_space(before, after):
        return ' ' if before[-1:].isascii() and after[:1].isascii() else ''

    def public_prose(value, rows, binding=None, *, charted_lines=(), section=None, separate_lines=False,
                     named_calculations=None, named_observations=None):
        from .numeric_integrity import _DRAFT_QUANTITY
        from .publication_content import draft_quantity_presentation
        value = str(value)
        retained = []
        if (isinstance(binding, dict) and binding.get('text_hash') == fingerprint(value)
                and isinstance(binding.get('links'), list)):
            for link in binding['links']:
                if not isinstance(link, dict):
                    continue
                start, end = link.get('start'), link.get('end')
                if (type(start) is int and type(end) is int and 0 <= start < end <= len(value)
                        and value[start:end] == link.get('original') and (basis := quantity_basis(link))):
                    retained.append(dict(link, label=link['original'], basis=basis))
        if project_paper or editorial:
            value, retained = client_research_text(value, retained, hide_unverified_amounts=protect_amounts,
                                                   editorial=editorial)
        market_level_links = content.get(CLIENT_COPY_POLICY) in CLIENT_COPY_VERSIONS[
            CLIENT_COPY_VERSIONS.index(CLIENT_COPY_MARKET_LEVEL_VERSION):]
        paragraphs = []
        previous_line = ''
        offset = 0

        def display_text(text, cursor):
            if not unified_amounts or cursor:
                return escape(text)
            if section == 'summary':
                prefix = f"若两期单位一致，{facts[0]['code']}合并报表{newer}报告期，"
                if text.startswith(prefix):
                    text = '合并口径下' + text[len(prefix):]
            elif section == 'logic':
                text = text.removeprefix('若所用原值单位一致，')
                for prefix in (f'{newer}报告期较{older.year}年同期，', f'{newer}较{older.year}年同期，'):
                    if text.startswith(prefix):
                        text = ('' if previous_line else '较上年同期，') + text[len(prefix):]
                        break
                text = text.replace('报表净利润率', '净利润率').replace('经营现金流/净利润倍数', '经营现金流净额与净利润之比')
                text = text.replace('经营现金流净额上升，净利润下降。现金流与利润变动方向相反。',
                                    '经营现金流净额上升，净利润下降。')
            return escape(text)

        for segment in value.splitlines(keepends=True):
            line = segment.splitlines()[0]
            start, offset = offset, offset + len(segment)
            if not line.strip():
                previous_line = ''
                continue
            if line.strip() in charted_lines:
                continue
            pieces = []; cursor = 0
            links = [dict(item, start=item['start']-start, end=item['end']-start) for item in retained
                     if start <= item['start'] < item['end'] <= start+len(line)]
            if market_level_links:
                # Reuse display rounding only when the same text and original
                # cell have already passed point-in-time citation checks.
                for link in links:
                    if link.get('kind') != 'market_level':
                        continue
                    row = facts_by_id[link['data_refs'][0]]
                    presentations = draft_quantity_presentation(line, [row])
                    match = next((item for item in presentations if (item['start'], item['end'])
                                  == (link['start'], link['end'])), None)
                    if match:
                        link['label'] = match['label']
            else:
                links.extend(draft_quantity_presentation(line, rows))
            links.sort(key=lambda link: (link['start'], -link['end']))
            for index, item in enumerate(links):
                if item['start'] < cursor:
                    continue
                basis = item.get('basis')
                identity = dict(refs=item['data_refs'], original=item['original'])
                if basis:
                    identity['calculations'] = basis.get('calculation_refs', [])
                    if 'document' in basis:
                        identity['document'] = basis['document']
                target = 'draft-quantity-' + fingerprint(identity)[:16]
                if target not in quantity_targets:
                    if basis and 'document' in basis:
                        inspection_records.append(document_inspection(target, basis))
                    else:
                        selected, quantity_sources = logic_basis(content, basis or dict(data_refs=item['data_refs']))
                        if not selected:
                            continue
                        inspection_records.append(basis_record(target, basis or dict(title=selected[0]['label']), selected, quantity_sources))
                    quantity_targets.add(target)
                pieces.append(display_text(line[cursor:item['start']], cursor))
                # Some bindings cover only the number. Keep any adjacent unit
                # in its visual box without extending the evidence binding.
                stop = links[index+1]['start'] if index+1 < len(links) else len(line)
                quantity = _DRAFT_QUANTITY.match(line, item['start'])
                end = item['end']
                suffix = ''
                if quantity and quantity.end('number') == end < quantity.end() <= stop:
                    suffix = line[end:quantity.end()]
                    pieces.append('<span class="quantity-with-unit">')
                purpose = '查看' + basis['title'] if basis else '查看原始读数与来源'
                calculation_refs = (basis or {}).get('calculation_refs', [])
                if client_financial and calculation_refs:
                    item['label'] = client_financial_value(item['label'])
                    if section == 'summary':
                        cited_in_summary.update(calculation_refs)
                valuation_attributes = ((' data-original-display="' + escape(item['original'])
                    + '" title="原值：' + escape(item['original']) + '"')
                    if not basis or item['original'] != item['label'] else '')
                refs = item['data_refs']
                reading_target = ('calculation:' + calculation_refs[0] if len(calculation_refs) == 1 else
                                  'observation:' + refs[0] if not calculation_refs and len(refs) == 1 else '')
                evidence_attributes = ''
                if len(calculation_refs) == 1:
                    published_calculations.update(calculation_refs)
                    if named_calculations is not None:
                        named_calculations.update(calculation_refs)
                    evidence_attributes = (' data-calculation-ref="' + escape(calculation_refs[0])
                                           + '" data-calculation-inputs="' + escape(' '.join(refs)) + '"')
                elif basis and reading_target.startswith('observation:'):
                    if named_observations is not None:
                        named_observations.add(refs[0])
                    evidence_attributes = ' data-observation-id="' + escape(refs[0]) + '"'
                pieces.append('<a class="quantity-link" href="#' + target + '" aria-haspopup="dialog"'
                    + (' data-quantity-basis="' + escape(reading_target) + '"' if internal and reading_target else '')
                    + evidence_attributes + valuation_attributes + ' aria-label="' + escape(item['label'] + '，' + purpose)
                    + '">' + escape(item['label']) + '</a>')
                if suffix:
                    pieces.append(escape(suffix) + '</span>')
                cursor = end + len(suffix)
            pieces.append(display_text(line[cursor:], cursor))
            inline = ''.join(pieces).strip()
            if previous_line and not separate_lines:
                paragraphs[-1] += prose_space(previous_line, line.lstrip()) + inline
            else:
                paragraphs.append(inline)
            previous_line = line.rstrip()
        return paragraphs

    def prose_markup(paragraphs, *, measured_tail=False):
        result = ''.join('<p>' + paragraph + '</p>' for paragraph in paragraphs[:-1])
        if paragraphs:
            attribute = ' class="measured-reading"' if measured_tail else ''
            result += '<p' + attribute + '>' + paragraphs[-1] + '</p>'
        return result

    from .publication_content import financial_scope_labels
    scope_labels=financial_scope_labels(content,facts)
    def scoped_label(row):return row['label']+('，'+scope_labels[row['id']] if row['id'] in scope_labels else '')

    def observation_reading(rows, target):
        from .publication_content import observation_presentation, reader_period_label, observation_value

        parts = []
        for row in rows:
            shown = (observation_presentation(row) if report_profile == 'standard' and readable_format
                     else dict(period=reader_period_label(row['period']), value=observation_value(row),
                               unit=observation_unit(row)))
            unconfirmed_amount = (internal and analysis_field(row) in FINANCIAL
                                  and shown['unit'] in UNKNOWN_FINANCIAL_UNITS)
            raw_reading = not internal and shown['unit'] in ('未确认', '原始单位待核对', '接口原值')
            label = ' '.join(dict.fromkeys(filter(None, (row.get('object_label') or row.get('code'),
                                                       shown['period'], scoped_label(row)))))
            value = inspection_link(target, shown['value'], '查看' + row['label'] + '的原始数据',
                                    observation_ref=row['id'])
            lead, unit = '为', shown['unit']
            if unconfirmed_amount:
                lead, unit = '原值为', '（单位待核对）'
            elif raw_reading:
                lead, unit = '原始读数为', ''
            parts.append('<span data-observation-id="' + escape(row['id']) + '">' + escape(label)
                         + lead + value + escape(unit) + '</span>')
        return '<p class="measured-reading">' + '，'.join(parts) + '。</p>'

    def observation_table(rows, target):
        # A measured value remains useful even when it cannot form a chart.
        # Use the original cell, date and unit; never interpolate another point.
        from .draft_visuals import display_observation
        from .publication_content import observation_value,reader_period_label,observation_period_heading
        cells = []
        readable = report_profile == 'standard' and readable_format
        for row in rows:
            if readable:
                from .publication_content import observation_presentation
                shown = observation_presentation(row)
            else:
                value = observation_value(row) if report_profile == 'detailed' else display_observation(row)
                shown = dict(value=value, period=reader_period_label(row['period']), unit=observation_unit(row))
            value = inspection_link(target, shown['value'], '查看' + row['label'] + '的原始数据',
                                    observation_ref=row['id'])
            cells.append('<tr data-observation-id="' + escape(row['id']) + '"><th scope="row">'
                + escape(row['code']) + '</th><td>' + escape(scoped_label(row)) + '</td><td>'
                + escape(shown['period'] or '未标注') + '</td><td>' + value + '</td><td>'
                + escape(shown['unit']) + '</td></tr>')
        value_heading = '读数' if readable else '原值'
        return ('<div class="report-data-scroll argument-observations" tabindex="0" role="region" aria-label="观测数据">'
            '<table><thead><tr><th>标的</th><th>指标</th><th>'+escape(observation_period_heading(rows,'日期'))+'</th><th>' + value_heading + '</th><th>单位</th></tr></thead>'
            '<tbody>' + ''.join(cells) + '</tbody></table></div>')

    def peer_table(table, target):
        from .draft_visuals import FIELDS
        from .publication_content import observation_raw_unit, observation_value

        title = '财务读数对照，' + table['statement']
        headers = '<th scope="col">指标</th>' + ''.join(
            '<th scope="col">'+escape(code)+' <span>'+period.isoformat()+'</span></th>'
            for code, period in table['columns'])
        lines, unknown = [], False
        for field, observations in table['rows'].items():
            cells = []
            for row in observations:
                if row is None:
                    cells.append('<td>未取得</td>')
                    continue
                value, unit = observation_value(row), observation_raw_unit(row)
                if row['value'] is not None:
                    literal = re.fullmatch(r'([+-]?)([0-9]{4,})(\.[0-9]+)?', value)
                    if literal:
                        digits = re.sub(r'\B(?=(?:[0-9]{3})+$)', ',', literal[2])
                        value = literal[1] + digits + (literal[3] or '')
                    if unit in ('', '原始单位待核对', '接口原值', '原值'):
                        value += '*'
                        unknown = True
                    else:
                        value += unit
                label = '查看'+row['code']+FIELDS[field]+row['period']+'的原值'
                link = inspection_link(target, value, label, observation_ref=row['id'])
                cells.append('<td data-observation-id="'+escape(row['id'])+'">'+link+'</td>')
            lines.append('<tr><th scope="row">'+escape(FIELDS[field])+'</th>'+''.join(cells)+'</tr>')
        note = '<p class="reader-basis">*单位待核对，按接口原值列示。</p>' if unknown else ''
        return ('<div class="report-data-scroll argument-observations" tabindex="0" role="region" aria-label="'+escape(title)+'">'
                '<table class="financial-peer-table"><caption>'+escape(title)+'</caption><thead><tr>'+headers
                +'</tr></thead><tbody>'+''.join(lines)+'</tbody></table>'+note+'</div>')

    def basis_record(target, item, rows, records, knowledge_entries=None):
        from .publication_content import reader_period_label, unit_basis_table
        comparison = item.get('comparison_roles')
        if comparison:
            order = list(comparison)
            rows = sorted(rows, key=lambda row: order.index(row['id']))

        def original_cells(row):
            period = row['period']
            cells = [row['code'], scoped_label(row), period,
                     '未返回' if row['value'] is None else row.get('raw_value', row['value']), observation_unit(row)]
            if row.get('qualifier') == 'lower_exclusive':
                cells[3] = '超过' + str(cells[3])
            if comparison:
                cells[2] = comparison[row['id']] + '，' + reader_period_label(period)
            return cells

        cells = ''.join('<tr data-observation-id="'+escape(row['id'])+'"><td>' + '</td><td>'.join(escape(value) for value in
            original_cells(row)) + '</td></tr>' for row in rows)
        period_heading = item.get('period_heading', '报告期') if comparison else '期间'
        headings = '<th>标的</th><th>指标</th><th>' + escape(period_heading) + '</th><th>原值</th><th>单位</th>'
        body = ('<div class="table-wrap" tabindex="0"><table><thead><tr>' + headings
            + '</tr></thead><tbody>' + cells + '</tbody></table></div>') if rows else ''
        if comparison and rows:
            sources_by_label = {}
            for row in rows:
                label = source_labels.get(row['artifact_id'], source_label(row.get('source'))) or '留存资料'
                sources_by_label.setdefault(label, []).append(comparison[row['id']])
            sources = (next(iter(sources_by_label)) if len(sources_by_label) == 1 else
                       '、'.join('和'.join(roles) + ' ' + label for label, roles in sources_by_label.items()))
            body += '<p class="figure-source">来源：' + escape(sources) + '</p>'
        unit_rows=unit_basis_table(rows,records)
        if unit_rows:
            body+='<h3>单位依据</h3><div class="table-wrap" tabindex="0"><table><thead><tr>'+''.join('<th>'+escape(value)+'</th>' for value in unit_rows[0])+'</tr></thead><tbody>'
            body+=''.join('<tr>'+''.join('<td>'+escape(value)+'</td>' for value in values)+'</tr>' for values in unit_rows[1:])+'</tbody></table></div>'
        if knowledge_entries is None and item.get('knowledge_refs'):
            knowledge_entries = logic_knowledge(content, item)
        for entry in knowledge_entries or ():
            body += '<h3>'+escape(entry['title'])+'</h3>'+paragraph(entry['content'])
            body += paragraph('适用条件：'+entry.get('conditions',''))
            body += paragraph('反例与相反渠道：'+entry.get('counterexample',''))
            body += '<div class="figure-source">'+knowledge_sources(entry)+'</div>'
        from .draft_analysis import (formula_mathml, GROSS_BRIDGE_INPUTS, GROSS_BRIDGE_METHODS, METHODS,
                                     PEER_NET_AFTER_GROSS, PEER_PROFIT_INPUTS, WEIGHT_TOTAL_METHOD)
        calendar_dates = {}
        for calculation in draft.get('calculations',[]):
            if calculation['id'] not in item.get('calculation_refs',[]):continue
            symbols = dict(zip(('a', 'b'), (role+'原值' for role in comparison.values()))) if comparison else None
            body+='<h3>'+escape(calculation_title(calculation))+'</h3>'+formula_mathml(calculation['method'], labels=symbols)
            body+='<p>结果：'+escape(calculation['display'])+escape(calculation['unit'])+'</p>'
            if not comparison and calculation['method'] != WEIGHT_TOTAL_METHOD:
                input_names = {}
                if calculation['method'] in GROSS_BRIDGE_METHODS:
                    input_names = dict(zip('abcd', GROSS_BRIDGE_INPUTS, strict=True))
                elif calculation['method'] == PEER_NET_AFTER_GROSS:
                    input_names = dict(zip('abcdef', PEER_PROFIT_INPUTS, strict=True))
                body+='<p>'+escape('，'.join(input_names.get(key, key)+'='+str(value)
                                          for key,value in calculation['inputs'].items()))+'</p>'
            body+=paragraph(calculation.get('condition') or METHODS[calculation['method']]['condition'])
            if calculation.get('conditional_equal_scale'):body+='<p>原始单位未明确，比较以各输入尺度一致为前提。</p>'
            if calculation.get('comparison_kind') == 'previous_session':
                for source_ref in calculation.get('session_basis', {}).get('source_refs', []):
                    calendar_dates.setdefault(source_ref, set()).update((calculation['window_start'], calculation['window_end']))
        # A document reference supplies attribution. Only bound excerpts
        # belong in the report, while the entire original stays in ResDesk.
        for record in records:
            name = source_labels.get(record.get('id'),source_label(record.get('source'))) or '留存资料'
            snapshot = record.get('snapshot', record)
            material = snapshot.get('material', snapshot) if isinstance(snapshot, dict) else {}
            if comparison and not material.get('parts') and record['id'] not in calendar_dates:
                continue
            title, disclosure = disclosure_caption(record, material.get('name') or name)
            body += '<h3>' + escape(title) + '</h3>'
            if disclosure:
                body += '<p class="figure-source">' + escape(disclosure) + '</p>'
            if record['id'] in calendar_dates:
                body += paragraph('核对交易日：' + '、'.join(sorted(calendar_dates[record['id']])))
            locations = [location for row in rows if row['artifact_id'] == record['id']
                         and (location := filing_locations.get(row['id']))]
            etf = [etf_locations[row['id']] for row in rows
                   if row['artifact_id'] == record['id'] and row['id'] in etf_locations]
            bulletin_parts = material.get('parts', ())
            bulletins = [row['locator'] for row in rows if row['artifact_id'] == record['id']
                         and row.get('locator', {}).get('kind') in (INDUSTRY_CELL_KIND, MONTHLY_CELL_KIND)] if internal else []
            stock = [stock_locations[row['id']] for row in rows
                     if row['artifact_id'] == record['id'] and row['id'] in stock_locations]
            if locations or etf or bulletins or stock:
                quotes = {}
                for proof in bulletins:
                    index = proof['part_index']
                    if 0 <= index < len(bulletin_parts):
                        part = bulletin_parts[index]
                        if (part['locator'] == proof['part']
                                and part['text'][proof['start']:proof['end']] == proof['quote']):
                            quotes.setdefault(proof['part'], dict())[proof['quote']] = None
                for location in locations:
                    quotes.setdefault(location['part'], dict())[location['table_header']] = None
                    for proof in (location, location['unit_basis'], location['period_basis'], location['statement_basis']):
                        quotes.setdefault(proof['part'], dict())[proof['quote']] = None
                    note = location['statement_basis']
                    if note.get('unit_quotes'):
                        quotes[note['part']][note['header']] = None
                        quotes[note['part']].update(dict.fromkeys(note['unit_quotes']))
                for location in etf:
                    for proof in (location, location['product'], location['reporting_period'], location['benchmark'],
                                  location.get('manager_discussion')):
                        if proof:
                            quotes.setdefault(proof['part'], dict())[proof['quote']] = None
                for location in stock:
                    proofs = [location, location['statement_basis'], location['period_basis'], location['unit_basis'],
                              *location['unit_basis'].get('references', ()), location.get('management_explanation')]
                    for proof in proofs:
                        if proof:
                            quotes.setdefault(proof['part'], dict())[proof['quote']] = None
                for locator, lines in quotes.items():
                    body += '<p class="figure-source">' + escape(locator) + '</p>'
                    body += ''.join(paragraph(line) for line in lines)
        notes=item.get('workpaper_notes',[])+item.get('reference_notes',[]) if internal else []
        if item.get('risk') and not (risks_at_end or (internal and draft.get('observed_argument_sections') is True)):
            notes = [item['risk'], *notes]
        if notes:body+='<h3>判断边界</h3>'+''.join(paragraph(note) for note in notes)
        return '<section id="' + target + '" data-inspection-title="' + escape(item['title']) + '"><h3>研究依据</h3>' + body + '</section>'

    def figure(spec, *, scale_note=''):
        drawn.add(spec['target'])
        height = spec.get('height',400 if spec['horizontal'] else 250)
        caption=escape(spec['name'])
        target=None
        if spec.get('document_groups'):
            target = 'draft-figure-basis-' + spec['target']
            groups = []
            for group in spec['document_groups']:
                groups.append('<h3>' + escape(group['period']) + '</h3>' + paragraph(group['line'])
                    + '<p class="figure-source"><a href="' + escape(group['url'])
                    + '" target="_blank" rel="noopener noreferrer">上海证券交易所周度统计</a></p>')
            inspection_records.append('<section id="' + target + '" data-inspection-title="' + caption
                                      + '">' + ''.join(groups) + '</section>')
            caption = inspection_link(target, spec['name'], '查看' + spec['name'] + '的图表依据')
        elif spec.get('calculation_refs') or spec.get('data_refs') or spec.get('coverage_kind')=='mechanism':
            rows,sources=logic_basis(content,spec)
            target='draft-figure-basis-'+spec['target']
            inspection_records.append(basis_record(target,dict(spec,title=spec['name']),rows,sources))
            caption=inspection_link(target,spec['name'],'查看'+spec['name']+'的图表依据')
        description = ' aria-describedby="' + escape(scale_note) + '"' if scale_note else ''
        from .draft_analysis import _day
        period_label=lambda value:(_day(value).isoformat() if _day(value) else str(value))
        context=escape(spec.get('context') or (period_label(spec['period'])+'较'+period_label(spec['comparison']) if spec.get('comparison') else ''))
        if context:context='<p class="figure-context">'+context+'</p>'
        plot='<div class="chart" id="'+spec['target']+'" style="height:'+str(height)+'px" tabindex="0" role="img" aria-label="'+escape(spec['name'])+'图，按回车查看数据依据"></div>'
        display=ledger_html(spec,plot,target) if report_profile=='detailed' or spec.get('auto_visual',{}).get('reason')=='single_session_ohlc' else plot
        return '<figure'+description+'><figcaption>'+caption+'</figcaption>'+context+display+'</figure>'

    def visible_calculation_title(row):
        title = calculation_title(row, include_scope=False)
        if not internal and row['method'] == 'raw_change_v1':
            title = title.replace('归属于母公司所有者的净利润', '归母净利润')
            title = title.replace('经营活动产生的现金流量净额', '经营现金流净额')
            if title.endswith('原值同比变化'):
                title = title.removesuffix('原值同比变化') + '同比'
        if client_financial and row['method'] == 'raw_multiple_v1':
            title = title.replace('经营现金流/报表净利润原值比', '经营现金流净额与净利润之比')
        if not internal:
            title = title.replace('收入成本原值差占比', '收入成本差占营业收入比重')
            title = title.replace('报表净利润/收入原值比', '净利润占营业收入比重')
        scopes = {scope_labels.get(ref) for ref in row['data_refs']}
        if len(scopes) == 1 and None not in scopes:
            title = next(iter(scopes)) + ' ' + title
        return title

    def calculation_table(results, target, conditional, scale_note):
        from .publication_content import calculation_period

        codes = {row.get('code') or '' for row in results}
        periods = {calculation_period(row) for row in results}
        common_code = next(iter(codes)) if len(codes) == 1 else ''
        common_period = next(iter(periods)) if len(periods) == 1 else ''
        show_code = len(codes) > 1
        show_period = len(periods) > 1
        context = '，'.join(filter(None, (common_code, common_period)))
        caption = '<caption>' + escape(context) + '</caption>' if context else ''
        headers = ['指标'] + (['标的'] if show_code else []) + (['期间'] if show_period else []) + ['结果']
        cells = []
        for row in results:
            value = row['display'] + ('' if row['unit'] == '原始单位待核对' else row['unit'])
            refs = ' '.join(row['data_refs'])
            limited = row['id'] in conditional
            result = inspection_link(target, value, value + '，查看' + calculation_title(row) + '的计算依据',
                                     observation_ref=refs, described_by=scale_note if limited else None)
            metric = '<th scope="row">' + escape(visible_calculation_title(row)) + '</th>'
            subject = '<td>' + escape(row.get('code') or '未标注') + '</td>' if show_code else ''
            period = '<td class="calculation-period">' + escape(calculation_period(row) or '未标注') + '</td>' if show_period else ''
            cells.append('<tr data-calculation-ref="' + escape(row['id']) + '" data-calculation-inputs="' +
                         escape(refs) + '">' + metric + subject + period + '<td>' + result + '</td></tr>')
        return ('<div class="report-data-scroll argument-calculations" tabindex="0" role="region" aria-label="计算结果">'
                '<table class="calculation-results-table">' + caption + '<thead><tr>' +
                ''.join('<th scope="col">' + label + '</th>' for label in headers) +
                '</tr></thead><tbody>' + ''.join(cells) + '</tbody></table></div>')

    def calculation_reading(results, target, conditional, scale_note, *, compact=False):
        from .publication_content import calculation_period

        def reading(row, label, presented=None):
            value = row['display'] + ('' if row['unit'] == '原始单位待核对' else row['unit'])
            if client_financial:
                value = client_financial_value(value) if presented is None else presented
            limited = row['id'] in conditional
            refs = ' '.join(row['data_refs'])
            link = inspection_link(target, value, value + '，查看' + label + '的计算依据',
                                   observation_ref=refs, described_by=scale_note if limited else None)
            return ('<span data-calculation-ref="' + escape(row['id']) +
                    '" data-calculation-inputs="' + escape(refs) + '">' + link + '</span>')

        if compact:
            by_metric = {}
            for row in results:
                name = visible_calculation_title(row)
                by_metric.setdefault((row.get('code'), name, row['unit']), []).append(row)
            groups = list(by_metric.values())
        else:
            groups = [[row] for row in results]
        single_periods = {calculation_period(group[0]) for group in groups if len(group) == 1}
        common_period = next(iter(single_periods)) if compact and len(single_periods) == 1 else ''
        multiple_subjects = len({row.get('code') for row in results}) > 1
        parts = []
        for group in groups:
            name = visible_calculation_title(group[0])
            if multiple_subjects:
                name = (group[0].get('code') or '') + ' ' + name
            periods = {calculation_period(row) for row in group}
            if compact and len(group) == len(periods) == 2 and all(periods):
                before, after = sorted(group, key=calculation_period)
                old_value = before['display'] + before['unit']
                new_value = after['display'] + after['unit']
                if not client_financial:
                    parts.append(escape(name) + '由' + reading(before, name) + '变为' + reading(after, name))
                    continue
                old_label, new_label = map(client_financial_value, (old_value, new_value))
                if old_label == new_label and old_value != new_value:
                    old_label, new_label = old_value, new_value
                previous, current = (Decimal(row['display']) for row in (before, after))
                movement = '升至' if current > previous else '降至' if current < previous else '变为'
                parts.append(escape(name) + ('由约' if old_label != old_value else '由') + reading(before, name, old_label)
                             + movement + ('约' if new_label != new_value else '') + reading(after, name, new_label))
            elif client_financial and len(group) == 1 and group[0]['method'] == 'raw_change_v1' and group[0]['unit'] == '%':
                row = group[0]
                number = Decimal(row['display'])
                direction = '下降' if number < 0 else '增长'
                shown = client_financial_value(row['display'].lstrip('+-') + '%')
                parts.append(escape(name) + direction + reading(row, name, shown) if number else escape(name) + '持平')
            else:
                parts.extend(escape(' '.join(filter(None, ('' if common_period else calculation_period(row), name))))
                             + '为' + reading(row, name)
                             for row in group)
        prefix = escape(common_period) + '，' if common_period and not client_financial else ''
        return prefix + '，'.join(parts) + '。'


    summary_rows, _ = logic_basis(content, dict(data_refs=[row['id'] for row in facts]))
    period_scope=draft.get('financial_period_scope',{})
    recent_scope=draft.get('latest_observation_notes',{})
    expected_binding=dict(task_id=content.get('task_id'),research_version=content.get('research_version'),input_hash=content.get('input_hash'))
    fact_hash = fingerprint(facts) if period_scope or recent_scope else None
    scope_notes=period_scope.get('notes',[]) if (period_scope.get('version')==1 and period_scope.get('binding')==expected_binding and period_scope.get('facts_hash')==fact_hash) else []
    recent_notes=[note for note in recent_scope['notes'] if note not in draft['conclusion']] if (
        recent_scope.get('binding')==expected_binding and recent_scope.get('facts_hash')==fact_hash) else []
    def visible_paragraphs(*values):
        return {part.strip() for value in values if isinstance(value, str)
                for part in (value, *value.splitlines()) if part.strip()}

    printed = visible_paragraphs(draft['conclusion'], *scope_notes, *recent_notes) if concise else set()
    conclusion = public_prose(draft['conclusion'], summary_rows, field_links(draft, 'conclusion'), section='summary')
    sections = ['<section id="summary"><h2>核心结论</h2>' + prose_markup(conclusion)
        + ''.join('<p class="financial-period-scope" data-report-scope="financial-period">'+escape(note)+'</p>'
                  for note in scope_notes if isinstance(note,str) and note != draft['conclusion'])
        + ''.join('<p class="reader-basis" data-report-scope="latest-observation">'+escape(note)+'</p>'
                  for note in recent_notes if isinstance(note,str) and note != draft['conclusion'])+'</section>']
    from .knowledge import GENERAL_DEFINITION_SCOPE, ResearchKnowledge, cited_definitions, draft_definitions_enabled
    if draft_definitions_enabled(content):
        knowledge = ResearchKnowledge()
        bound = dict(content, id=content['task_id'], version=content['research_version'])
        for question in content['questions']:
            if question.get('answer_scope') == GENERAL_DEFINITION_SCOPE and question['status'] == '已回答':
                knowledge.validate_definition_answer(bound, question)
        definitions = cited_definitions(content)
        if definitions:
            paragraphs = []
            for entry in definitions:
                paragraphs.append('<h3>'+escape(entry['title'])+'</h3>'+paragraph(entry['content'])
                    +paragraph(entry['conditions'])+paragraph(entry['counterexample'])
                    +'<p class="reader-basis">'+escape(entry['temporal_scope'])+'</p>'
                    +'<p class="reader-basis">来源 '+escape(entry['source'])+'</p>')
            sections.append('<section id="indicator-definitions"><h2>指标口径</h2>'+''.join(paragraphs)+'</section>')
    future_rules = ({row['logic_index']: row['tracking'] for row in draft_conditions(content) if row['tracking']}
                    if internal else {})
    logic_risks = [item['risk'] for item in draft['logics'] if item.get('risk')]
    client_risk_lines = {risk.strip() for risk in [*draft['risks'], *logic_risks]} if not internal else set()
    theory_sources = []
    for index, item in enumerate(draft['logics'], 1):
        item = draft_logic_reading(item, draft.get('calculations', ()))
        logic_id = 'logic-' + str(index)
        related = [spec for spec in chart_specs if spec.get('paper_logic') == logic_id]
        reused = [spec for spec in chart_specs if logic_id in spec.get('reused_by', ())]
        figures = related + reused
        basis_rows, basis_sources = logic_basis(content, item)
        knowledge_entries = logic_knowledge(content, item) if item.get('knowledge_refs') else []
        if draft.get('organization') == 'mechanism':
            theory_sources.extend(entry['publisher'] for entry in knowledge_entries if entry.get('publisher'))
        target = 'draft-basis-' + str(index)
        has_peer_bridge = any(key in item for key in ('peer_revenue_series', 'bank_profit_series', 'peer_operating_series'))
        peer_summary = item.get('peer_summary_only')
        etf_narrative = item.get('etf_interim_readout') is True
        narrated = (item.get('event_session_summary') or item.get('event_observation_only')
                    or peer_summary or has_peer_bridge or etf_narrative
                    or item.get('economic_readout') is True
                    or item.get('industry_chain_readout') is True or item.get('macro_demand_readout') is True
                    or item.get('lpr_readout') is True
                    or item.get('stock_quality_readout') is True or item.get('valuation_research_readout') is True)
        peer_tables = ([] if has_peer_bridge and not internal else
                       financial_peer_tables(content, [row for row in basis_rows if row['id'] not in withheld_amounts]))
        peer_refs = {ref for table in peer_tables for ref in table['data_refs']}
        charted_lines = {group['line'] for spec in related for group in spec.get('document_groups', [])} if not internal else set()
        charted_lines.update(client_risk_lines)
        named_calculations, named_observations = set(), set()
        earlier_calculations = set(published_calculations) if editorial and research_paper else set()
        narrative = public_prose(item['analysis'], basis_rows, field_links(item, 'analysis'),
                                 charted_lines=charted_lines, section='logic',
                                 separate_lines=has_peer_bridge or etf_narrative
                                 or bool(peer_summary or item.get('peer_growth_series')),
                                 named_calculations=named_calculations, named_observations=named_observations)
        implications = public_prose(item['implication'], basis_rows, field_links(item, 'implication'),
                                   charted_lines=client_risk_lines, named_calculations=named_calculations,
                                   named_observations=named_observations)
        if narrative and implications:
            narrative[-1] += prose_space(item['analysis'].rstrip(), item['implication'].lstrip()) + implications.pop(0)
        narrative.extend(implications)
        if internal and (detail := item.get('detail_analysis')):
            narrative.extend(public_prose(detail, basis_rows, field_links(item, 'detail_analysis'), separate_lines=True,
                                          charted_lines=visible_paragraphs(item['analysis'], item['implication']),
                                          named_calculations=named_calculations, named_observations=named_observations))
        prose = ''
        if item.get('document_excerpts'):
            prose += retained_original_paragraphs(item)
        risk = item['risk']
        if concise:
            paragraphs = visible_paragraphs(item['analysis'], item['implication'])
            printed.update(paragraphs - client_risk_lines)
            if risk.strip() in paragraphs:
                risk = ''
            if internal and not risks_at_end:
                printed.update(visible_paragraphs(risk))
        if risk and internal and not risks_at_end:
            prose += '<p class="argument-boundary">风险：' + escape(risk) + '</p>'
        if index in future_rules:
            rule = future_rules[index]
            if concise:
                printed.update(visible_paragraphs(rule['condition']))
            metadata = '指标：' + rule['indicator'] + '；窗口：' + rule['window']
            if rule.get('benchmark'):
                metadata += '；比较基准：' + rule['benchmark']
            prose += ('<div class="draft-tracking-rule"><p><strong>跟踪口径</strong>：'
                + escape(rule['condition']) + '</p><p>' + escape(metadata)
                + '；来源：' + escape(rule['source']) + '</p></div>')
        for spec in reused:
            anchor = escape(spec['target'])
            prose += ('<p class="basis-references">见<a href="#' + anchor + '" data-reused-figure="' + anchor
                      + '">' + escape(spec['name']) + '图</a>。</p>')
        chart_refs = {ref for spec in figures if spec.get('coverage_kind') != 'mechanism'
                      for ref in spec.get('calculation_refs', [])}
        # Registered results still need a reading surface when the numbers
        # did not earn a plot. Detailed papers also keep the underlying methods.
        unplotted = [dependencies[ref] for ref in dict.fromkeys(item.get('calculation_refs', []))
                     if ref in dependencies and ref not in chart_refs and ref not in named_calculations
                     and ref not in earlier_calculations]
        results = unplotted if (not internal and not narrated and (show_calculation_table
            or not any(spec.get('coverage_kind') == 'numeric' for spec in figures))) else []
        if not internal and peer_tables and not narrated and not results:
            results = [row for row in unplotted if row['method'] in ('raw_margin_v1', 'raw_percent_v1', 'raw_multiple_v1')]
        compact_amounts = (not internal and (draft.get('observed_argument_sections') or editorial and research_paper)
                           and not any(spec.get('coverage_kind') == 'numeric' for spec in figures)
                           and 0 < len(results) <= 4)
        displayed_results = results
        if compact_amounts:
            # Keep the discussion and its citations. An adjacent argument's
            # chart already presents the registered changes at the same scope.
            displayed_results = [row for row in results
                                 if not (row['id'] in cited_in_summary
                                         and (client_financial or row.get('comparison_source_ref')))
                                 and not (narrative and row['id'] in graphed_changes
                                          and row['id'] not in conditional)]
        displayed_calculations = chart_refs | {row['id'] for row in displayed_results}
        published_calculations.update(displayed_calculations)
        conditional = {ref for ref in displayed_calculations if dependencies.get(ref, {}).get('conditional_equal_scale')}
        scale_note = 'risk' if conditional else ''
        calculation_markup = ''
        inline_amounts = bool(compact_amounts and displayed_results)
        if displayed_results:
            if inline_amounts:
                reading = calculation_reading(displayed_results, target, conditional, scale_note, compact=True)
                if narrative:
                    narrative[-1] += reading
                else:
                    narrative.append(reading)
            elif len(displayed_results) <= 2:
                reading = calculation_reading(displayed_results, target, conditional, scale_note)
                calculation_markup = prose_markup([reading], measured_tail=True)
            else:
                calculation_markup = calculation_table(displayed_results, target, conditional, scale_note)
        prose = prose_markup(narrative, measured_tail=inline_amounts) + prose
        if related:
            charts = []
            for spec in related:
                limited = bool(conditional.intersection(spec.get('calculation_refs', [])))
                charts.append(figure(spec, scale_note=scale_note if limited else ''))
            prose = '<div class="argument-reading"><div class="argument-introduction">' + prose + '</div><div class="argument-visual"><div class="draft-figures">' + ''.join(charts) + '</div></div></div>'
        label = item.get('display_heading') or draft_logic_heading(draft.get('organization'), index, item['title'])
        for table in peer_tables:
            prose += peer_table(table, target)
        prose += calculation_markup
        displayed_data.update(peer_refs)
        plotted = {ref for spec in figures if spec.get('coverage_kind') != 'mechanism'
                   for ref in spec.get('data_refs', [])}
        reading_inputs = {ref for result in displayed_results for ref in result['data_refs']} if compact_amounts else set()
        if research_paper:
            # A displayed financial comparison already accounts for its input
            # cells. Absolute levels stay available from the evidence link.
            active_calculations = (named_calculations | earlier_calculations.intersection(item.get('calculation_refs', ())))
            for result in [*displayed_results, *(dependencies[ref] for ref in active_calculations if ref in dependencies)]:
                if all(analysis_field(row) in FINANCIAL for row in result['rows']):
                    reading_inputs.update(result['data_refs'])
        if not internal:
            displayed_data.update(reading_inputs | named_observations)
        cross_sections={(spec.get('source_ref'),spec.get('field'),spec.get('unit'),spec.get('series_basis'),spec.get('context'))
            for spec in figures if spec.get('axis_role')=='cross_section'}
        # Keep calculation-only predecessors in the linked basis. A ranked
        # cross-section already presents its observations in the chart.
        basis_only = {ref for spec in figures for ref in spec.get('basis_only_refs', ())} if not internal else set()
        excluded_raw = plotted | peer_refs | reading_inputs | named_observations | withheld_amounts | basis_only
        if internal and item.get('peer_profit_details'):
            from .draft_analysis import PEER_PROFIT_LINE_CHANGE

            excluded_raw.update(ref for ident in item['calculation_refs']
                                if (entry := dependencies.get(ident))
                                and entry.get('comparison_kind') == PEER_PROFIT_LINE_CHANGE
                                for ref in entry['data_refs'])
        if narrated and not internal and narrative:
            discussed = {row['id'] for row in basis_rows}
            excluded_raw.update(discussed)
            displayed_data.update(discussed)
        sparse = [row for row in basis_rows if row['id'] not in excluded_raw
            and not (scoped_points and row.get('previous_close_for')) and not (
                row.get('axis_role')=='cross_section' and (row.get('artifact_id'),row.get('field'),
                row.get('unit'),row.get('series_basis'),row.get('period')) in cross_sections)]
        numeric = [row for row in sparse if type(row['value']) in (int, float) and math.isfinite(row['value'])]
        if numeric:
            prose += observation_reading(numeric, target) if len(numeric) <= 2 else observation_table(sparse, target)
            displayed_data.update(row['id'] for row in sparse)
        if results or (not item.get('document_excerpts') and (basis_rows or basis_sources or knowledge_entries or item.get('risk')
                or (internal and item.get('workpaper_notes')))):
            inspection_records.append(basis_record(target, item, basis_rows, basis_sources, knowledge_entries))
            heading = inspection_link(target, label, '查看' + item['title'] + '的研究依据')
        else:
            heading = escape(label)
        sections.append('<section id="logic-' + str(index) + '"><div class="argument-heading"><h2>'
            + heading + '</h2></div>' + prose + '</section>')
    remaining = [spec for spec in chart_specs if spec['target'] not in drawn]
    directories = draft.get('filing_directories') or [draft.get('filing_directory') or {}]
    directory_tables = []
    id_header = '<th scope="col">目录编号</th>' if internal else ''
    for directory in directories:
        coverage = directory.get('official')
        groups = [('', directory.get('rows', []))]
        if coverage:
            groups = [('iFinD', directory['rows']), (coverage['name'], coverage['rows'])]
        label = ''
        if directory.get('code'):
            label = f'{directory["code"]}，{directory["start_date"]}至{directory["end_date"]}'
        for source, items in groups:
            if not items:
                continue
            heading = '，'.join(part for part in (source, label) if part)
            caption = '<caption>' + escape(heading) + '</caption>' if heading else ''
            rows = ''.join('<tr><td>' + escape(row['title']) + '</td><td><time>' + escape(row['date']) + '</time></td>'
                           + ('<td>' + escape(row['source_id']) + '</td>' if internal else '') + '</tr>'
                           for row in items)
            directory_tables.append('<table>' + caption + '<thead><tr><th scope="col">标题</th>'
                '<th scope="col">公告日期</th>' + id_header + '</tr></thead><tbody>' + rows + '</tbody></table>')
    if directory_tables:
        sections.append('<section id="filing-directory"><h2>公告目录</h2>'
            '<div class="report-data-scroll" tabindex="0" role="region" aria-label="公告目录">'
            + ''.join(directory_tables) + '</div></section>')
    collections = draft.get('collection_coverage', [])
    if collections:
        from .publication_content import reader_period_label

        count_rows, sample_tables = [], []
        for item in collections:
            period = escape(reader_period_label(item['requested_period']))
            scope = escape(item['topic'] + item['discovery_kind'])
            fields = [field['name'] for field in item['fields']]
            for field in item['fields']:
                count_rows.append('<tr><th scope="row">' + scope + '</th><td>' + period + '</td><td>'
                    + escape(field['name']) + '</td><td>' + escape(field['received']) + '</td><td>'
                    + escape(field['missing']) + '</td></tr>')
            sample_head = ''.join('<th scope="col">' + escape(field) + '</th>' for field in fields)
            sample_rows = ''.join('<tr><th scope="row">' + escape(row['code']) + '</th><td>'
                + escape(row['name'] or '—') + '</td><td>' + escape(row['classification'] or '—') + '</td>'
                + ''.join('<td>' + ('已返回' if row['fields'][field] else '缺项') + '</td>' for field in fields) + '</tr>'
                for row in item['rows'])
            sample_tables.append('<table><caption>' + scope + ' ' + period + ' 样本</caption><thead><tr>'
                '<th scope="col">代码</th><th scope="col">名称</th><th scope="col">来源分类</th>'
                + sample_head + '</tr></thead><tbody>' + sample_rows + '</tbody></table>')
        panel = '<div class="report-data-scroll" tabindex="0" role="region" aria-label="样本明细">' + ''.join(sample_tables) + '</div>'
        if internal:
            sample_access = panel
        else:
            inspection_records.append('<section id="draft-sample-coverage" data-inspection-title="样本明细">'
                '<h3>样本明细</h3>' + panel + '</section>')
            sample_access = '<p>' + inspection_link('draft-sample-coverage', '查看样本明细') + '</p>'
        sections.append('<section id="sample-coverage"><h2>样本覆盖</h2><div class="report-data-scroll" tabindex="0"'
            ' role="region" aria-label="样本覆盖"><table><thead><tr><th scope="col">范围</th><th scope="col">报告期</th>'
            '<th scope="col">字段</th><th scope="col">已返回</th><th scope="col">缺项</th></tr></thead><tbody>'
            + ''.join(count_rows) + '</tbody></table></div>' + sample_access + '</section>')
    if visible_facts:
        from .draft_visuals import display_observation
        from .publication_content import observation_value,reader_period_label
        display_value=observation_value if report_profile=='detailed' else display_observation
        from .draft_visuals import comparison_table,temporal_table
        matrix=comparison_table(content) if any(spec.get('kind') for spec in chart_specs) else None
        aligned=temporal_table(content)
        for table in (matrix,aligned):
            if table:
                units={observation_unit(row) for group in table['rows'] for row in group['observations']}
                if len(units)==1:table['unit']=next(iter(units))
                elif table is aligned:aligned=None
                else:matrix=None
        if aligned:
            rows=''.join('<tr><td>'+escape(reader_period_label(item['period']))+'</td>'+''.join(
                '<td data-raw-value="'+escape(value['id'])+'">'+escape(display_value(value))+'</td>'
                for value in item['observations'])+'</tr>' for item in aligned['rows'])
            table=('<table class="financial-comparison-table temporal-comparison-table" data-raw-observations="'+str(aligned['observations'])+'">'
                '<caption>数据对照<span class="table-unit">单位：'+escape(aligned['unit'])+'</span></caption>'
                '<thead><tr><th>日期</th>'+''.join('<th>'+escape(label)+'</th>' for label in aligned['columns'])
                +'</tr></thead><tbody>'+rows+'</tbody></table>')
        elif matrix:
            rows=''.join('<tr><td>'+escape(row['label'])+'</td>'+''.join(
                '<td data-raw-value="'+escape(value['id'])+'">'+escape(display_value(value))+'</td>'
                for value in row['observations'])+'</tr>' for row in matrix['rows'])
            unit='金额单位待核对，保留接口原值' if matrix['unit']=='原始单位待核对' else '单位：'+matrix['unit']
            table=('<table class="financial-comparison-table" data-raw-observations="'+str(matrix['observations'])+'">'
                '<caption>'+escape(matrix['code'])+'<span class="table-unit">'+escape(unit)+'</span></caption>'
                '<thead><tr><th>指标</th>'+''.join('<th>'+escape(period)+'</th>' for period in matrix['periods'])
                +'</tr></thead><tbody>'+rows+'</tbody></table>')
        else:
            named = any(row.get('object_label') not in (None, '', row['code']) for row in visible_facts)
            rows = ''.join(('<tr data-observation-id="'+escape(row['id'])+'"><td>' if report_profile=='detailed' else '<tr><td>') + '</td><td>'.join(escape(value) for value in (
                [row['code']] + ([row.get('object_label', '')] if named else [])
                + [scoped_label(row), reader_period_label(row['period']) or '未标注', display_value(row), observation_unit(row)]))
                + '</td></tr>' for row in visible_facts)
            table='<table><thead><tr><th>标的</th>'+('<th>名称</th>' if named else '')+'<th>指标</th><th>期间</th><th>原值</th><th>单位</th></tr></thead><tbody>'+rows+'</tbody></table>'
        complete_data='<div class="report-data-scroll" tabindex="0" role="region" aria-label="完整原始数据，可横向滚动">'+table+'</div>'
        if internal and not event_endpoint_report:
            extra_figures='<div class="draft-figures">'+''.join(figure(spec) for spec in remaining)+'</div>' if remaining else ''
            sections.append('<section id="data"><h2>关键数据</h2>'+extra_figures+complete_data+'</section>')
        else:
            inspection_records.append('<section id="complete-report-data" data-inspection-title="数据与来源"><h3>完整数据</h3>'+complete_data+'</section>')
            if remaining:
                sections.append('<section id="data"><h2>补充图表</h2><div class="draft-figures">'+''.join(figure(spec) for spec in remaining)+'</div></section>')
            elif not internal and not displayed_data and not collections and not all(
                    row.get('locator', {}).get('kind') == STOCK_QUALITY_CELL_KIND for row in visible_facts):
                # Reports without usable plot references still expose acquired
                # observations in a table, instead of hiding every value.
                published = [row for row in visible_facts if row['id'] not in withheld_amounts]
                if published:
                    visible_data = complete_data if len(published) == len(visible_facts) else observation_table(
                        published, 'complete-report-data')
                    sections.append('<section id="data"><h2>关键数据</h2>'+visible_data+'</section>')
    from .publication_content import reader_notes
    basis_notes=[note for note in reader_notes(content, client=not internal,
        comparison_refs=published_calculations if editorial else None) if note != draft['conclusion']]
    public_risks = [*draft['risks'], *logic_risks] if not internal or risks_at_end else (draft['risks'] or logic_risks)
    if project_paper or editorial:
        public_risks = [text for risk in public_risks if (text := client_research_text(
            risk, hide_unverified_amounts=protect_amounts, editorial=editorial)[0].strip())]
        basis_notes = [text for note in basis_notes if (text := client_research_text(
            note, hide_unverified_amounts=protect_amounts, editorial=editorial)[0].strip())]
    if concise:
        public_risks = [risk for risk in (draft['risks'] if internal and not risks_at_end else public_risks)
                        if risk.strip() not in printed]
        printed.update(visible_paragraphs(*public_risks))
        basis_notes = [note for note in basis_notes if note.strip() not in printed]
    if public_risks or basis_notes:
        heading = '口径与限制' if concise and not public_risks else '风险提示'
        sections.append('<section id="risk"><h2>' + heading + '</h2>'
            + ('<ul>'+''.join('<li>' + escape(item) + '</li>' for item in dict.fromkeys(public_risks))+'</ul>' if public_risks else '')
            + ''.join('<p class="reader-basis">'+escape(note)+'</p>' for note in basis_notes)+'</section>')
    comparison = (content.get('changes') or {}).get('draft_observations')
    if report_profile == 'detailed' and comparison:
        table = ''
        if comparison['rows']:
            headings = ''.join('<th scope="col">' + escape(cell) + '</th>' for cell in comparison['columns'])
            rows = ''.join('<tr>' + ''.join('<td>' + escape(cell) + '</td>' for cell in row['cells']) + '</tr>'
                           for row in comparison['rows'])
            table = ('<div class="report-data-scroll report-observation-delta" tabindex="0" role="region" aria-label="版本读数对照">'
                     '<table><thead><tr>' + headings + '</tr></thead><tbody>' + rows + '</tbody></table></div>')
        sections.append('<section id="changes"><h2>观测变化</h2><p>' + escape(comparison['summary']) + '</p>' + table + '</section>')
    referenced_sources = {row.get('artifact_id') for row in facts}
    if directory.get('source_ref'):
        referenced_sources.add(directory['source_ref'])
    referenced_sources.update(item['reference']['artifact_id'] for item in collections)
    referenced_sources.update(ref for logic in draft.get('logics',[]) for ref in logic.get('source_refs',[]))
    sources = [name for ref, name in source_labels.items() if ref in referenced_sources and name]
    sources = list(dict.fromkeys(sources + theory_sources))
    from .market_series import STATED_LATEST_EXACT_NAV_VERSION, STATED_LATEST_POLICY
    show_sources = (sources or facts or not draft.get('readout_gaps') or
                    (content.get(STATED_LATEST_POLICY) or 0) < STATED_LATEST_EXACT_NAV_VERSION)
    if concise:
        show_sources = bool(sources or facts)
    source_line = ''
    if show_sources:
        data_link = (' ' + inspection_link('complete-report-data', '数据与来源')
                     if visible_facts and (not internal or event_endpoint_report) else '')
        stock_refs = {row['artifact_id'] for row in facts if row.get('locator', {}).get('kind') == STOCK_QUALITY_CELL_KIND}
        stock_links = []
        if stock_refs:
            from .announcement_sources import original_url

            for record in content['source_records']:
                if (record['id'] not in stock_refs or record.get('task_id') != content['task_id']
                        or record.get('research_version') != content['research_version']
                        or (record.get('source'), record.get('operation')) != ('official', 'release_original')):
                    continue
                title, _ = disclosure_caption(record, '')
                if not title:
                    continue
                origin = record['snapshot']['official_origin']
                try:
                    address = original_url(origin.get('url'), 'cninfo')
                except ContractError:
                    continue
                stock_links.append('<a href="' + escape(address) + '" target="_blank" rel="noopener noreferrer">'
                                   + escape(title) + '</a>')
        source_links = {}
        if content.get('lpr_study'):
            from .lpr_rates import HISTORY_PAGE, PUBLISHER as LPR_PUBLISHER
            from .nfra_bank_statistics import CATALOGUE_PAGE, PUBLISHER as NFRA

            source_links[LPR_PUBLISHER] = HISTORY_PAGE
            source_links[NFRA] = CATALOGUE_PAGE
        if scope := content.get('fomc_treasury_scope'):
            from .treasury_rates import OPERATION as H15_YIELDS, PUBLISHER as H15_PUBLISHER, validated_snapshot

            for record in content['source_records']:
                if (record['id'] not in referenced_sources or record.get('task_id') != content['task_id']
                        or record.get('research_version') != content['research_version']):
                    continue
                label, snapshot = source_labels.get(record['id']), record.get('snapshot', {})
                if label == '美联储' and record.get('operation') == 'release_original':
                    origin = snapshot['official_origin']
                    if origin['path_date_hint'] == scope['end']:
                        source_links[label] = origin['url']
                if label == H15_PUBLISHER and record.get('operation') == H15_YIELDS and record['parameters'] == scope:
                    try:
                        if validated_snapshot(snapshot, scope):
                            source_links[label] = snapshot['url']
                    except (ContractError, KeyError, TypeError, ValueError):
                        continue
        source_text = '、'.join('<a href="'+escape(source_links[label])+'" target="_blank" rel="noopener noreferrer">'
                              +escape(label)+'</a>' if label in source_links else escape(label) for label in sources) or '研究资料'
        if stock_links:
            source_text += '，' + '、'.join(stock_links)
        source_line = '<footer class="report-source-line"><p>来源：' + source_text + data_link + '</p></footer>'
    css = (resource_directory(resources, 'frontend') / 'report.css').read_text()
    if facts and aligned:
        css+='\n.temporal-comparison-table th:not(:first-child){white-space:normal;line-height:1.5}.temporal-comparison-table th:first-child,.temporal-comparison-table td:first-child{white-space:nowrap}'
    vendor = (resource_directory(resources, 'vendor') / 'echarts.min.js').read_text() if chart_specs else ''
    data = json.dumps(chart_specs, ensure_ascii=False).replace('<', '\\u003c')
    script=(resource_directory(resources,'frontend')/'draft-figures.js').read_text().replace('__RH_DRAFT_FIGURES__',data)
    from .visual_catalog import catalogue, default_report_theme
    visual_profile=visual_profile or {'theme':default_report_theme(content)}
    if visual_profile.get('theme') not in {row['id'] for row in catalogue()['themes']}:raise ValueError('配色未登记')
    profile=json.dumps(visual_profile,ensure_ascii=False).replace('<','\\u003c')
    visual_catalog=(resource_directory(resources,'frontend')/'visual-catalog-data.js').read_text()
    visual_library=(resource_directory(resources,'frontend')/'visual-library.js').read_text()
    return '<!doctype html><html lang="zh-CN" data-reader-version="' + escape(READER_VERSION) + '"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' \
        + '<title>' + escape(draft['title']) + '</title><style>' + css \
        + '.draft-figures{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.draft-figures figure{min-width:0;margin:8px 0}.argument-visual .draft-figures{grid-template-columns:minmax(0,1fr)}table{width:100%;border-collapse:collapse}th,td{text-align:right;padding:8px;border-bottom:1px solid #DFE5EC}th:first-child,td:first-child{text-align:left}@media(max-width:640px){.draft-figures{grid-template-columns:1fr}}' \
        + '</style><main><header class="report-header"><h1>' + escape(draft['title']) \
        + '</h1><p class="report-meta"><time>' + escape(research_date_label(content['cutoff'], compact=True)) \
        + '</time></p></header>' + ''.join(sections) + source_line + '</main>' \
        + (inspection_surface(inspection_records) + '<script>' + INSPECTION_SCRIPT + '</script>' if inspection_records else '') \
        + (('<script>' + vendor + '</script>') if chart_specs else '') + '<script>'+visual_catalog+'</script><script>'+visual_library+'</script><script id="rh-visual-profile" type="application/json">'+profile+'</script><script>' + script + '</script></html>'
