"""Present raw field availability, never financial facts or absence approval."""
import base64
import hashlib
import json

from .contracts import ContractError, DOCUMENT_CONTENT_INTENT

POLICY = 'source_field_inventory_version'
PCF_FIELDS = ('NAV','PRE_TRADING_DAY','TRADING_DAY','TRADE_CODE','CREATION_REDEMPTION_UNIT',
    'CREATION_REDEMPTION','CREATION_LIMIT','REDEMPTION_LIMIT','MAX_CASH_RATIO',
    'CREATION_LIMIT_PER_ACCT','REDEMPTION_LIMIT_PER_ACCT')


def _present(value):
    return value is not None and (not isinstance(value,str) or value.strip() not in ('','-','--','N/A','null'))


def _field(name, values):
    return dict(name=name,status='present_unverified' if any(_present(v) for v in values)
        else 'empty_or_placeholder',financial_approval=False)


def inventory(task, selected=None):
    version = task.get(POLICY)
    if version is None:return None
    if type(version) is not int or version != 1:
        raise ContractError('原始字段目录版本未登记')
    if task.get('research_mode') != 'current' or task.get('intent') == DOCUMENT_CONTENT_INTENT:
        return None
    from .evidence import current_snapshot_reference, current_query_reference
    from .financial_sources import metadata_enabled, metadata_response, partial_financial_enabled
    wanted = {q['id'] for q in task['questions'] if selected is None or q['id'] in selected}
    records, seen = [], set()
    for source in task.get('source_records',[]):
        if source['id'] in seen:raise ContractError('原件标识重复，不生成字段目录')
        seen.add(source['id'])
        related = [q for q in source.get('question_ids',[]) if q in wanted]
        if not related:continue
        reference = current_snapshot_reference(task,source)
        if (reference is None and source.get('source') == 'ifind' and source.get('operation') == 'basic_data_service'
                and metadata_enabled(task)):
            reference = current_query_reference(task, source)
        if reference is None:continue
        snapshot, groups = source['snapshot'], []
        coverage = None
        if (source.get('source') == 'ifind' and source.get('operation') == 'basic_data_service'
                and partial_financial_enabled(task)):
            parsed = metadata_response('basic_data_service', source['parameters'], snapshot, allow_partial=True)
            coverage = {key: parsed[key] for key in ('requested_code_count', 'returned_code_count',
                'requested_field_count', 'requested_cell_count', 'returned_numeric_cell_count',
                'missing_codes', 'missing_columns', 'missing_values', 'response_coverage_complete',
                'financial_values_approved', 'absence_proved', 'limitation')}
        if source['source']=='official' and source['operation']=='etf_pcf':
            try:
                raw = base64.b64decode(snapshot['original_base64'],validate=True)
                if hashlib.sha256(raw).hexdigest()!=snapshot['original_sha256']:
                    raise ValueError('Original bytes differ')
                rows = json.loads(raw)['result']
                if not isinstance(rows,list) or any(not isinstance(row,dict) for row in rows):
                    raise ValueError('Original rows are not objects')
            except (KeyError,TypeError,ValueError) as error:
                raise ContractError('原始JSON不能逐字核对，不生成字段目录') from error
            for index,row in enumerate(rows):
                groups.append(dict(locator=dict(result=index),fields=[_field(name,[row[name]])
                    for name in PCF_FIELDS if name in row]))
        elif source['source']=='ifind' and isinstance(snapshot.get('tables'),list):
            for index,table in enumerate(snapshot['tables']):
                fields = table.get('table') if isinstance(table,dict) else None
                if not isinstance(fields,dict):continue
                names = sorted(fields) if coverage is not None else fields
                groups.append(dict(locator=dict(table=index),fields=[_field(name,fields[name])
                    for name in names if isinstance(name,str) and isinstance(fields[name],list)]))
        if any(group['fields'] for group in groups) or coverage is not None:
            item = dict(reference=reference,source=source['source'],operation=source['operation'],
                question_ids=related,groups=groups)
            if coverage is not None:
                item['retrieval_coverage'] = coverage
            records.append(item)
    return dict(version=1,records=records,financial_approval=False,absence_proved=False,
        rule='这是已下载原件的字段目录，不是已核验金融事实；不列字段数值，不能作为投资陈述引用。'
        'present_unverified仅表示字段有原始内容；empty_or_placeholder不证明信息不存在。'
        '未提取、未核验、时点不匹配与未取得分别描述，不把已含字段概括为未取得。'
        '字段可能对应不同交易日或估值日，仍须逐项核验单位、日期和用途。目录并非全渠道或全文覆盖，'
        '未列字段不能写成缺失；不能补数、自动采用证据、扩大研究义务或追加模型请求。')
