"""An interval need must not offer a point date that adoption will reject."""
import copy
import gzip
import hashlib
import json
import unittest

from tests import ROOT
from researchhelper.output_schema import (original_span_output_schema, _original_span_choices,
    _matches, decode_original_spans)
from researchhelper.data_requirements import requirement_slots
from researchhelper.contracts import ContractError

SOURCE = ROOT / 'result/acceptance/one-sentence-research-_vvm3dyi/task.json'


class OriginalPeriodNeedCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE if SOURCE.is_file() else SOURCE.with_suffix('.json.gz')
        cls.original = hashlib.sha256(cls.source.read_bytes()).hexdigest()
        opener = gzip.open if cls.source.suffix == '.gz' else open
        with opener(cls.source, 'rt', encoding='utf-8') as stream:
            cls.task = json.load(stream)
        cls.views = cls.task['reasoning_state']['reading']['bundle']['views']

    def setUp(self):
        self.schema = original_span_output_schema(self.task, self.views)
        self.slots = requirement_slots(self.task)
        self.slot = next(slot for slot in self.slots if '电池产量' in slot['text'])
        self.choice = _original_span_choices(self.task, self.views)[0]

    def tearDown(self):
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.original)

    def properties(self, slot):
        branch = self.schema['properties']['requirements']['properties'][slot['key']]['anyOf'][0]
        return branch['properties']['items']['items']['anyOf'][0]['properties']

    def test_half_year_production_and_utilization_offer_only_real_intervals(self):
        for slot in self.slots:
            if slot['constraints']['period_kind'] != 'interval':
                continue
            variants = self.schema['properties']['requirements']['properties'][slot['key']]['anyOf'][0]
            for branch in variants['properties']['items']['items']['anyOf']:
                times = branch['properties']['temporal_reference']['anyOf']
                self.assertTrue(times)
                self.assertTrue(all(set(item['properties']) == {'period_ref'} for item in times))

    def test_unrestricted_capacity_keeps_point_and_interval_choices(self):
        capacity = next(slot for slot in self.slots if '电池产能的数值' in slot['text'])
        keys = {key for branch in self.properties(capacity)['temporal_reference']['anyOf'] for key in branch['properties']}
        self.assertEqual(keys, {'period_ref', 'observation_ref'})

    def wire(self):
        return dict(version=12, binding_ref=self.schema['properties']['binding_ref']['enum'][0], selections=[],
                    requirements={slot['key']:dict(status='not_found',reason='机械格式检查，不推断原件缺失')
                                  for slot in self.slots})

    def number(self):
        token = next(item for item in self.choice['number_tokens'] if item['token'] == '498')
        unit = next(item for item in self.choice['unit_references'] if item['unit'] == 'GWh')
        period = next(item for item in self.choice['period_anchors'] if item['period'] == '2026-01-01/2026-06-30')
        properties = self.properties(self.slot)
        return dict(id='production_period_fixture', kind='number', source_ref=self.choice['source_ref'],
                    object=properties['object']['enum'][0], nature='已实现事实',
                    number_reference=dict(number_ref=token['ref'], unit_ref=unit['ref']),
                    temporal_reference=dict(period_ref=period['ref']),
                    review=dict(scope='current_original_descriptive_only'))

    def test_explicit_interval_restores_same_raw_number_not_a_repaired_value(self):
        wire = self.wire()
        wire['requirements'][self.slot['key']] = dict(status='found',items=[self.number()])
        self.assertTrue(_matches(wire,self.schema))
        before = copy.deepcopy(wire)
        restored = decode_original_spans(wire,self.schema,(self.task,self.views))['evidence_specs'][0]
        self.assertEqual((restored['value'],restored['unit'],restored['period']),
                         (498,'GWh','2026-01-01/2026-06-30'))
        self.assertEqual(wire,before)

    def test_point_or_foreign_period_is_rejected_before_adoption(self):
        for temporal in (dict(observation_ref='any_point_reference'),dict(period_ref='foreign_period')):
            wire = self.wire()
            item = self.number();item['temporal_reference'] = temporal
            wire['requirements'][self.slot['key']] = dict(status='found',items=[item])
            self.assertFalse(_matches(wire,self.schema))
            with self.assertRaises(ContractError):
                decode_original_spans(wire,self.schema,(self.task,self.views))


if __name__ == '__main__':
    unittest.main()
