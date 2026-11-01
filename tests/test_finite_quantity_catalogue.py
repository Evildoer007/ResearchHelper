"""Source-local integer choices contain only real paired number/unit entries."""
import copy
import json
import socket
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT, test_multi_original_capacity as fixtures
from researchhelper.contracts import canonical
from researchhelper.model import ModelService, ModelSettings
from researchhelper.output_schema import (StructuredOutputError, original_span_output_schema,
    original_span_prompt, decode_original_spans, _original_span_choices, _quantity_index)
from researchhelper.reasoning import model_object, MAX_CONTEXT_CHARACTERS


class FiniteQuantityCatalogueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.MultiOriginalCapacityTests.setUpClass()
        cls.original = fixtures.MultiOriginalCapacityTests()
        cls.task = dict(copy.deepcopy(cls.original.task), literal_quantity_reference_version=4)
        cls.views, cls.payload = cls.original.views, cls.original.payload
        cls.schema = original_span_output_schema(cls.task, cls.views)
        cls.prompt = original_span_prompt(cls.payload, cls.task)
        cls.choices = _original_span_choices(cls.task, cls.views)
        cls.quantities = _quantity_index(cls.choices[0])

    @classmethod
    def tearDownClass(cls):
        fixtures.MultiOriginalCapacityTests.tearDownClass()

    def setUp(self):
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline quantity catalogue'))
        guard.start()
        self.addCleanup(guard.stop)

    def wire(self):
        wire = self.original.wire()
        item = wire['selections'][0]
        ref = item.pop('number_ref')
        for field in ('unit', 'unit_span', 'text_span', 'contexts'):
            item.pop(field)
        item['number_reference'] = dict(quantity_ref=next(index for index, value in self.quantities.items()
                                                        if value['number_ref'] == ref))
        wire.update(version=9, binding_ref=self.prompt['span_binding_ref'])
        return wire

    def decode(self, wire=None):
        return decode_original_spans(wire or self.wire(), self.schema, (self.task, self.views))

    def test_finite_integer_range_decodes_real_values_with_distinct_literal_units(self):
        first = self.decode()['evidence_specs'][0]
        self.assertEqual((first['raw_token'], first['value'], first['unit']), ('50.1', 50.1, '%'))
        self.assertNotIn('source_check', first)
        self.assertNotIn('applicability_check', first)
        self.assertEqual(list(self.quantities), list(range(1, len(self.quantities) + 1)))
        for index in (1, len(self.quantities) // 2, len(self.quantities)):
            wire = self.wire()
            wire['selections'][0]['number_reference']['quantity_ref'] = index
            actual = self.decode(wire)['evidence_specs'][0]
            expected = self.quantities[index]
            token = next(token for token in self.choices[0]['number_tokens'] if token['ref'] == expected['number_ref'])
            self.assertEqual((actual['raw_token'], actual['raw_unit']), (token['token'], expected['unit']))

    def test_raw_address_boolean_zero_and_out_of_range_are_not_quantity_choices(self):
        for ref in (0, len(self.quantities) + 1, 'n1_1', True):
            wire = self.wire()
            wire['selections'][0]['number_reference']['quantity_ref'] = ref
            with self.subTest(ref=ref), self.assertRaises(StructuredOutputError):
                self.decode(wire)

    def test_every_original_character_number_and_address_is_restorable(self):
        for choice, view in zip(self.choices, self.prompt['untrusted_sources']):
            parts = [dict(zip(view['part_columns'], row)) for row in view['parts']]
            refs, raw = _quantity_index(choice), iter(choice['number_tokens'])
            for original, part in zip(choice['view']['parts'], parts):
                self.assertEqual(original['text'], ''.join(row[1] for row in part['indexed_lines']))
                for line, _, rows in part['indexed_lines']:
                    for row in rows:
                        token = next(raw)
                        self.assertEqual((token['line'], token['token']), (line, row[1]))
                        if type(row[0]) is int:
                            self.assertEqual((refs[row[0]]['number_ref'], refs[row[0]]['unit']), (token['ref'], row[2]))
                        else:
                            self.assertEqual(row[0], token['ref'])
                            self.assertEqual(len(row), 2)
            self.assertEqual(list(raw), [])
            self.assertEqual(view['reference_types'], {'integer': 'quantity_ref', 'string': 'number_ref'})

    def test_manual_unit_path_is_preserved_and_three_originals_fit_the_same_limit(self):
        wire = self.wire()
        old = self.original.wire()['selections'][0]
        wire['selections'][0]['number_reference'] = dict(number_ref=old['number_ref'], unit=old['unit'],
            unit_reference=dict(line=old['unit_span']['start']))
        self.assertEqual(self.decode(wire), self.decode())
        self.assertEqual(self.schema['properties']['version']['enum'], [9])
        self.assertLessEqual(len(canonical(self.prompt)) + len(canonical(self.schema)), MAX_CONTEXT_CHARACTERS)

    def test_old_registered_wire_versions_are_not_implicitly_upgraded(self):
        for policy, expected in ((1, 6), (2, 7), (3, 8)):
            task = dict(self.task, literal_quantity_reference_version=policy)
            self.assertEqual(original_span_output_schema(task, self.views)['properties']['version']['enum'], [expected])

    def test_actual_transport_uses_integer_catalogue_and_one_mock_call(self):
        response = dict(object='response', status='completed', error=None, incomplete_details=None,
            output=[dict(type='message', role='assistant', status='completed',
                         content=[dict(type='output_text', text=json.dumps(self.wire()))])], usage={})
        task = copy.deepcopy(self.task)
        before = task['ledger']['used']['model_calls']
        with tempfile.TemporaryDirectory(prefix='finite-quantity-', dir=ROOT / 'result/acceptance') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash',
                               api_key='FAKE_FINITE_QUANTITY_KEY', extraction_mode='direct'))
            service = ModelService(settings, offline=False)
            with patch.object(service, '_request_bytes', return_value=json.dumps(response).encode()) as transport:
                result, receipt = model_object(service, task, 'extract', self.payload, {'evidence_specs'})
        self.assertEqual(result, self.decode())
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(task['ledger']['used']['model_calls'], before + 1)
        self.assertEqual(receipt['literal_quantity_reference_version'], 4)
