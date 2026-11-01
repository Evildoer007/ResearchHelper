"""Current-vintage economic observations bound to real search and series bytes.

This adapter owns statistical identity, frequency, literal units and source
positions. It does not infer first publication, a historical vintage, causal
effects, forecasts or a registered calculation's missing context.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import math
import re

from .contracts import ContractError, fingerprint, market_date, timepoint
from .economic_sources import discovered_indicators, validate_economic_request

VERSION = 1
DOCUMENTATION = 'https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/help-center/manual.html'
LIMITATION = '仅本任务取得的当前统计序列，所属期不等于首发时点，供应商rtime不证明历史版本；不直接批准因果、预测或投资判断。'


def enabled(task):
    return (type(task.get('economic_observation_version')) is int
            and task['economic_observation_version'] == VERSION
            and task.get('research_mode') == 'current'
            and task.get('intent') != '原件内容核对')


def is_series(record):
    return isinstance(record, dict) and record.get('source') == 'ifind' and record.get('operation') == 'edb_service'


def _period(day, frequency):
    if frequency == 'M':
        return day[:7]
    if frequency == 'Y':
        return day[:4]
    if frequency == 'Q':
        return day[:4]+'年第'+str((int(day[5:7])-1)//3+1)+'季度'
    return day


def _numeric(raw):
    if type(raw) not in (str, int, float) or isinstance(raw, str) and not re.fullmatch(r'-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?', raw):
        raise ValueError('Non-numeric original cell')
    number = Decimal(str(raw))
    if not number.is_finite() or abs(number.adjusted()) > 308:
        raise ValueError('Unrepresentable original cell')
    value = int(number) if number == number.to_integral_value() else float(number)
    if not math.isfinite(value) or Decimal(str(value)) != number:
        raise ValueError('Do not round an original observation')
    return value


def contract(task, record):
    """Pure projection; missing/null cells remain gaps, never zeroes."""
    result = dict(version=VERSION, status='unavailable', cells=[], gaps=[],
                  numeric_approval=False, historical_vintage_approved=False, event_causality_approved=False)
    if not enabled(task) or not is_series(record):
        result['reason'] = '本任务未登记当前宏观观测核验'
        return result
    from .evidence import _current_api_reference
    reference = _current_api_reference(task, record, {'ifind.edb_service'}, 'current_snapshot')
    if reference is None:
        result['reason'] = '宏观序列未绑定本任务实际取数账本'
        return result
    try:
        validate_economic_request(task, 'edb_service', record['parameters'])
        snapshot, params = record['snapshot'], record['parameters']
        if snapshot.get('errorcode') not in (0, '0') or isinstance(snapshot.get('errorcode'), bool):
            raise ContractError('宏观业务响应未成功')
        echo = snapshot.get('inputParams', {})
        if (echo.get('zb_str') != params['indicators'] or echo.get('sdate') != params['startdate'].replace('-', '')
                or echo.get('edate') != params['enddate'].replace('-', '')):
            raise ContractError('宏观请求代码或窗口与实际回显不一致')
        tables = snapshot.get('tables')
        if not isinstance(tables, list) or len(tables) > 1000:
            raise ContractError('宏观原始表结构不完整')
        requested = params['indicators'].split(',')
        metadata = {}
        records = {row['id']: row for row in task['source_records']}
        for candidate in discovered_indicators(task):
            parent = records[candidate['artifact_id']]
            if (parent['acquisition']['dispatch_index'] >= record['acquisition']['dispatch_index']
                    or timepoint(parent['obtained_at']) > timepoint(record['obtained_at'])):
                continue
            metadata.setdefault(candidate['displayId'], []).append(candidate)
        seen = set()
        for table_index, table in enumerate(tables):
            identifiers = table.get('id') if isinstance(table, dict) else None
            if (not isinstance(identifiers, list) or len(identifiers) != 1
                    or identifiers[0] not in requested or identifiers[0] in seen):
                raise ContractError('宏观响应含额外、重复或不明确指标，不猜代码')
            code = identifiers[0]
            seen.add(code)
            choices = metadata.get(code, [])
            semantics = {fingerprint({key: value for key, value in row.items()
                if key not in ('artifact_id', 'snapshot_hash')}) for row in choices}
            if not choices or len(semantics) != 1:
                result['gaps'].append(dict(indicator=code, reason='缺少同任务先行取得且一致的完整指标定义'))
                continue
            definition = choices[0]
            if (definition['freq'] not in ('D', 'W', 'M', 'Q', 'Y')
                    or re.search(r'预测|forecast', definition['indexName'], re.I)):
                result['gaps'].append(dict(indicator=code, reason='频率未登记或序列含预测，不自动当作已实现事实'))
                continue
            dates, values = table.get('time'), table.get('value')
            names, revisions = table.get('index_name'), table.get('rtime')
            if (not isinstance(dates, list) or not isinstance(values, list) or len(dates) != len(values)
                    or len(dates) > 100000 or names is not None and (not isinstance(names, list) or len(names) != len(dates))
                    or revisions is not None and (not isinstance(revisions, list) or len(revisions) != len(dates))):
                result['gaps'].append(dict(indicator=code, reason='原始日期、数值或修订信息错位，不截断或补齐'))
                continue
            labels = [_period(day, definition['freq']) for day in dates
                      if isinstance(day, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', day)]
            if len(labels) != len(set(labels)):
                result['gaps'].append(dict(indicator=code, reason='同一统计期重复，不选择首值或平均'))
                continue
            pending = []
            for index, (day, raw) in enumerate(zip(dates, values)):
                locator = dict(table=table_index, row=index, field='value')
                try:
                    if not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
                        raise ValueError('Invalid source date')
                    timepoint(day)
                    if not params['startdate'] <= day <= params['enddate'] or day > market_date(task['cutoff']):
                        raise ValueError('Observation outside requested cutoff')
                    if not definition['startDate'] <= day.replace('-', '') <= definition['endDate']:
                        raise ValueError('Observation outside selected metadata coverage')
                    if names is not None and names[index] != definition['indexName']:
                        raise ValueError('Actual series name differs from selected definition')
                    revision = revisions[index] if revisions is not None else ''
                    if not isinstance(revision, str) or revision and (not re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', revision)
                            or revision[:10] > market_date(task['cutoff'])):
                        raise ValueError('Invalid or future supplier revision date')
                    if revision:
                        datetime.strptime(revision, '%Y-%m-%d %H:%M:%S')
                    period = _period(day, definition['freq'])
                    value = _numeric(raw)
                    metadata_reference = dict(artifact_id=definition['artifact_id'], snapshot_hash=definition['snapshot_hash'], display_id=code)
                    context = dict(indicator=code, name=definition['indexName'], country=definition['country'],
                        data_source=definition['dataSource'], frequency=definition['freq'], unit=definition['unit'],
                        observation_date=day, period=period, provider_revision_time=revision,
                        metadata_reference=metadata_reference)
                    field_definition = dict(registry='ifind.edb_service.observation', version=VERSION, field='value',
                        definition=dict(unit=definition['unit'], raw_unit=definition['unit'], factor=1,
                                        name=definition['indexName'], frequency=definition['freq'], metadata_reference=metadata_reference),
                        documentation=DOCUMENTATION)
                    identity = dict(mode='economic_indicator_observation', provider_code=code, object=code, basis=metadata_reference)
                    candidate_id = 'rh_edb_cell_'+fingerprint(dict(task=task['id'], version=task['version'],
                        artifact=record['id'], snapshot=record['hash'], locator=locator, context=context))[:24]
                    pending.append(dict(candidate_id=candidate_id, artifact_id=record['id'], object=code,
                        record_locator=locator, period=period, raw_value=raw, value=value,
                        raw_unit=definition['unit'], unit=definition['unit'], conversion_factor=1,
                        nature='已实现事实', identity=identity, field_definition=field_definition,
                        economic_context=context, calculation_context={}, source_approved=False, limitation=LIMITATION))
                except (ValueError, InvalidOperation, OverflowError):
                    result['gaps'].append(dict(indicator=code, record_locator=locator, reason='原始单元缺失、不可表示或所属期、名称、修订信息不适用，未填零'))
            result['cells'].extend(pending)
        result['gaps'].extend(dict(indicator=code, reason='本次响应未返回该指标，不证明指标不存在') for code in requested if code not in seen)
    except (ContractError, KeyError, TypeError, ValueError):
        result.update(status='unavailable', cells=[], reason='宏观原始请求、响应或指标定义未通过联合核对')
        return result
    result.update(status='registered_observations' if result['cells'] else 'unavailable', availability=reference,
                  response_coverage_complete=not result['gaps'], reason='只提供可逐项核验的实际统计观测，不是采用或计算批准')
    return result


def cell(task, record, locator):
    found = [row for row in contract(task, record)['cells'] if row['record_locator'] == locator]
    if len(found) != 1:
        raise ContractError('宏观观测未绑定唯一真实单元及先行指标定义')
    return found[0]


def selection_spec(task, record, selected, proposal):
    expected = cell(task, record, selected['record_locator'])
    if expected['candidate_id'] != selected['candidate_id']:
        raise ContractError('宏观单元选择已变化，不采用过期候选')
    from .evidence import current_snapshot_reference
    result = {key: expected[key] for key in ('artifact_id', 'object', 'record_locator', 'period', 'raw_value',
        'value', 'raw_unit', 'unit', 'conversion_factor', 'nature', 'identity', 'economic_context')}
    result.update(id=expected['candidate_id'], availability=current_snapshot_reference(task, record), published_at=None,
        publication=dict(at=None, basis_ref=None, precision='未知', timezone='未知', certainty='未知', basis=LIMITATION),
        question_ids=proposal['question_ids'], review=proposal['review'])
    return result


def validate(item, task, record):
    expected = cell(task, record, item.get('record_locator'))
    for key in ('object', 'period', 'raw_value', 'raw_unit', 'unit', 'nature', 'identity', 'field_definition', 'economic_context'):
        if fingerprint(item.get(key)) != fingerprint(expected[key]):
            raise ContractError('宏观原值、定义、单位、期间或来源引用与实际单元不一致')
    if (type(item.get('value')) not in (int, float) or not math.isfinite(item['value'])
            or Decimal(str(item['value'])) != Decimal(str(expected['value']))
            or type(item.get('conversion_factor')) not in (int, float) or item['conversion_factor'] != 1
            or any(key in item for key in ('calculation_context', 'calculation_context_basis'))):
        raise ContractError('宏观原值不得改写、换算或借此补造登记计算口径')
    return True
