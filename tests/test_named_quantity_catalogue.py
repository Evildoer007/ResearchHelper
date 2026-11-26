"""Named finite references separate identifier selection from financial values."""
import copy
import json
import socket
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT, test_finite_quantity_catalogue as fixtures
from researchhelper.contracts import canonical, fingerprint
from researchhelper.model import ModelService, ModelSettings
from researchhelper.output_schema import (StructuredOutputError, original_span_output_schema,
    original_span_prompt, decode_original_spans, _original_span_choices, _named_quantity_index,
    provider_output_schema, provider_request_schema, ORIGINAL_SPAN_PROTOCOL)
from tests.test_shared_schema_transport import expanded
from researchhelper.reasoning import model_object, MAX_CONTEXT_CHARACTERS


class NamedQuantityCatalogueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.FiniteQuantityCatalogueTests.setUpClass()
        cls.base = fixtures.FiniteQuantityCatalogueTests()
        cls.task = dict(copy.deepcopy(cls.base.task), literal_quantity_reference_version=5)
        cls.views, cls.payload = cls.base.views, cls.base.payload
        cls.schema = original_span_output_schema(cls.task, cls.views)
        cls.prompt = original_span_prompt(cls.payload, cls.task)
        cls.choices = _original_span_choices(cls.task, cls.views)

    @classmethod
    def tearDownClass(cls):
        fixtures.FiniteQuantityCatalogueTests.tearDownClass()

    def setUp(self):
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline named catalogue'))
        guard.start()
        self.addCleanup(guard.stop)

    def wire(self):
        wire = self.base.wire()
        wire.update(version=10, binding_ref=self.prompt['span_binding_ref'])
        reference = wire['selections'][0]['number_reference']
        reference['quantity_ref'] = 'v' + str(reference['quantity_ref'])
        return wire

    def decode(self, wire=None):
        return decode_original_spans(wire or self.wire(), self.schema, (self.task, self.views))

    def test_named_reference_restores_identical_numeric_evidence(self):
        actual = self.decode()['evidence_specs'][0]
        expected = self.base.decode()['evidence_specs'][0]
        self.assertEqual(actual, expected)
        self.assertEqual((actual['raw_token'], actual['unit']), ('50.1', '%'))

    def test_raw_values_integer_line_indices_and_unknown_names_remain_rejected(self):
        for value in (50.1, 1, True, '50.1', 'n1_1', 'v0', 'v99999'):
            wire = self.wire()
            wire['selections'][0]['number_reference']['quantity_ref'] = value
            with self.subTest(value=value), self.assertRaises(StructuredOutputError):
                self.decode(wire)

    def test_prompt_restores_every_character_and_catalogue_is_schema_exact(self):
        for choice, view in zip(self.choices, self.prompt['untrusted_sources']):
            catalogue = _named_quantity_index(choice)
            parts = [dict(zip(view['part_columns'], row)) for row in view['parts']]
            for original, part in zip(choice['view']['parts'], parts):
                self.assertEqual(original['text'], ''.join(row[1] for row in part['indexed_lines']))
                for line, _, tokens in part['indexed_lines']:
                    for token in tokens:
                        if token[0].startswith('v'):
                            self.assertEqual((catalogue[token[0]]['line'], catalogue[token[0]]['unit']),
                                             (line, token[2]))
            variant = next(item for item in self.schema['properties']['selections']['items']['anyOf']
                if item['properties']['source_ref']['enum'] == [choice['source_ref']]
                and item['properties']['kind']['enum'] == ['number'])
            named = variant['properties']['number_reference']['anyOf'][0]
            self.assertEqual(named['properties']['quantity_ref'], {'type': 'string', 'enum': list(catalogue)})
            self.assertEqual(view['reference_types'], {'v_prefix': 'quantity_ref', 'n_prefix': 'number_ref'})

    def test_manual_header_branch_still_restores_same_evidence(self):
        wire = self.wire()
        previous = self.base.original.wire()['selections'][0]
        wire['selections'][0]['number_reference'] = dict(number_ref=previous['number_ref'],
            unit=previous['unit'], unit_reference=dict(line=previous['unit_span']['start']))
        self.assertEqual(self.decode(wire), self.decode())

    def test_complete_three_originals_fit_unchanged_context_limit(self):
        wire_schema = provider_request_schema(self.task, self.schema, ORIGINAL_SPAN_PROTOCOL)
        self.assertGreater(len(canonical(self.prompt)) + len(canonical(self.schema)), MAX_CONTEXT_CHARACTERS)
        size = len(canonical(self.prompt)) + len(canonical(wire_schema))
        self.assertLessEqual(size, MAX_CONTEXT_CHARACTERS, size)
        self.assertEqual(expanded(wire_schema), provider_output_schema(self.schema))
        self.assertEqual(self.schema['properties']['version']['enum'], [10])

    def test_actual_model_transport_uses_named_enum_with_one_mock_call(self):
        response = dict(object='response', status='completed', error=None, incomplete_details=None,
            output=[dict(type='message', role='assistant', status='completed',
                         content=[dict(type='output_text', text=json.dumps(self.wire()))])], usage={})
        task = copy.deepcopy(self.task)
        before = task['ledger']['used']['model_calls']
        with tempfile.TemporaryDirectory(prefix='named-quantity-', dir=ROOT / 'result/acceptance') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash',
                               api_key='FAKE_NAMED_QUANTITY_KEY', extraction_mode='direct'))
            with patch.object(ModelService, '_request_bytes', return_value=json.dumps(response).encode()) as transport:
                result, receipt = model_object(ModelService(settings, offline=False), task, 'extract',
                                               self.payload, {'evidence_specs'})
        self.assertEqual(result, self.decode())
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(task['ledger']['used']['model_calls'], before + 1)
        self.assertEqual(receipt['literal_quantity_reference_version'], 5)
        request = json.loads(transport.call_args.args[1].data)
        wire_schema = request['text']['format']['schema']
        self.assertIn('$defs', wire_schema)
        self.assertEqual(expanded(wire_schema), provider_output_schema(self.schema))
        event = task['ledger']['dispatches'][-1]
        self.assertEqual(event['output_schema_hash'], fingerprint(self.schema))
        self.assertEqual(event['provider_output_schema_hash'], fingerprint(wire_schema))
        self.assertTrue(event['schema_definitions_shared'])
