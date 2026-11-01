"""Task-scoped iFinD economic series retrieval, separate from numeric approval.

The official HTTP manual defines EDB requests but does not certify the unit,
meaning, original release time or revision vintage of any particular series.
"""
from __future__ import annotations

import re
import json

from .contracts import ContractError, market_date, timepoint

EDB_DOCUMENTATION = 'https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/help-center/manual.html'
ECONOMIC_REQUEST_REGISTRY = {'ifind.edb_service': {
    'version': 1, 'kind': '宏观与行业原始序列', 'parameters': ['indicators', 'startdate', 'enddate'],
    'raw_only': True, 'fields': {}, 'documentation': EDB_DOCUMENTATION,
    'limitations': '仅查询当前问题明确的EDB指标ID。指标含义、单位、频率、首次公开及修订版本未核验；不能直接作数字证据或计算输入。',
}}
INDICATOR = re.compile(r'(?<![A-Za-z0-9_])M[0-9]{9}(?![A-Za-z0-9_])')
CURRENT_INDICATOR = re.compile(r'(?<![A-Za-z0-9_])[MG][0-9]{9}(?![A-Za-z0-9_])')
DISPLAY_ID = re.compile(r'[A-Z][0-9]{9}')


def discovery_enabled(task):
    return (type(task.get('economic_discovery_version')) is int
            and task['economic_discovery_version'] == 1
            and task.get('research_mode') == 'current')


def search_response(payload):
    """Decode one supplier JSON block, never infer metadata from the question.

    The supplier returns text containing an independently described JSON list.
    Keep that complete text in the snapshot; this is only a derived directory.
    """
    if (not isinstance(payload, dict) or set(payload) != {'text'}
            or not isinstance(payload['text'], str) or not 0 < len(payload['text']) <= 200000):
        raise ContractError('EDB搜索须保留完整文本响应，不接受预先批准的指标列表')
    text = payload['text']
    if any(word in text for word in ('无权限', '调用次数上限', '调用额度已用完')):
        raise ContractError('EDB搜索权限或额度不足，不自动重试')
    blocks = re.findall(r'```json\s*\n([\s\S]*?)\n```', text)
    if len(blocks) != 1:
        if not blocks and text.strip() == '未查询到符合要求的EDB指标':
            return []
        raise ContractError('EDB搜索未返回唯一的明确JSON指标列表，保留原文不猜代码')
    try:
        rows = json.loads(blocks[0])
    except (ValueError, TypeError):
        raise ContractError('EDB搜索指标列表不是有效JSON') from None
    fields = {'country', 'dataSource', 'displayId', 'endDate', 'freq', 'id', 'indexName', 'node', 'startDate', 'unit'}
    if (not isinstance(rows, list) or len(rows) > 1000
            or any(not isinstance(row, dict) or not fields <= set(row)
                or any(not isinstance(row[key], str) or len(row[key]) > 2000 for key in fields)
                or not DISPLAY_ID.fullmatch(row['displayId'])
                or not row['indexName'].strip() or not row['dataSource'].strip()
                or not row['freq'].strip() or not row['unit'].strip()
                or not re.fullmatch(r'\d{8}', row['startDate'])
                or not re.fullmatch(r'\d{8}', row['endDate']) for row in rows)
            or len({row['displayId'] for row in rows}) != len(rows)):
        raise ContractError('EDB搜索目录字段缺失、代码重复或标识不明确，不取数字id代替displayId')
    for row in rows:
        for key in ('startDate', 'endDate'):
            value = row[key]
            timepoint(value[:4]+'-'+value[4:6]+'-'+value[6:])
        if row['startDate'] > row['endDate']:
            raise ContractError('EDB搜索指标覆盖区间倒置')
    return [{key: row[key] for key in sorted(fields)} for row in rows]


def discovered_indicators(task):
    """Only current task's actual, unmodified search acquisitions unlock IDs."""
    if not discovery_enabled(task):
        return []
    from .financial_sources import _metadata_records
    return [dict(row, artifact_id=source['record']['id'], snapshot_hash=source['record']['hash'],
                 metadata_only=True, financial_approval=False, historical_vintage_approved=False)
            for source in _metadata_records(task, 'search_edb') for row in source['parsed']]


def available_economic_indicators(task):
    return sorted(set(declared_economic_indicators(task)) | {row['displayId'] for row in discovered_indicators(task)})


def declared_economic_indicators(task):
    """Read explicit IDs; never turn model memory or securities into EDB IDs."""
    texts = [task.get('question', ''), *task.get('objects', [])]
    pattern = (CURRENT_INDICATOR if type(task.get('economic_identifier_namespace_version')) is int
               and task['economic_identifier_namespace_version'] == 2 else INDICATOR)
    return sorted({match.group() for text in texts if isinstance(text, str) for match in pattern.finditer(text)})


def validate_economic_request(task, operation, parameters):
    definition = ECONOMIC_REQUEST_REGISTRY.get('ifind.' + str(operation))
    if definition is None:
        raise ContractError('宏观接口未登记')
    if not isinstance(parameters, dict) or set(parameters) != set(definition['parameters']):
        raise ContractError('EDB请求须明确指标ID和日期窗口，不接受额外参数')
    if not all(isinstance(value, str) and value for value in parameters.values()):
        raise ContractError('EDB指标与日期须为明确文字，不补默认值')
    identifiers = parameters['indicators'].split(',')
    if (len(identifiers) != len(set(identifiers))
            or not set(identifiers).issubset(available_economic_indicators(task))):
        raise ContractError('EDB仅查询本任务明确或实际搜索取得的displayId，不猜代码、重复指标或借用其他任务目录')
    dates = [parameters[key] for key in ('startdate', 'enddate')]
    for value in dates:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ContractError('EDB日期须为YYYY-MM-DD，不改写实际请求')
        timepoint(value)
    if not task.get('cutoff') or dates[0] > dates[1] or dates[1] > market_date(task['cutoff']):
        raise ContractError('EDB窗口倒置、未绑定截止时点或越过北京时间截止日期')
    return dates


def economic_request_shapes(task):
    identifiers = available_economic_indicators(task)
    if discovery_enabled(task) and not identifiers:
        return []  # Search first; never offer a free-form guessed EDB code.
    questions = [item['id'] for item in task['questions']]
    return [{'qualified_id_for_lookup_only': 'ifind.edb_service', 'source': 'ifind', 'operation': 'edb_service',
        'parameter_keys': ['indicators', 'startdate', 'enddate'], 'date_format': 'YYYY-MM-DD',
        'date_timezone': 'Asia/Shanghai', 'declared_indicators': identifiers, 'numeric_fields': [], 'raw_only': True,
        'prerequisite': '指标ID须由当前问题或研究对象明确声明，或来自本任务实际search_edb响应；不从模型记忆猜映射。',
        **({'search_candidates': discovered_indicators(task),
            'metadata_parameter_schema': {'type': 'object', 'additionalProperties': False,
                'required': ['indicators', 'startdate', 'enddate'], 'properties': {
                    'indicators': {'type': 'string', 'pattern': '^(?:'+'|'.join(map(re.escape, identifiers))+')(?:,(?:'+'|'.join(map(re.escape, identifiers))+'))*$'},
                    'startdate': {'type': 'string', 'pattern': r'^\d{4}-\d{2}-\d{2}$'},
                    'enddate': {'type': 'string', 'pattern': r'^\d{4}-\d{2}-\d{2}$'}}},
            'selection_rule': '逐项核对名称、地区、来源、频率、单位及覆盖期；年度、累计、当月及预测指标不能相互替代。搜索只提供候选，不批准数值或历史版本。'} if discovery_enabled(task) else {}),
        'limitations': economic_record_contract(task)['limitations'],
        'example': {'id': 'shape_example', 'source': 'ifind', 'operation': 'edb_service',
            'parameters': {'indicators': identifiers[0], 'startdate': market_date(task['cutoff']), 'enddate': market_date(task['cutoff'])},
            'question_ids': questions[:1], 'selection_reason': '仅接口形状示例，不是默认指标或交易日'} if identifiers and questions else None}]


def economic_record_contract(task=None):
    if task is not None and discovery_enabled(task):
        return {'version': 2, 'raw_only': True, 'numeric_evidence_registered': False,
            'documentation': EDB_DOCUMENTATION,
            'limitations': 'EDB代码可来自本任务实际搜索。独立核对指标名称、地区、频率和原始来源；搜索元数据不自动批准数值、连续期间覆盖或历史首次可得。原始序列和修订信息完整保留，不直接作计算输入。',
            'vintage': '留存当前取得版本，不证明历史当时可得',
            'unit': '候选目录有字面单位，数值适用性仍待联合核验', 'publication': '未核验'}
    return {'version': 1, 'raw_only': True, 'numeric_evidence_registered': False,
            'documentation': EDB_DOCUMENTATION, 'limitations': ECONOMIC_REQUEST_REGISTRY['ifind.edb_service']['limitations'],
            'vintage': '留存当前取得版本，不证明历史当时可得', 'unit': '未核验', 'publication': '未核验'}


def with_economic_navigation_coverage(task, coverage):
    """A searched directory is an unfinished data route, not a numeric source.

    Readable files for the same question cannot discharge a declared EDB
    discovery route. One actual, bound business response closes navigation;
    missing observations and financial applicability are still checked later.
    No indicator is selected here and a directory-only question adds no route.
    """
    version = task.get('economic_navigation_version')
    if version is None:
        return coverage
    if type(version) is not int or version != 1:
        raise ContractError('宏观来源导航版本未登记')
    if not discovery_enabled(task) or task.get('intent') == '原件内容核对':
        return coverage
    from .financial_sources import _metadata_records
    from .evidence import _current_api_reference
    eligible = set()
    for question in task.get('questions', []):
        needs = question.get('evidence_needs', [])
        scopes = question.get('evidence_need_scopes', {})
        if (question.get('required', True) and question.get('status') != '已回答'
                and question.get('answer_scope') != 'general_definition' and needs
                and not all(scopes.get(need) == 'query_provenance' for need in needs)):
            eligible.add(question['id'])
    candidates = {}
    searches = _metadata_records(task, 'search_edb')
    for source in searches:
        identifiers = {row['displayId'] for row in source['parsed']}
        if not identifiers:
            continue  # An actual empty search is not proof that data exists.
        for question_id in eligible.intersection(source['record'].get('question_ids', [])):
            candidates.setdefault(question_id, set()).update(identifiers)
    attempted = set()
    for record in task.get('source_records', []):
        if record.get('source') != 'ifind' or record.get('operation') != 'edb_service':
            continue
        if _current_api_reference(task, record, {'ifind.edb_service'}, 'current_snapshot') is None:
            continue
        try:
            validate_economic_request(task, 'edb_service', record['parameters'])
        except (ContractError, KeyError, TypeError):
            continue
        identifiers = set(record['parameters']['indicators'].split(','))
        for question_id in set(record.get('question_ids', [])).intersection(candidates):
            earlier = {row['displayId'] for source in searches
                if question_id in source['record'].get('question_ids', [])
                and source['record']['acquisition']['dispatch_index'] < record['acquisition']['dispatch_index']
                and timepoint(source['record']['obtained_at']) <= timepoint(record['obtained_at'])
                for row in source['parsed']}
            if identifiers and identifiers <= earlier:
                attempted.add(question_id)
    pending = sorted(set(candidates) - attempted)
    return dict(coverage, ready=coverage['ready'] and not pending,
        economic_navigation_version=1, pending_economic_question_ids=pending,
        economic_data_route_attempted_question_ids=sorted(attempted),
        economic_directory_question_ids=sorted(candidates),
        economic_numeric_approval=False,
        economic_route_rule='已搜索到目录的数值需求须继续取得本任务EDB业务响应；目录、其他原件及接口尝试均不证明数值完整或适用，缺失值保留。')
