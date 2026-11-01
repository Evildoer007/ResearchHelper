"""Literal citation properties for the registered NBS PMI release layout.

No financial values, inferred publisher, concatenated excerpt or publication
clock is created. A short reference only selects five actually read anchors.
"""
import copy
import re

from .contracts import ContractError, context, fingerprint

POLICY = 'source_label_selection_version'
ROLE = 'document_source_label'
REFERENCE = 'source_label_ref'
SIGNATORIES = ('国家统计局服务业调查中心', '中国物流与采购联合会')
LIMITATION = '仅核对本页标题、来源站点、原文署名和页面标注日期，不确认法律发布主体、历史首发、金融指标或投资判断。'


def enabled(task):
    value = task.get(POLICY)
    if value is None: return False
    if type(value) is not int or value != 1: raise ContractError('来源标注选择版本未登记')
    return task.get('research_mode') == 'current' and task.get('intent') != '原件内容核对'


def candidates(task, record, parts=None):
    if (not enabled(task) or not isinstance(record, dict) or record.get('source') != 'official'
            or record.get('operation') != 'release_original'):
        return []
    from .document_dates import anchors, _MetaTag
    from .official_sources import article_url, validate_original
    from .evidence import _original_download_profile
    origin = record.get('snapshot', {}).get('official_origin', {})
    if not re.fullmatch(r'\d{4}年\d{1,2}月中国采购经理指数运行情况', origin.get('title', '')): return []
    article_url(origin.get('url'), channel='nbs_release')
    validate_original(task, record)
    dates = anchors(task, record, parts)
    if len(dates) != 1: return []
    actual = _original_download_profile(record)['material']['parts']
    readable = actual if parts is None else parts
    def is_read(part):
        return sum(row.get('locator') == part['locator'] and row.get('text') == part['text'] for row in readable) == 1
    titles = []
    for part in actual:
        if part.get('metadata_field', '').casefold() != 'articletitle': continue
        parser = _MetaTag(); parser.feed(part['text']); parser.close()
        if parser.unexpected or len(parser.tags) != 1: continue
        attributes = parser.tags[0][1]
        if (attributes.get('name') or attributes.get('property')) != part['metadata_field']: continue
        if attributes.get('content') == origin['title'] and is_read(part): titles.append(part)
    headers = [part for part in actual if part['locator'].startswith('标题')
               and part['text'] == origin['title'] + ' - 国家统计局' and is_read(part)]
    sections = [index for index, part in enumerate(actual)
                if part['text'].strip() == '一、中国制造业采购经理指数运行情况']
    if len(titles) != 1 or len(headers) != 1 or not sections or sections[0] < 2: return []
    bylines = actual[sections[0] - 2:sections[0]]
    if tuple(part['text'] for part in bylines) != SIGNATORIES or not all(is_read(part) for part in bylines): return []
    basis = lambda part: dict(part=part['locator'], quote=part['text'])
    label = dict(version=1, title=origin['title'], site_label='国家统计局',
        title_basis=basis(titles[0]), page_header=basis(headers[0]),
        signatories=[basis(part) for part in bylines],
        declared_date=dict(value=dates[0]['date'], part=dates[0]['part'], quote=dates[0]['quote']))
    reference = 'source_label_' + fingerprint(dict(task=context(task), artifact_id=record['id'],
        snapshot_hash=record['snapshot_hash'], label=label))[:24]
    return [dict(reference=reference, label=label, limitation=LIMITATION,
                 source_or_financial_approval=False, first_publication_verified=False)]


def specification(task, record, candidate, ident, question_ids, review):
    label = copy.deepcopy(candidate['label']); day = label['declared_date']; header = label['page_header']
    from .evidence import current_original_reference
    return dict(id=ident, artifact_id=record['id'], object='来源标注', nature='已实现事实', unit='不适用',
        question_ids=copy.deepcopy(question_ids), text=header['quote'], period=day['value'], date_role=ROLE,
        source_label=label, record_locator=dict(part=header['part'], period_part=day['part'], period_quote=day['quote']),
        published_at=None, publication=dict(at=None, basis_ref=None, precision='未知', timezone='未知',
            certainty='未知', basis='首次公开时间未知，当前可得性另由实际取数账本核验'),
        availability=current_original_reference(task, record), review=copy.deepcopy(review))


def expand(task, record, spec):
    if REFERENCE not in spec: return spec
    if set(spec) != {'id', 'artifact_id', REFERENCE, 'question_ids', 'review'}:
        raise ContractError('来源标注短引用不重复填写日期、标题、主体或批准字段')
    if spec['artifact_id'] != record['id']:
        raise ContractError('来源标注短引用不能借用另一原件标识')
    rows = [row for row in candidates(task, record) if row['reference'] == spec[REFERENCE]]
    if len(rows) != 1: raise ContractError('来源标注引用不属于当前任务、原件和版本')
    return specification(task, record, rows[0], spec['id'], spec['question_ids'], spec['review'])


def validate(item, task, record):
    forbidden = {'value', 'raw_value', 'raw_token', 'raw_unit', 'conversion_factor', 'observation_date',
        'observation_period', 'reporting_period_basis', 'calculation_context', 'calculation_context_basis'}
    label = item.get('source_label')
    if (not enabled(task) or item.get('date_role') != ROLE or item.get('object') != '来源标注'
            or item.get('nature') != '已实现事实' or item.get('unit') != '不适用' or forbidden.intersection(item)
            or not isinstance(label, dict) or type(label.get('version')) is not int or label['version'] != 1):
        raise ContractError('来源标注只能核对当前原页属性，不能作为数值、条款或金融观测')
    rows = [row for row in candidates(task, record) if row['label'] == label]
    if len(rows) != 1: raise ContractError('来源标题、署名、页面日期或原件位置已变化，未采用标注')
    expected = specification(task, record, rows[0], item['id'], item['question_ids'], {})
    for key in ('text', 'period', 'record_locator'):
        if item.get(key) != expected[key]: raise ContractError('来源标注须保留同一实际原页的完整标题及日期定位')
