"""Resolve which parts of the question can authorize a research subject."""

import re


_CLAUSE = re.compile(r'[^，,。；;！？?!\n]+')
_TURN_MODIFIER = r'(?:本轮|本次|这次|请|也|并|暂时|暂|先|现在)\s*'
_LEADING = rf'(?:{_TURN_MODIFIER})*(?:我\s*(?:{_TURN_MODIFIER})*)?'
_DECLINE = r'不想(?:要)?|不打算|不要|不用|不必|无需|不再|别|不'
_EXCLUDE = (r'排除|剔除|跳过|忽略|不纳入(?:研究|比较)?|'
            rf'(?:{_DECLINE})\s*(?:查询|研究|分析|比较|考虑|查看|核对|看|查)')
_PREFIX = re.compile(r'^' + _LEADING + r'(?:' + _EXCLUDE + r')\s*')
_SUFFIX = re.compile(r'\s*(?:则\s*)?' + _LEADING + r'(?:' + _EXCLUDE + r')(?:了)?$')
_SUBJECT_LIST = re.compile(r'\s*(?:、|以及|与|和|及|或)\s*')
_SUBJECT_TAIL = re.compile(r'(?:的(?:全部|任何)?(?:数据|资料))$')
_NAME = re.compile(r'[A-Za-z\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff·&* ]{0,29}')
_SIDES = ('前者', '后者')
_SWITCH_VERBS = r'改看|改为研究|改为分析|改研究|改分析|换看|换成|换为|转而研究|转而分析'
SUBJECT_SWITCH = re.compile(r'(?:' + _SWITCH_VERBS + r')(.+)')
_NEGATED_SWITCH = re.compile(
    r'^' + _LEADING + rf'(?:不是要?|并非要?|{_DECLINE})\s*'
    r'(?:(?:先|再|继续)\s*)?(?:' + _SWITCH_VERBS + r')\s*(?=\S)')
_RESUME_RESEARCH = re.compile(r'(?:而是|还是|仍然|仍旧|仍|是)\s*(?=核对|查|看|继续|比较|分析|研究|只|仅)')
QUOTED_TEXT = re.compile(
    r'“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|《[^》]*》|"(?:[^"\\]|\\.)*"|```[\s\S]*?```|`[^`\n]*`')


def object_mentions(text, task, names=()):
    """Use the source grammars and retain literal spans in user order."""
    from .economic_sources import economic_indicator_matches
    from .evidence import security_code_matches

    securities = [(match[1], *match.span(1)) for match in security_code_matches(
        text, encoding=task.get('security_declaration_encoding', 1))]
    indicators = [(match[0], *match.span()) for match in economic_indicator_matches(
        text, namespace=task.get('economic_identifier_namespace_version'))]
    literal_names = []
    if names:
        ordered = sorted(names, key=len, reverse=True)
        pattern = '|'.join(f'({re.escape(name)})' for name in ordered)
        literal_names = [(ordered[match.lastindex - 1], *match.span())
                         for match in re.finditer(pattern, text, re.IGNORECASE)]
    return sorted(securities + indicators + literal_names, key=lambda mention: mention[1])


def subject_key(value):
    number, separator, market = value.partition('.')
    if separator and market == 'HK' and number.isascii() and number.isdecimal():
        return f'{int(number)}.HK'
    return value.casefold()


def _excluded_term(term, task, names, pair, named_prefix=False):
    term = _SUBJECT_TAIL.sub('', term.strip()).strip()
    if term in _SIDES and len(pair) == 2:
        return [pair[_SIDES.index(term)]]
    known = next((name for name in names if name.casefold() == term.casefold()), None)
    if known:
        return [known]
    mentions = object_mentions(term, task)
    if len(mentions) != 1:
        if (not mentions and named_prefix and _NAME.fullmatch(term)
                and re.search(r'(?:公司|集团|控股|银行|证券|基金|指数|ETF|行业|板块|产业链)$', term)):
            return [term]
        return []
    value, start, end = mentions[0]
    prefix = term[:start]
    label = prefix.rstrip(' (（')
    tail = term[end:].strip()
    if tail.startswith(('(', '（')) and tail.endswith((')', '）')) and not label:
        label, tail = tail[1:-1].strip(), ''
    # A qualified metric, such as “600519.SH的市盈率”, is not a subject exclusion.
    if tail and not (tail in (')', '）') and prefix.endswith(('(', '（'))):
        return []
    if label and (not _NAME.fullmatch(label) or '的' in label):
        return []
    return [value, label] if label and label.upper() != 'ETF' else [value]


def _blank_spans(text, spans):
    for start, end in reversed(sorted(spans)):
        text = text[:start] + ' ' * (end - start) + text[end:]
    return text


def stated_subject_scope(task, *, names=(), pair=()):
    """Separate current choices, rejected switches and explicit exclusions."""
    question = task['question']
    excluded, spans = [], []
    for quote in QUOTED_TEXT.finditer(question):
        body = quote.group().strip('“”‘’「」『』《》"`').strip()
        command = re.sub(r'^' + _LEADING, '', body, count=1)
        if (SUBJECT_SWITCH.match(command) or _NEGATED_SWITCH.match(body)
                or _PREFIX.match(body) or _SUFFIX.search(body)):
            spans.append(list(quote.span()))
    for clause in _CLAUSE.finditer(_blank_spans(question, spans)):
        raw = clause.group()
        text = raw.strip()
        start = clause.end() - len(raw.lstrip())
        if rejected := _NEGATED_SWITCH.match(text):
            # Later affirmative choices can still select the rejected target.
            bridge = _RESUME_RESEARCH.search(text, rejected.end())
            if bridge:
                end = start + bridge.end()
            else:
                end = clause.end()
                if following := _RESUME_RESEARCH.match(question, end + 1):
                    end = following.end()
            spans.append([clause.start(), end])
            continue
        prefix, suffix = _PREFIX.match(text), _SUFFIX.search(text)
        if not prefix and not suffix:
            continue
        bridge = _RESUME_RESEARCH.search(text, prefix.end()) if prefix else None
        targets = (text[prefix.end():bridge.start() if bridge else len(text)]
                   if prefix else text[:suffix.start()])
        named_prefix = bool(prefix and prefix.group().strip().endswith(('排除', '剔除', '不纳入')))
        terms = [_excluded_term(term, task, names, pair, named_prefix) for term in _SUBJECT_LIST.split(targets)]
        values = [value for term in terms for value in term] if all(terms) else _excluded_term(
            targets, task, names, pair, named_prefix)
        if values:
            excluded.extend(values)
            spans.append([clause.start(), start + bridge.end() if bridge else clause.end()])
    return dict(version=1, excluded=list(dict.fromkeys(excluded)), question_spans=sorted(spans)) if spans else None


def scope_question(task, text=None):
    """Remove directives only for scope lookup, retaining the original question."""
    question = task.get('question', '')
    if text is not None and text != question:
        return text
    return _blank_spans(question, (task.get('subject_scope') or {}).get('question_spans', []))


def excluded_subjects(task):
    return (task.get('subject_scope') or {}).get('excluded', [])


def carried_exclusions(previous, task):
    """Keep exclusions in continuing scope until the user selects the subject."""
    existing = task.get('subject_scope') or {}
    previous_exclusions = excluded_subjects(previous)
    if not previous_exclusions:
        return None
    accepted = {subject_key(value) for value, _, _ in object_mentions(scope_question(task), task, previous_exclusions)}
    prior = [value for value in previous_exclusions if subject_key(value) not in accepted]
    excluded = list(dict.fromkeys([*existing.get('excluded', []), *prior]))
    if not excluded:
        return None
    return dict(version=1, excluded=excluded, question_spans=existing.get('question_spans', []))


def subject_allowed(task, value):
    excluded = excluded_subjects(task)
    if not excluded:
        return True
    key = subject_key(value)
    return all(subject_key(item) != key for item in excluded)
