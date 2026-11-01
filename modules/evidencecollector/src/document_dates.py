"""Declared page dates, separate from observation periods and first availability."""
import base64
from collections import Counter
from datetime import date, datetime
from html.parser import HTMLParser
import re

from .contracts import ContractError

POLICY = 'document_date_role_version'
ROLE = 'document_declared_publication'
FIELDS = frozenset(('pubdate', 'datepublished', 'article:published_time', 'dc.date.issued',
                    'citation_publication_date'))


def enabled(task):
    version = task.get(POLICY)
    if version is None:
        return False
    if type(version) is not int or version != 1:
        raise ContractError('原页标注日期版本未登记')
    return task.get('research_mode') == 'current' and task.get('intent') != '原件内容核对'


class _MetaTag(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.unexpected = False

    def handle_starttag(self, tag, attrs):
        if tag != 'meta' or len({key for key, _ in attrs}) != len(attrs):
            self.unexpected = True
        self.tags.append((tag, dict(attrs)))

    handle_startendtag = handle_starttag

    def handle_data(self, text):
        if text.strip(): self.unexpected = True

    def handle_endtag(self, tag):
        self.unexpected = True

    def handle_comment(self, text):
        self.unexpected = True

    handle_decl = handle_comment
    handle_pi = handle_comment


def _declared_day(part):
    field, text = part.get('metadata_field'), part.get('text')
    if not isinstance(field, str) or field.casefold() not in FIELDS or not isinstance(text, str):
        return None
    parser = _MetaTag(); parser.feed(text); parser.close()
    if parser.unexpected or len(parser.tags) != 1:
        return None
    _, attributes = parser.tags[0]
    if ((attributes.get('name') or attributes.get('property')) != field
            or attributes.get('name') and attributes.get('property')):
        return None
    content = attributes.get('content')
    match = re.fullmatch(r'(\d{4})([-/])(\d{1,2})\2(\d{1,2})'
        r'([ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)?', content or '')
    if not match:
        return None
    try:
        day = date(int(match[1]), int(match[3]), int(match[4])).isoformat()
        if match[5]:
            datetime.fromisoformat(day + 'T' + match[5][1:])
        return day
    except ValueError:
        return None


def anchors(task, record, parts=None):
    """Only dates actually read from the same acquired HTML version qualify."""
    if not enabled(task) or not isinstance(record, dict):
        return []
    from .evidence import _original_download_profile, current_original_reference
    from .material_parser import verified_material
    from .contracts import market_date
    profile = _original_download_profile(record)
    material = profile.get('material') if profile else None
    if (not material or material.get('extract_version') != 'html-2'
            or current_original_reference(task, record) is None):
        return []
    try:
        raw = base64.b64decode(material['original_base64'], validate=True)
        actual = verified_material(material, raw)['parts']
    except (ContractError, KeyError, ValueError):
        return []
    # Conflicting declared publication dates stay visible as raw metadata,
    # but neither is advertised as the page's single declared date.
    values = {_declared_day(part) for part in actual} - {None}
    if len(values) != 1:
        return []
    day = next(iter(values))
    if day > market_date(task['cutoff']):
        return []
    retained = {part['locator']: part for part in actual}
    readable = actual if parts is None else parts
    counts = Counter(part.get('locator') for part in readable)
    result = []
    for part in readable:
        original = retained.get(part.get('locator'))
        if (original is None or counts[part.get('locator')] != 1
                or part.get('text') != original['text'] or _declared_day(original) != day):
            continue
        result.append(dict(date=day, date_role=ROLE, part=original['locator'], quote=original['text']))
    return result


def validate(item, task, record):
    """A page property cannot be promoted to market time, money or a forecast."""
    forbidden = {'value', 'raw_value', 'raw_token', 'raw_unit', 'conversion_factor', 'observation_date',
                 'observation_period', 'reporting_period_basis', 'calculation_context', 'calculation_context_basis'}
    if (not enabled(task) or item.get('date_role') != ROLE or item.get('unit') != '不适用'
            or item.get('nature') != '已实现事实' or forbidden.intersection(item)):
        raise ContractError('原页标注日期仅作当前版本文字事实，不作金融观测、数值或计算口径')
    locator = item.get('record_locator', {})
    matched = [anchor for anchor in anchors(task, record)
               if anchor['date'] == item.get('period') and anchor['part'] == locator.get('part')
               and anchor['quote'] == item.get('text') and anchor['quote'] == locator.get('period_quote')
               and anchor['part'] == locator.get('period_part')]
    if len(matched) != 1:
        raise ContractError('原页标注日期须逐字定位同一实际meta字段，不采用截取、修改日或其他原件')
