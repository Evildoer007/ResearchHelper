"""Pure decoding of retained basicData financial cells and controlled definitions.

``definitions`` is an indicator-keyed, caller-controlled catalogue, never model
output or task input. Each entry has ``claims`` and ``basis``. Claims explicitly
bind indicator, unit, currency, accounting_standard, metric, statement_scope,
report_type (the C value), parameter_order (D/C), period_kind (cumulative), and
applicable_codes. Basis retains kind (vendor_document/vendor_metadata), source,
locator, obtained_at, payload and hash. Its payload must contain the exact
claims. The loader must authenticate provenance; a self-consistent hash alone
does not authenticate supplier facts. Synthetic fixtures prove no real units.

No I/O, unit conversion, identity approval, publication dating or historical
PIT approval is performed here. Request policy and acquisition-ledger checks
remain the caller's responsibility. Missing definitions keep cells raw-only.
"""

from __future__ import annotations

import copy
import re

from .contracts import ContractError, FINANCIAL_AMOUNT_UNITS, fingerprint, timepoint
from .financial_sources import metadata_response


_METRICS = {'revenue', 'operating_cost', 'net_profit', 'operating_cash_flow',
            'parent_net_profit'}
_CLAIMS = {'indicator', 'unit', 'currency', 'accounting_standard', 'metric',
           'statement_scope', 'report_type', 'parameter_order', 'period_kind',
           'applicable_codes'}


def _require(condition, message):
    if not condition:
        raise ContractError(message)


def _definition(indicator, definition, obtained_at):
    _require(isinstance(definition, dict), '缺少受控字段定义')
    claims, basis = definition.get('claims'), definition.get('basis')
    _require(isinstance(claims, dict) and _CLAIMS <= set(claims), '字段定义缺少单位、币种、指标或报表口径')
    _require(claims['indicator'] == indicator, '字段定义指标不一致')
    _require(claims['unit'] in FINANCIAL_AMOUNT_UNITS, '字段金额单位未登记')
    _require(isinstance(claims['currency'], str) and re.fullmatch(r'[A-Z]{3}', claims['currency']), '字段币种缺失或无效')
    _require(claims['accounting_standard'] in ('CAS', 'IFRS', 'US_GAAP'), '会计准则未登记')
    _require(claims['metric'] in _METRICS, '财务指标语义未登记')
    _require(claims['statement_scope'] in ('consolidated', 'parent'), '报表范围未登记')
    _require(claims['parameter_order'] == ['D', 'C'] and claims['period_kind'] == 'cumulative', '须明确D/C顺序和本年累计口径')
    _require(isinstance(claims['report_type'], str) and bool(claims['report_type']), '缺少C报表类型定义')
    codes = claims['applicable_codes']
    _require(isinstance(codes, list) and bool(codes) and all(isinstance(code, str) for code in codes)
             and len(codes) == len(set(codes)), '定义须显式绑定适用代码，不从A股推定币种或准则')
    if 'applicable_periods' in claims:
        periods = claims['applicable_periods']
        _require(isinstance(periods, list) and bool(periods)
                 and all(isinstance(period, str) and re.fullmatch(r'\d{4}(0331|0630|0930|1231)', period)
                         for period in periods)
                 and len(periods) == len(set(periods)), '定义适用报告期须为明确且不重复的D日期列表')
    _require(isinstance(basis, dict) and basis.get('kind') in ('vendor_document', 'vendor_metadata'), '缺少供应商文档或元数据依据')
    _require(all(isinstance(basis.get(key), str) and basis[key].strip()
                 for key in ('source', 'locator', 'obtained_at', 'hash')), '定义依据缺少来源、定位、时间或哈希')
    payload = basis.get('payload')
    _require(isinstance(payload, dict) and payload.get('claims') == claims
             and fingerprint(payload) == basis['hash'], '定义与保留依据不一致')
    _require(timepoint(basis['obtained_at']) <= timepoint(obtained_at), '定义依据晚于取数，禁止未来信息回填')
    return claims


def basic_financial_contract(record, definitions):
    """Return detached registered fields, table contexts and original cell locators.

    ``fields`` maps indicator to its literal field definition; ``table_contexts``
    uses the existing fixed_fields/row_fields/column_fields protocol. ``cells``
    retains report_end separately from the cumulative ``period``. The response's
    optional ``time`` is NOT treated as a disclosure date. Callers must explicitly
    opt into current-snapshot usage and retain their normal validation gates.
    """
    result = dict(version=1, status='raw_only', raw_only=True, fields={},
                  registered_numeric_fields=[], table_contexts={}, cells=[],
                  row_fields=[], fixed_fields={}, column_fields={},
                  unavailable_fields=[], basis_templates={}, reasons=[],
                  context_is_approval=False, historical_availability_verified=False,
                  usage_scope='current_snapshot', publication_time_verified=False)
    try:
        _require(isinstance(record, dict) and isinstance(definitions, dict), '记录及受控定义须为对象')
        _require(record.get('kind') == 'api' and record.get('source') == 'ifind'
                 and record.get('operation') == 'basic_data_service', '不是iFinD基础财务记录')
        _require(record.get('status') == '成功待核验', '响应状态未明确成功')
        _require(record.get('research_mode', 'current') == 'current', '当前快照不得批准历史PIT')
        params, snapshot = record.get('parameters'), record.get('snapshot')
        digest = record.get('snapshot_hash', record.get('hash'))
        _require(isinstance(params, dict) and set(params) == {'codes', 'indipara'}
                 and isinstance(snapshot, dict), '原始请求或响应结构不完整')
        _require(fingerprint(snapshot) == digest, '原始响应哈希不一致')
        _require(fingerprint(dict(source='ifind', operation='basic_data_service', parameters=params))
                 == record.get('request_hash'), '原始请求哈希不一致')
        obtained_at = record['obtained_at']
        acquired_day = timepoint(obtained_at).date().isoformat()
        _require(isinstance(record.get('id'), str) and bool(record['id']), '缺少原始记录编号')
        codes = params['codes'].split(',')
        _require(bool(codes) and len(codes) == len(set(codes))
                 and all(re.fullmatch(r'\d{6}\.(SH|SZ)', code) for code in codes), '请求代码缺失或重复')
        requested = params['indipara']
        _require(isinstance(requested, list) and bool(requested), '缺少显式指标和D/C参数')
        requests = {}
        for item in requested:
            _require(isinstance(item, dict) and set(item) == {'indicator', 'indiparams'}, '请求指标形状无效')
            indicator, values = item['indicator'], item['indiparams']
            _require(isinstance(indicator, str) and indicator not in requests, '请求指标缺失或重复')
            _require(isinstance(values, list) and len(values) == 2
                     and all(isinstance(value, str) for value in values), '须显式提供D和C')
            _require(re.fullmatch(r'\d{4}(0331|0630|0930|1231)', values[0]), 'D必须为显式季度末')
            day = values[0][:4] + '-' + values[0][4:6] + '-' + values[0][6:]
            timepoint(day)
            _require(day <= acquired_day, '报告期晚于取数，禁止未来信息')
            if record.get('cutoff'):
                _require(day <= timepoint(record['cutoff']).date().isoformat(), '报告期晚于研究截止日期')
            requests[indicator] = values
        _require(len({tuple(values) for values in requests.values()}) == 1, '同次请求报告期或C报表范围不一致')
        metadata_response('basic_data_service', params, snapshot, allow_partial=True)
        result.update(artifact_id=record['id'], snapshot_hash=digest)
    except (ContractError, KeyError, TypeError, ValueError, AttributeError) as exc:
        result['reason'] = str(exc)
        result['reasons'].append(str(exc))
        return result

    valid = {}
    for indicator, values in requests.items():
        try:
            claims = _definition(indicator, definitions.get(indicator), obtained_at)
            _require(values[1] == claims['report_type'], 'C报表类型与定义范围不一致')
            _require('applicable_periods' not in claims or values[0] in claims['applicable_periods'],
                     'D报告期不在受控定义适用范围，不能跨期沿用校准依据')
            valid[indicator] = claims
        except (ContractError, KeyError, TypeError, ValueError) as exc:
            result['reasons'].append(indicator + ': ' + str(exc))

    for index, table in enumerate(snapshot['tables']):
        fields, contexts = {}, {}
        for indicator, claims in valid.items():
            values = table.get('table', {}).get(indicator)
            if values is None or values[0] is None:
                continue
            code = table['thscode']
            if code not in claims['applicable_codes']:
                result['reasons'].append(code + '/' + indicator + ': 代码不在定义适用范围')
                continue
            end = requests[indicator][0]
            day = end[:4] + '-' + end[4:6] + '-' + end[6:]
            period = end[:4] + '年1-' + str(int(end[4:6])) + '月'
            context = dict(currency=claims['currency'], accounting_standard=claims['accounting_standard'],
                           accounting_scope=claims['statement_scope'], period=period, metric=claims['metric'])
            field = dict(unit=claims['unit'], metric=claims['metric'],
                         definition_hash=fingerprint(definitions[indicator]),
                         definition_basis=copy.deepcopy(definitions[indicator]['basis']))
            fields[indicator], contexts[indicator] = field, context
            result['fields'][indicator] = field
            result['cells'].append(dict(record_locator=dict(table=index, row=0, field=indicator),
                provider_code=code, raw_value=values[0], value=values[0], raw_unit=claims['unit'],
                unit=claims['unit'], conversion_factor=1, report_end=day, period=period,
                period_kind='cumulative', calculation_context=dict(context, entity_id=code)))
        if fields:
            if len({context['accounting_scope'] for context in contexts.values()}) != 1:
                result.update(status='raw_only', raw_only=True, fields={}, cells=[], table_contexts={},
                              reason='同一C报表类型的字段定义存在合并范围冲突')
                result['reasons'].append(result['reason'])
                return result
            common = {key: value for key, value in next(iter(contexts.values())).items() if key != 'metric'
                      if all(context[key] == value for context in contexts.values())}
            columns = {key: {name: context[key] for name, context in contexts.items()}
                       for key in next(iter(contexts.values())) if key not in common}
            result['table_contexts'][str(index)] = dict(unit_kind='financial_amount', fields=fields,
                registered_numeric_fields=sorted(fields), row_fields=['entity_id'], fixed_fields=common,
                column_fields=columns, unavailable_fields=[], period_kind='cumulative')
    if result['cells']:
        result.update(status='registered_constraints', raw_only=False,
                      registered_numeric_fields=sorted(result['fields']), row_fields=['entity_id'])
        contexts = list(result['table_contexts'].values())
        result['fixed_fields'] = {key: value for key, value in contexts[0]['fixed_fields'].items()
                                  if all(row['fixed_fields'].get(key) == value for row in contexts)}
        result['column_fields'] = {key: value for key, value in contexts[0]['column_fields'].items()
                                   if all(row['column_fields'].get(key) == value for row in contexts)}
        template = dict(kind='api', artifact_id=record['id'], snapshot_hash=digest)
        result['basis_template'] = template
        for row in contexts:
            for key in set(row['fixed_fields']) | set(row['column_fields']) | {'entity_id'}:
                result['basis_templates'][key] = dict(template, field=key)
    else:
        result['reason'] = '; '.join(result['reasons']) or '没有同时具备真实有限数值和完整字段定义的单元'
    return copy.deepcopy(result)
