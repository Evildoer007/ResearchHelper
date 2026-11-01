"""Bound source addresses for daily NAV extraction, never fact approval.

Literal values, labels and metadata stay attached to their original spans.
Missing identity proofs remain missing. Every selected proposal still passes
the ordinary source, applicability and calculation gates.
"""
import copy
from decimal import Decimal, InvalidOperation
import math
import re

from .contracts import ContractError, context, fingerprint, calculation_context_contract
from .financial_sources import selected_original_span, _AMOUNT_TOKEN


POLICY = 'nav_original_choice_version'
ORIGINAL_ONLY_POLICY = 'nav_original_only_selection_version'
METHOD = 'etf_daily_nav_premium_v1'


def nonofficial_prefix(text, start):
    return bool(re.search(r'(?:累计|参考|估算|预估|预计|复权|IOPV)\s*$',
                          text[max(0, start - 24):start], re.I))


def _span(choice, fragment, start, end):
    positioned, offset = [], 0
    for line in choice['lines']:
        if line['fragment_index'] != fragment:
            continue
        stop = offset + len(line['text'])
        if stop > start and offset < end:
            positioned.append(line['index'])
        offset = stop
    if not positioned or positioned[-1] - positioned[0] >= 200:
        return None
    return dict(start=positioned[0], end=positioned[-1])


def catalogue(task, views):
    from .output_schema import _original_span_choices
    from .evidence import validate_official_nav_number
    if (type(task.get(POLICY)) is not int or task[POLICY] != 1
            or type(task.get('original_number_reference_version')) is not int
            or task['original_number_reference_version'] != 1
            or calculation_context_contract(task, METHOD) is None):
        return None
    methods = {method for question in task.get('questions', []) for method in question.get('method_needs', [])}
    methods.update(spec['method'] for spec in task.get('calculation_specs', []))
    if methods != {METHOD}:
        return None  # Other research keeps its full existing grammar.
    choices = _original_span_choices(task, views)
    original_only = (type(task.get(ORIGINAL_ONLY_POLICY)) is int
                     and task[ORIGINAL_ONLY_POLICY] == 1)
    if (not choices or not any(choice['kind'] == 'original' for choice in choices)
            or not original_only and not any(choice['kind'] == 'api' for choice in choices)):
        return None
    if any(choice['kind'] == 'api' and choice['api_schema']['properties']['version']['enum'] != [2]
           for choice in choices):
        return None
    if any(choice['kind'] == 'api' and len({tuple(sorted(cell['calculation_context']))
            for cell in choice['source']['cell_choices']}) != 1 for choice in choices):
        return None  # The existing grouped grammar handles heterogeneous cells.
    result = dict(choices=choices, numbers={}, units={}, contexts={})
    for choice in choices:
        if choice['kind'] != 'original':
            continue
        source = choice['description']['source']
        binding = source.get('object_binding')
        if not binding:
            return None  # No guessed document-to-security identity.
        object_code = binding['value']
        def add(kind, payload):
            value = dict(source_ref=choice['source_ref'], object=object_code,
                artifact_id=source['artifact_id'], snapshot_hash=source['snapshot_hash'], **payload)
            ident = kind + '_' + fingerprint(value)[:20]
            result[kind + 's'][ident] = value
        for candidate in source.get('listed_etf_unit_candidates', []):
            add('context', dict(field='share_class', value=candidate['value'], span=candidate['span']))
        for anchor_index, date in enumerate(choice['description']['dates']):
            for fragment, part in enumerate(choice['view']['parts']):
                if part['locator'] == date['part'] and date['quote'] in part['text']:
                    start = part['text'].index(date['quote'])
                    span = _span(choice, fragment, start, start + len(date['quote']))
                    if span:
                        add('context', dict(field='valuation_date', value=date['date'], span=span,
                            observation_anchor=anchor_index))
        for fragment, part in enumerate(choice['view']['parts']):
            text = part['text']
            patterns = [('fund_id', object_code.split('.')[0],
                r'(?:基金主代码|(?:场内)?交易代码)\s*[:：]?\s*' + re.escape(object_code.split('.')[0]) + r'(?!\d)'),
                ('currency', 'CNY', r'人民币|(?<![A-Za-z])(?:RMB|CNY)(?![A-Za-z])')]
            for field, value, pattern in patterns:
                for match in re.finditer(pattern, text):
                    span = _span(choice, fragment, match.start(), match.end())
                    if span:
                        add('context', dict(field=field, value=value, span=span))
            for match in re.finditer(r'(?:金额)?单位\s*[:：]?\s*(?:人民币\s*)?元(?!\s*[/／])', text):
                span = _span(choice, fragment, match.start(), match.end())
                if span:
                    add('unit', dict(unit='元', span=span))
            pattern = r'(?:单位净值|基金份额净值)\s*(?:[（(]\s*元\s*[）)]\s*)?(?:为|[:：])?\s*(?P<value>' + _AMOUNT_TOKEN + r')(?![0-9A-Za-z_.,%％])'
            for match in re.finditer(pattern, text):
                if nonofficial_prefix(text, match.start()):
                    continue
                span = _span(choice, fragment, match.start(), match.end())
                if span is None:
                    continue
                quote = selected_original_span(choice['lines'], span)['quote']
                token = match.group('value')
                try:
                    number = Decimal(token.replace(',', '').replace('−', '-'))
                    value = int(number) if number == number.to_integral_value() else float(number)
                    if not number.is_finite() or not math.isfinite(value) or Decimal(str(value)) != number:
                        continue
                    validate_official_nav_number(dict(raw_token=token), quote)
                except (ContractError, InvalidOperation, ValueError, OverflowError):
                    continue
                position = match.start('value')
                offset = 0
                address = None
                for line in choice['lines']:
                    if line['fragment_index'] != fragment:
                        continue
                    for ordinal, amount in enumerate(re.finditer(r'(?<![0-9A-Za-z_.,])' + _AMOUNT_TOKEN + r'(?![0-9A-Za-z_.,])', line['text'])):
                        if offset + amount.start() == position:
                            address = next((item['ref'] for item in choice['number_tokens']
                                if item['line'] == line['index'] and item['ref'].endswith('_' + str(ordinal))), None)
                    offset += len(line['text'])
                if address:
                    add('number', dict(raw_token=token, value=value, span=span, number_ref=address))
    if not result['numbers']:
        return None
    result['binding_ref'] = fingerprint(dict(task=context(task), views=views, version=5,
        numbers=result['numbers'], units=result['units'], contexts=result['contexts']))
    return result


def output_schema(task, views):
    from .output_schema import object_schema, array_schema, _original_quote_selector
    found = catalogue(task, views)
    if found is None:
        return None
    variants = []
    def refs(field, object_code, source_ref=None):
        return [ident for ident, value in found['contexts'].items()
                if value['field'] == field and value['object'] == object_code
                and (source_ref is None or value['source_ref'] == source_ref)]
    for choice in found['choices']:
        ref = choice['source_ref']
        if choice['kind'] == 'api':
            objects = {cell['object'] for cell in choice['source']['cell_choices']}
            if len(objects) != 1:
                return None
            object_code = next(iter(objects))
            existing = set.intersection(*(set(cell['calculation_context']) for cell in choice['source']['cell_choices']))
            missing = [field for field in calculation_context_contract(task, METHOD)['input_fields']['price'] if field not in existing]
            context_fields = {field: {'type': 'string', 'enum': refs(field, object_code)}
                              for field in missing if refs(field, object_code)}
            additions = object_schema(context_fields)
            variants.append(object_schema(dict(kind={'type': 'string', 'enum': ['api']},
                source_ref={'type': 'string', 'enum': [ref]}, selection=choice['api_schema'], context_refs=additions)))
            continue
        source = choice['description']['source']
        properties = choice['description']['schema']['properties']['text_quotes']['items']['properties']
        base = {key: copy.deepcopy(properties[key]) for key in ('id', 'object', 'nature', 'question_ids', 'review', 'observation_anchor')}
        base['source_ref'] = {'type': 'string', 'enum': [ref]}
        span = object_schema({'start': {'type': 'integer', 'minimum': 0, 'maximum': len(choice['lines']) - 1},
                              'end': {'type': 'integer', 'minimum': 0, 'maximum': len(choice['lines']) - 1}})
        variants.append(object_schema(dict(base, kind={'type': 'string', 'enum': ['text']},
            period={'type': 'string', 'minLength': 1}, text_span=span)))
        numbers = [ident for ident, value in found['numbers'].items() if value['source_ref'] == ref]
        units = [ident for ident, value in found['units'].items() if value['source_ref'] == ref]
        if not numbers or not units:
            continue
        object_code = source['object_binding']['value']
        price_dates = {cell['period'] for api in found['choices'] if api['kind'] == 'api'
                       for cell in api['source']['cell_choices'] if cell['object'] == object_code}
        # A targeted original-only supplement has no price view. Keep its
        # actual document dates selectable; the unchanged calculation gate
        # still requires a verified price and the explicit target date.
        unpaired_dates = (not price_dates and type(task.get(ORIGINAL_ONLY_POLICY)) is int
                          and task[ORIGINAL_ONLY_POLICY] == 1)
        dates = [ident for ident in refs('valuation_date', object_code, ref)
                 if unpaired_dates or found['contexts'][ident]['value'] in price_dates]
        if not dates:
            continue
        contexts = {field: {'type': 'string', 'enum': refs(field, object_code)}
            for field in ('fund_id', 'share_class', 'currency') if refs(field, object_code)}
        contexts['valuation_date'] = {'type': 'string', 'enum': dates}
        numeric_base = dict(base); numeric_base.pop('observation_anchor')
        variants.append(object_schema(dict(numeric_base, kind={'type': 'string', 'enum': ['nav']},
            number_ref={'type': 'string', 'enum': numbers}, unit_ref={'type': 'string', 'enum': units},
            context_refs=object_schema(contexts))))
    return object_schema(dict(version={'type': 'integer', 'enum': [5]},
        binding_ref={'type': 'string', 'enum': [found['binding_ref']]}, selections=array_schema({'anyOf': variants})))


def prompt(payload, task):
    from .output_schema import original_span_prompt
    legacy = dict(task); legacy.pop(POLICY, None)
    result = original_span_prompt(payload, legacy)
    for source in result['untrusted_sources']:
        source.pop('number_token_columns', None)
        for part in source.get('parts', []):
            part.pop('number_tokens', None)  # Redundant addresses, not source characters.
    found = catalogue(task, payload['untrusted_sources'])
    result['span_binding_ref'] = found['binding_ref']
    result['original_choices'] = {}
    lookup = {choice['source_ref']: choice for choice in found['choices']}
    for kind in ('numbers', 'units', 'contexts'):
        result['original_choices'][kind] = [dict(ref=ident, **value,
            **selected_original_span(lookup[value['source_ref']]['lines'], value['span']))
            for ident, value in found[kind].items()]
    for number in result['original_choices']['numbers']:
        # The internal legacy address is not another selectable number_ref.
        # Expose one unambiguous public ref while retaining its exact quote.
        number.pop('number_ref', None)
    result['choice_policy'] = dict(version=5, source_or_financial_approval=False,
        rule='nav显式选择原值、单位和逐字段引用；程序只还原绑定原文，不补缺失份额或移动错选位置。不同原件的口径引用仅限同一对象。未提供的字段仍为缺口。')
    result['choice_policy']['missing_nav_context_by_source'] = {
        choice['source_ref']: [field for field in ('fund_id', 'share_class', 'currency', 'valuation_date')
            if not any(value['object'] == choice['description']['source']['object_binding']['value']
                       and value['field'] == field for value in found['contexts'].values())]
        for choice in found['choices'] if choice['kind'] == 'original'}
    return result


def decode(wire, schema, task, views):
    from .output_schema import (original_span_output_schema, decode_original_spans,
        _matches, StructuredOutputError, schema_diagnostic)
    if schema != output_schema(task, views):
        raise StructuredOutputError('original_choices_binding_changed',
            '原件候选与当前任务或实际读取绑定不符，未采用')
    if not _matches(wire, schema):
        raise StructuredOutputError('invalid_original_choices',
            '原件选择不符合本次候选格式，未采用', schema_diagnostic(wire, schema))
    found = catalogue(task, views)
    lookup = {choice['source_ref']: choice for choice in found['choices']}
    legacy = dict(task); legacy.pop(POLICY, None)
    legacy_schema = original_span_output_schema(legacy, views)
    converted, number_contexts = [], {}
    for chosen in wire['selections']:
        item = copy.deepcopy(chosen)
        choice = lookup[item['source_ref']]
        if item['kind'] == 'api':
            additions = [found['contexts'][ref] for ref in item.pop('context_refs').values()]
            item['additional_contexts'] = [dict(source_ref=value['source_ref'], field=value['field'],
                value=value['value'], span=value['span']) for value in additions]
        elif item['kind'] == 'nav':
            number = found['numbers'][item['number_ref']]
            unit = found['units'][item.pop('unit_ref')]
            date_ref = found['contexts'][item['context_refs']['valuation_date']]
            item['observation_anchor'] = date_ref['observation_anchor']
            selected_date = date_ref['value']
            entries = [found['contexts'][ref] for ref in item.pop('context_refs').values()]
            for entry in entries:
                if entry['object'] != item['object'] or entry['field'] == 'valuation_date' and entry['value'] != selected_date:
                    raise ContractError('口径引用对象或估值日与所选原值不一致')
            # The literal NAV label belongs to the selected number, not a free
            # second label elsewhere in the document.
            entries.append(dict(source_ref=item['source_ref'], field='nav_kind', value='official', span=number['span']))
            number_contexts[item['id']] = entries
            if number['object'] != item['object']:
                raise ContractError('净值候选不属于所声明对象')
            item.update(kind='number', number_ref=number['number_ref'], text_span=number['span'],
                unit=unit['unit'], unit_span=unit['span'], period=selected_date, contexts=[])
        converted.append(item)
    value = decode_original_spans(dict(version=legacy_schema['properties']['version']['enum'][0],
        binding_ref=legacy_schema['properties']['binding_ref']['enum'][0], selections=converted), legacy_schema, (legacy, views))
    for spec in value['evidence_specs']:
        if spec['id'] in number_contexts:
            fields, bases = {}, {}
            for entry in number_contexts[spec['id']]:
                original = lookup[entry['source_ref']]
                source = original['description']['source']
                if source['object_binding']['value'] != spec['object'] or entry['field'] in fields:
                    raise ContractError('原件字段重复或跨对象引用')
                fields[entry['field']] = entry['value']
                bases[entry['field']] = dict(kind='material', artifact_id=source['artifact_id'],
                    snapshot_hash=source['snapshot_hash'], **selected_original_span(original['lines'], entry['span']))
            spec.update(calculation_context=fields, calculation_context_basis=bases)
    return value
