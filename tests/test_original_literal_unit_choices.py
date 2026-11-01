"""Original-unit grammar must not offer a unit absent from the read source.

Closed real ETF fixture; no model, network, answer injection or task restart.
"""
import copy
import gzip
import hashlib
import json
import unittest

from tests import ROOT
from researchhelper.contracts import ContractError
from researchhelper.data_requirements import requirement_slots, _source_literal_units
from researchhelper.output_schema import (_original_span_choices, _matches,
    decode_original_spans, original_span_output_schema)

SOURCE = ROOT / 'result/acceptance/autonomous-data-phase-hwsfel2e/task.json.gz'


class OriginalLiteralUnitChoicesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
        with gzip.open(SOURCE, 'rt', encoding='utf-8') as stream:
            cls.task = json.load(stream)
        cls.views = cls.task['reasoning_state']['reading']['bundle']['views']

    def setUp(self):
        self.schema = original_span_output_schema(self.task, self.views)
        self.slots = requirement_slots(self.task)
        self.slot = next(row for row in self.slots if '510300.SH' in row['text'] and '管理费' in row['text'])
        entry = self.schema['properties']['requirements']['properties'][self.slot['key']]['anyOf'][0]
        self.properties = entry['properties']['items']['items']['anyOf'][0]['properties']
        ref = self.properties['source_ref']['enum'][0]
        self.choice = next(row for row in _original_span_choices(self.task, self.views) if row['source_ref'] == ref)

    def tearDown(self):
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), self.original)

    def wire(self, reference):
        period = next(row for row in self.choice['period_anchors'] if row['period'] == '2026-01-01/2026-06-30')
        item = dict(id='explicit_fee_fixture', kind='number', source_ref=self.choice['source_ref'],
                    object=self.properties['object']['enum'][0], nature='已实现事实',
                    temporal_reference=dict(period_ref=period['ref']), number_reference=reference,
                    review=dict(scope='current_original_descriptive_only'))
        wire = dict(version=12, binding_ref=self.schema['properties']['binding_ref']['enum'][0], selections=[],
            requirements={slot['key']:dict(status='not_found', reason='独立格式检查，不推断原件缺失') for slot in self.slots})
        wire['requirements'][self.slot['key']] = dict(status='found', items=[item])
        return wire

    def actual_references(self):
        token = next(row for row in self.choice['number_tokens'] if row['token'] == '0.15')
        unit = next(row for row in self.choice['unit_references'] if row['unit'] == '%')
        return token, unit

    def test_absent_compound_annual_unit_is_not_offered_as_literal_raw_unit(self):
        references = self.properties['number_reference']
        for branch in references.get('anyOf', [references]):
            field = branch['properties'].get('unit')
            if field:
                self.assertNotIn('%/年', field['enum'])

    def test_invalid_compound_unit_cannot_pass_schema_before_decoder_rejection(self):
        token, unit = self.actual_references()
        wire = self.wire(dict(number_ref=token['ref'], unit='%/年', unit_reference=dict(line=unit['line'])))
        self.assertFalse(_matches(wire, self.schema))
        with self.assertRaises(ContractError):
            decode_original_spans(wire, self.schema, (self.task, self.views))

    def test_actual_percent_reference_preserves_original_value_without_annual_conversion(self):
        token, unit = self.actual_references()
        wire = self.wire(dict(number_ref=token['ref'], unit_ref=unit['ref']))
        self.assertTrue(_matches(wire, self.schema))
        original = copy.deepcopy(wire)
        decoded = decode_original_spans(wire, self.schema, (self.task, self.views))['evidence_specs'][0]
        self.assertEqual((decoded['value'], decoded['unit'], decoded['period']),
                         (0.15, '%', '2026-01-01/2026-06-30'))
        self.assertEqual(wire, original)

    def test_unavailable_result_still_has_explicit_not_found_branch(self):
        wire = self.wire(self.actual_references()[0])
        wire['requirements'][self.slot['key']] = dict(status='not_found', reason='没有足够依据')
        self.assertTrue(_matches(wire, self.schema))

    def test_literal_compound_unit_is_retained_when_it_really_occurs(self):
        choice = dict(lines=[dict(text='原文单位：%/年')])
        self.assertEqual(_source_literal_units(choice, ('%', '%/年')), {'%/年'})

    def test_amount_unit_is_not_shortened_to_an_embedded_yuan(self):
        choice = dict(lines=[dict(text='金额单位：万元')])
        self.assertEqual(_source_literal_units(choice, ('元', '万元', '亿元')), {'万元'})

    def test_annual_rate_context_does_not_rewrite_the_literal_percent_unit(self):
        choice = dict(lines=[dict(text='管理费年费率0.15%，按日计提')])
        self.assertEqual(_source_literal_units(choice, ('%', '%/年', 'bp')), {'%'})


if __name__ == '__main__':
    unittest.main()
