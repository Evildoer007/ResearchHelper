"""Compare frozen report wording and observations without approving a judgment."""
import copy
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from .publication_content import observation_title, reader_period_label, source_label
from .contracts import ContractError, fingerprint, timepoint


COMPARISON_POLICY = 'draft_comparison_version'
BASIS_SCHEMA = 'researchhelper.report-comparison-basis'


def comparison_enabled(task):
    version = task.get(COMPARISON_POLICY)
    return type(version) is int and version == 1


def report_comparison_basis(value):
    """Select exact public conclusions; never summarize or duplicate full archives.

    An unstructured old logic keeps its title and investment meaning. Missing
    explicit claims stay empty; full argument text remains in its frozen report.
    Positions describe each version's own order and are not cross-version IDs.
    """
    draft = value.get('draft_report', value.get('draft'))
    if not isinstance(draft, dict):
        return None
    text = lambda item: item if isinstance(item, str) else ''
    logics = []
    rows = draft.get('logics')
    for index, logic in enumerate(rows if isinstance(rows, list) else [], 1):
        if not isinstance(logic, dict):
            continue
        argument = logic.get('argument')
        claim = text(argument.get('claim')) if isinstance(argument, dict) else ''
        logics.append(dict(position=index, title=text(logic.get('title')), claim=claim,
                           implication=text(logic.get('implication'))))
    basis = dict(schema=BASIS_SCHEMA, version=1,
        task_id=value.get('task_id', value.get('id')),
        research_version=value.get('research_version', value.get('version')),
        cutoff=value['cutoff'], title=text(draft.get('title')),
        conclusion=text(draft.get('conclusion')), logics=logics)
    return dict(basis, basis_hash=fingerprint(basis))


def original_comparison_basis(task):
    """Reject changed or foreign history before building a model request."""
    prior = task.get('update_from')
    if not comparison_enabled(task) or not isinstance(prior, dict):
        return None
    from .planning import input_fingerprint
    if task.get('input_hash') != input_fingerprint(task):
        raise ContractError('版本对照输入已变化，请重新读取任务；未发起模型请求')
    value = prior.get('report_comparison_basis')
    if value is None:
        return None  # A legacy non-draft report has its own thesis review.
    expected = {'schema', 'version', 'task_id', 'research_version', 'cutoff',
                'title', 'conclusion', 'logics', 'basis_hash'}
    if (not isinstance(value, dict) or set(value) != expected
            or value.get('schema') != BASIS_SCHEMA or type(value.get('version')) is not int
            or value['version'] != 1 or value.get('task_id') != task['id']
            or type(value.get('research_version')) is not int
            or value['research_version'] != prior.get('version')
            or type(prior.get('version')) is not int or prior['version'] != task['version'] - 1
            or any(not isinstance(value.get(key), str) for key in ('cutoff', 'title', 'conclusion', 'basis_hash'))
            or not isinstance(value.get('logics'), list)
            or any(not isinstance(logic, dict) or set(logic) != {'position', 'title', 'claim', 'implication'}
                or type(logic.get('position')) is not int or logic['position'] < 1
                or any(not isinstance(logic.get(key), str) for key in ('title', 'claim', 'implication'))
                for logic in value.get('logics', []))
            or value.get('basis_hash') != fingerprint({key: item for key, item in value.items() if key != 'basis_hash'})):
        raise ContractError('原报告的任务、版本或正文依据已变化，未采用该版本对照')
    if timepoint(value['cutoff']) >= timepoint(task['cutoff']):
        raise ContractError('原报告截止时点不早于本版，未采用该版本对照')
    return copy.deepcopy(value)


def compare_reports(prior, task):
    original = report_comparison_basis(prior)
    if original_comparison_basis(task) != original:
        raise ContractError('原结论与冻结报告不一致，不能覆盖后复核')
    return dict(version=1, baseline_available=original is not None,
                prior=original, current=report_comparison_basis(task))


LABELS = {
    'changed': '读数变化',
    'added': '新增列入',
    'filled': '补齐读数',
    'missing': '缺值或未列出',
    'basis_changed': '口径变化',
    'refreshed': '本版记录同值',
    'retained': '沿用旧记录',
    'unmatched': '未能配对',
}
TABLE_COLUMNS = ('标的与指标', '期间', '原版读数', '本版读数', '来源', '变化')


def _group_observations(value, draft, version, *, market_refresh=False, compare_series_requests=False,
                        economic_periods=None, prior_reader=None, historical_target=None):
    from .draft_sources import (_observation_cell_selected, _observation_scope, _sequence_day,
                                _source_issue, date_sequence_basis, historical_structured_issue,
                                market_observation_basis)
    from .market_series import DAILY_MARKET_OPERATIONS, daily_market_as_of
    from .economic_observations import _period as economic_period

    last_day = _sequence_day(daily_market_as_of(historical_target)) if historical_target is not None else None

    records = defaultdict(list)
    sources = value.get('source_records')
    for record in sources if isinstance(sources, list) else ():
        if isinstance(record, dict) and isinstance(record.get('id'), str):
            records[record['id']].append(record)
    groups, unmatched = defaultdict(list), 0
    market_bases = {}
    prior_scopes = {}
    for row in draft['facts']:
        if not isinstance(row, dict) or not isinstance(row.get('artifact_id'), str):
            unmatched += 1
            continue
        if row.get('previous_close_for'):
            continue  # A prior close used as a calculation input is outside the observed window.
        origins = records[row.get('artifact_id')]
        parts = tuple(row.get(key) for key in ('source', 'code', 'field', 'period', 'series_basis'))
        if (len(origins) != 1 or not all(isinstance(part, str) and part for part in parts)
                or origins[0].get('task_id') != value.get('task_id', value.get('id'))
                or origins[0].get('research_version') != version or origins[0].get('source') != parts[0]
                or not isinstance(origins[0].get('operation'), str)):
            unmatched += 1
            continue
        record = origins[0]
        operation = record['operation']
        if historical_target is not None:
            source = record['source']
            if historical_structured_issue(historical_target, source, operation):
                unmatched += 1
                continue
            if operation in DAILY_MARKET_OPERATIONS.get(source, ()):
                observed = _sequence_day(row['period'])
                if observed is None or observed > last_day:
                    unmatched += 1
                    continue
        if prior_reader is not None and record['source'] == 'ifind' and record.get('kind') == 'api':
            if record['id'] not in prior_scopes:
                try:
                    prior_scopes[record['id']] = ({'tables': {}} if _source_issue(prior_reader, record) else
                        _observation_scope(prior_reader, record, latest_only=False))
                except (ContractError, KeyError, TypeError, ValueError, OverflowError):
                    prior_scopes[record['id']] = {'tables': {}}
            scope = prior_scopes[record['id']]
            locator = row.get('locator')
            if scope is not None and (not isinstance(locator, dict) or locator.get('field') != row['field']
                    or type(locator.get('table')) is not int or type(locator.get('row')) is not int
                    or not _observation_cell_selected(scope, locator['table'], row['field'], locator['row'])):
                unmatched += 1
                continue
        compare_series = compare_series_requests and record['source'] == 'ifind' and operation == 'date_sequence'
        if market_refresh or compare_series:
            key = record['id'], row['field']
            if key not in market_bases:
                basis = date_sequence_basis if compare_series else market_observation_basis
                market_bases[key] = basis(record, row['field'])
            if market_bases[key] is not None and (day := _sequence_day(row['period'])) is not None:
                parts = (*parts[:3], day.isoformat(), market_bases[key])
        if economic_periods and record['source'] == 'ifind' and operation == 'edb_service':
            frequency = economic_periods.get(row['code'])
            if frequency and (day := _sequence_day(row['period'])) is not None:
                parts = (*parts[:3], economic_period(day.isoformat(), frequency), parts[4])
        query = record.get('request_hash') if operation == 'smart_stock_picking' else ''
        if operation == 'smart_stock_picking' and (not isinstance(query, str) or not query):
            unmatched += 1
            continue
        retained = record.get('retained_from_version', version)
        if type(retained) is not int or not 1 <= retained <= version:
            unmatched += 1
            continue
        raw = row.get('raw_value', row.get('value'))
        try:
            number = Decimal(str(raw).strip()) if row.get('value') is not None else None
            if number is not None and not number.is_finite():
                raise InvalidOperation
        except InvalidOperation:
            unmatched += 1
            continue
        scope = tuple(str(row.get(key) or '') for key in ('unit', 'currency', 'period_kind', 'period_basis'))
        groups[(*parts, operation, query)].append(dict(
            number=number, scope=scope, row=row, source_ref=record['id'], source_version=retained,
            display=_display(number, raw, scope[0])))
    return groups, unmatched


def _display(number, raw, unit):
    if number is None:
        return '未返回'
    text = str(raw).strip()
    return text + ('，单位待核对' if unit in ('', '原始单位待核对') else unit)


def _point(candidates):
    if not candidates:
        return None, False
    if len({(point['number'], point['scope']) for point in candidates}) != 1:
        return None, True
    return max(candidates, key=lambda point: point['source_version']), False


def _change(before, after, version):
    if after is None or after['number'] is None:
        return 'missing'
    if before is None:
        return 'added'
    if before['number'] is None:
        return 'filled'
    if before['scope'] != after['scope']:
        return 'basis_changed'
    if before['number'] != after['number']:
        return 'changed'
    if after['source_version'] < version or after['source_ref'] == before['source_ref']:
        return 'retained'
    return 'refreshed'


def compare_observations(prior, task):
    """Pair original reads by source, instrument, indicator, period and series basis."""
    from .draft_sources import (READER_POLICY, _reader_at_least, economic_frequencies, economic_refresh_enabled,
                                historical_structured_scope, market_refresh_enabled)

    version = task['version']
    market_refresh = market_refresh_enabled(task)
    compare_series_requests = _reader_at_least(task, 24)
    economic_periods = economic_frequencies(task) if economic_refresh_enabled(task) else None
    current, unmatched = _group_observations(task, task['draft_report'], version, market_refresh=market_refresh,
                                             compare_series_requests=compare_series_requests,
                                             economic_periods=economic_periods)
    baseline = prior.get('draft')
    available = isinstance(baseline, dict) and isinstance(baseline.get('facts'), list)
    counts = dict.fromkeys(LABELS, 0)
    rows = []
    if available:
        prior_reader = None
        if _reader_at_least(task, 21):
            # Check the old raw cells at their original cutoff. A newer
            # "latest available" choice cannot invalidate an old observation.
            prior_reader = dict(prior, id=prior['task_id'], version=prior['research_version'],
                                draft_quote_scope_version=task.get('draft_quote_scope_version'))
            prior_reader[READER_POLICY] = task[READER_POLICY]
        previous, old_unmatched = _group_observations(prior, baseline, prior['research_version'],
            market_refresh=market_refresh, compare_series_requests=compare_series_requests, prior_reader=prior_reader,
            economic_periods=economic_periods,
            historical_target=task if historical_structured_scope(task) else None)
        counts['unmatched'] = unmatched + old_unmatched
        for address in sorted(previous.keys() | current.keys()):
            before, prior_conflict = _point(previous.get(address))
            after, current_conflict = _point(current.get(address))
            if prior_conflict or current_conflict:
                counts['unmatched'] += 1
                continue
            kind = _change(before, after, version)
            counts[kind] += 1
            if kind in ('refreshed', 'retained'):
                continue
            source, code, field, period = address[:4]
            present = (after or before)['row']
            rows.append(dict(kind=kind, code=code, field=field, period=period,
                prior_source_ref=before['source_ref'] if before else None,
                current_source_ref=after['source_ref'] if after else None,
                cells=[observation_title(present), reader_period_label(period),
                       before['display'] if before else '未列出', after['display'] if after else '未列出',
                       source_label(source), LABELS[kind]]))
        summary = '、'.join(label + str(counts[kind]) + '项' for kind, label in LABELS.items() if counts[kind])
        summary = summary + '。' if summary else '两版均未列出可对照的原始读数。'
    else:
        summary = '原版未留存草稿读数，暂无法进行同口径对照。'
    return dict(version=1, baseline_available=available, counts=counts, rows=rows,
                columns=list(TABLE_COLUMNS), summary=summary)
