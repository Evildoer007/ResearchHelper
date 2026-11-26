"""Automated evidence for the supported lithium battery value-chain study."""

import base64
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from html import unescape
import re

from .contracts import ContractError, market_date, timepoint
from .html_material import extract_html
from .miit_releases import MAX_PAGE, POLICY

CELL_KIND = 'miit_industry_bulletin_cell'
PERIOD_BASIS = '工信部公布的1至6月累计或期均读数'
ELECTRONICS = 'miit_electronics'
VEHICLES = 'miit_vehicles'

_CHAIN = re.compile(r'锂(?:离子)?电(?:池)?(?:的)?(?:产业链|供应链|上下游|全链条)')
_PERIOD = re.compile(r'(?P<year>20\d{2})年(?:的)?上半年|(?P<relative>今|去)年(?:的)?上半年')
_OUTSIDE = re.compile(r'回测|估值|目标价|预测|个股|股票|推荐|ETF|成交额|装车量|装机量|企业样本|'
                      r'月度|逐月|分月|近\d+|下半年|未来|库存量|稼动率|开工率|毛利率|百分点|'
                      r'20\d{2}[-/]\d{1,2}|\d{1,2}月\d{1,2}日|20\d{2}年全年|美国|欧洲', re.I)
_NUMBER = r'(\d+(?:\.\d+)?)'
_DIRECTION = r'((?:增长|下降|持平))'
_VEHICLE = dict(sales='新能源汽车销量', exports='新能源汽车出口量', penetration='新能源汽车新车销量占比')


def study_scope(task):
    """Resolve the requested half-year; avoid claiming a broader mandate."""
    from .draft_sources import historical_original_scope, local_scope_text, validate_stated_source

    if (task.get('draft_first_version') != 1 or task.get(POLICY) != 1
            or task.get('research_mode') != 'current' or task.get('entry') not in ('产业链', '行业', '混合')
            or task.get('intent') not in ('综合研究', '解释变化') or historical_original_scope(task)):
        return None
    question = local_scope_text(task['question'])
    if (not _CHAIN.search(question) or _OUTSIDE.search(question)
            or not re.search(r'供需|上游|中游|下游|传导|增长|研究|分析', question)
            or re.search(r'假设|如果|假如|不要用工信部|仅使用我|仅查iFinD', question, re.I)):
        return None
    from .evidence import declared_security_codes

    if declared_security_codes(task) or len(task['questions']) != 1:
        return None
    current_year = int(market_date(task['cutoff'])[:4])
    matches = [int(match['year']) if match['year'] else current_year - (match['relative'] == '去')
               for match in _PERIOD.finditer(question)]
    if len(set(matches)) != 1 or len(set(re.findall(r'20\d{2}年', question))) > 1:
        return None
    year = matches[0]
    if not 2023 <= year <= current_year or f'{year}-06-30' > market_date(task['cutoff']):
        return None
    try:
        validate_stated_source(task, 'official')
    except ContractError:
        return None
    return dict(year=year, question_id=task['questions'][0]['id'], titles={
        ELECTRONICS: [f'{year}年上半年全国锂离子电池行业运行情况'],
        VEHICLES: [f'{period}年6月汽车工业经济运行情况' for period in (year, year - 1)],
    })


def _meta(parts, name):
    values = []
    for part in parts:
        if part.get('metadata_field', '').casefold() != name:
            continue
        match = re.search(r'''\bcontent\s*=\s*(["'])(.*?)\1''', part['text'], re.I)
        if match:
            values.append(unescape(match[2]).strip())
    return values[0] if len(values) == 1 else ''


def _verified_body(task, record, scope, channel):
    from .official_sources import validate_original

    try:
        validate_original(task, record)
        snapshot = record['snapshot']
        origin = snapshot['official_origin']
        if snapshot['extract_version'] != 'html-miit-1' or origin['title'] not in scope['titles'][channel]:
            return None
        payload = base64.b64decode(snapshot['original_base64'], validate=True)
        if sha256(payload).hexdigest() != snapshot['hash']:
            return None
        parts = extract_html(payload, extract_version='html-miit-1')
        if parts != snapshot['parts'] or _meta(parts, 'articletitle') != origin['title']:
            return None
        department = '电子信息司' if channel == ELECTRONICS else '装备工业一司'
        publication = datetime.strptime(_meta(parts, 'pubdate'), '%Y-%m-%d %H:%M')
        if (_meta(parts, 'contentsource') != department or publication.date().isoformat() != origin['path_date_hint']
                or timepoint(publication.isoformat() + '+08:00') > timepoint(task['cutoff'])):
            return None
        year = int(origin['title'][:4])
        if publication.date().isoformat() <= f'{year}-06-30':
            return None
        return year, parts
    except (ContractError, KeyError, TypeError, ValueError, UnicodeError):
        return None


def _match(parts, pattern):
    matches = [(index, part, match) for index, part in enumerate(parts) if part['locator'].startswith('段落')
               for match in re.finditer(pattern, part['text'])]
    return matches[0] if len(matches) == 1 else None


def _reading(hit, group, field, label, unit, *, direction=None, qualifier=None):
    index, part, match = hit
    raw = match[group]
    if direction:
        raw = ('-' if match[direction] == '下降' else '') + raw
    return dict(field='miit_' + field, label=label, raw=raw, unit=unit, qualifier=qualifier,
                locator=dict(kind=CELL_KIND, part_index=index, part=part['locator'],
                             start=match.start(), end=match.end(), quote=match[0], field=field))


def _battery_metrics(parts):
    num = _NUMBER
    if hit := _match(parts, rf'全国锂电池总产量(超过|达到)?{num}GWh[，,]同比{_DIRECTION}{num}%'):
        qualifier = 'lower_exclusive' if hit[2][1] == '超过' else None
        yield _reading(hit, 2, 'battery_output', '锂电池总产量', 'GWh', qualifier=qualifier)
        yield _reading(hit, 4, 'battery_yoy', '锂电池产量同比', '%', direction=3)
    if hit := _match(parts, rf'上半年消费型、储能型和动力型锂电池产量分别为{num}GWh、{num}GWh、{num}GWh'):
        for index, (field, name) in enumerate((('consumer', '消费电池'), ('storage', '储能电池'),
                                              ('power', '动力电池')), 1):
            yield _reading(hit, index, 'battery_' + field, name + '产量', 'GWh')
    if hit := _match(parts, rf'全国锂电池出口总额(?:达到|为)?{num}亿元[，,]同比{_DIRECTION}{num}%'):
        yield _reading(hit, 1, 'battery_export', '锂电池出口额', '亿元')
        yield _reading(hit, 3, 'battery_export_yoy', '锂电池出口额同比', '%', direction=2)
    for name, field in (('碳酸锂', 'carbonate'), ('氢氧化锂', 'hydroxide')):
        if hit := _match(parts, rf'电池级{name}产量(?:为)?{num}万吨[，,]同比{_DIRECTION}{num}%'):
            yield _reading(hit, 1, field + '_output', '电池级' + name + '产量', '万吨')
            yield _reading(hit, 3, field + '_yoy', '电池级' + name + '产量同比', '%', direction=2)
    if hit := _match(parts, rf'上半年电池级碳酸锂和氢氧化锂（微粉级）均价分别为{num}万元/吨和{num}万元/吨'):
        yield _reading(hit, 1, 'carbonate_price', '电池级碳酸锂均价', '万元/吨')
        yield _reading(hit, 2, 'hydroxide_price', '电池级微粉氢氧化锂均价', '万元/吨')


def _vehicle_metrics(parts):
    num = _NUMBER
    # Separate paragraphs include June-only readings. Match the whole cumulative clause.
    span = r'(?:1[-—－至]6月|上半年)'
    if not _match(parts, r'上述数据来自中国汽车工业协会'):
        return
    if hit := _match(parts, rf'{span}[，,]新能源汽车产销分别完成{num}万辆和{num}万辆[^。]*新能源汽车新车销量达到汽车新车总销量的{num}%'):
        yield _reading(hit, 2, 'vehicle_sales', _VEHICLE['sales'], '万辆')
        yield _reading(hit, 3, 'vehicle_penetration', _VEHICLE['penetration'], '%')
    if hit := _match(parts, rf'{span}[，,]汽车整车出口[^。]+。(?:其中[，,])?新能源汽车出口{num}万辆'):
        yield _reading(hit, 1, 'vehicle_exports', _VEHICLE['exports'], '万辆')


def bulletin_cells(task, record, scope, channel):
    if channel not in scope['titles'] or not (result := _verified_body(task, record, scope, channel)):
        return ()
    year, parts = result
    cells = _battery_metrics(parts) if channel == ELECTRONICS else _vehicle_metrics(parts)
    return [dict(cell, period=f'{year}-01-01至{year}-06-30', code='全国锂电产业链',
                 basis='工信部锂电行业测算' if channel == ELECTRONICS else '中国汽车工业协会整车统计')
            for cell in cells]


def selected_cells(facts):
    """Resolve a metric only when its source-bound readings agree exactly."""
    groups = {}
    for row in facts:
        if row.get('locator', {}).get('kind') != CELL_KIND:
            continue
        key = (row['period'][:4], row['field'].removeprefix('miit_'))
        groups.setdefault(key, []).append(row)
    return {key: rows[0] for key, rows in groups.items()
            if len({(Decimal(row['raw_value']), row['unit'], row.get('qualifier')) for row in rows}) == 1}


def requested_catalogue(channel, page, scope):
    if not 0 <= page <= MAX_PAGE:
        raise ContractError('工信部目录请求超出登记范围')
    return dict(id=f'{channel}_page_{page}', source='official', operation='release_catalog',
                parameters={'channel': channel, 'page': str(page)}, question_ids=[scope['question_id']],
                selection_reason='按所问半年度和所属栏目查找实际发布的工业统计公报。')
