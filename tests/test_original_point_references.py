"""Model point references cannot express cross-paragraph numeric ranges."""
import copy
import json
import socket
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT, test_original_part_references as fixtures
from researchhelper.contracts import ContractError, canonical
from researchhelper.output_schema import (StructuredOutputError, original_span_output_schema,
    original_span_prompt, decode_original_spans)
from researchhelper.reasoning import MAX_CONTEXT_CHARACTERS, model_object
from researchhelper.model import ModelService, ModelSettings


class OriginalPointReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.OriginalPartReferenceTests.setUpClass()
        cls.fixture = fixtures.OriginalPartReferenceTests()
        cls.original = cls.fixture.original
        cls.literal = cls.fixture.fixture
        cls.task = dict(copy.deepcopy(cls.fixture.task), literal_quantity_reference_version=3)
        cls.views = cls.literal.views
        cls.schema = original_span_output_schema(cls.task, cls.views)
        cls.prompt = original_span_prompt(cls.literal.payload, cls.task)
        cls.choice = cls.literal.choices[0]

    @classmethod
    def tearDownClass(cls):
        fixtures.OriginalPartReferenceTests.tearDownClass()

    def setUp(self):
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline point selection'))
        guard.start()
        self.addCleanup(guard.stop)

    def wire(self):
        wire = self.fixture.wire()
        wire.update(version=8, binding_ref=self.prompt['span_binding_ref'])
        return wire

    def decode(self, wire=None):
        return decode_original_spans(wire or self.wire(), self.schema, (self.task, self.views))

    def test_literal_reference_has_same_raw_number_unit_and_exact_body(self):
        self.assertEqual(self.decode(), self.fixture.decode())

    def test_header_unit_selects_one_actual_part_or_line_without_body_range(self):
        old = self.original.wire()['selections'][0]
        line = old['unit_span']['start']
        for reference in (dict(line=line), dict(part=self.choice['lines'][line]['part'])):
            wire = self.wire()
            wire['selections'][0]['number_reference'] = dict(number_ref=old['number_ref'], unit=old['unit'], unit_reference=reference)
            self.assertEqual(self.decode(wire), self.decode())

    def test_unknown_number_or_unit_location_is_not_guessed(self):
        old = self.original.wire()['selections'][0]
        for ref in (dict(part='not-a-part'), dict(line=len(self.choice['lines']))):
            wire = self.wire()
            wire['selections'][0]['number_reference'] = dict(number_ref=old['number_ref'], unit='%', unit_reference=ref)
            with self.assertRaises(ContractError):
                self.decode(wire)

    def test_text_can_choose_one_line_or_whole_original_part(self):
        text = self.fixture.text_wire()
        text.update(version=8, binding_ref=self.prompt['span_binding_ref'])
        whole = self.decode(text)['evidence_specs'][0]
        line = self.original.wire()['selections'][0]['text_span']['start']
        text['selections'][0]['text_reference'] = dict(line=line)
        self.assertEqual(self.decode(text)['evidence_specs'][0]['text'], whole['text'])

    def test_cross_part_ranges_are_not_offered_in_either_model_reference(self):
        wire = self.wire()
        old = self.original.wire()['selections'][0]
        wire['selections'][0]['number_reference'] = dict(number_ref=old['number_ref'], unit='%',
            unit_reference=dict(span=dict(start=0, end=100)))
        with self.assertRaises(StructuredOutputError):
            self.decode(wire)
        text = self.fixture.text_wire()
        text.update(version=8, binding_ref=self.prompt['span_binding_ref'])
        text['selections'][0]['text_reference'] = dict(span=dict(start=0, end=100))
        with self.assertRaises(StructuredOutputError):
            self.decode(text)

    def test_wrong_manual_unit_still_rejects_even_with_real_original_location(self):
        old = self.original.wire()['selections'][0]
        wire = self.wire()
        wire['selections'][0]['number_reference'] = dict(number_ref=old['number_ref'], unit='UNREGISTERED_UNIT',
            unit_reference=dict(line=old['unit_span']['start']))
        with self.assertRaises(ContractError):
            self.decode(wire)

    def test_all_prior_body_text_and_three_original_capacity_are_retained(self):
        self.assertEqual(self.schema['properties']['version']['enum'], [8])
        self.assertEqual(self.prompt['untrusted_sources'], self.fixture.prompt['untrusted_sources'])
        self.assertLessEqual(len(canonical(self.prompt)) + len(canonical(self.schema)), MAX_CONTEXT_CHARACTERS)
        self.assertEqual(original_span_output_schema(self.fixture.task, self.views)['properties']['version']['enum'], [7])

    def test_actual_model_transport_uses_the_point_contract_once(self):
        response = dict(object='response', status='completed', error=None, incomplete_details=None,
            output=[dict(type='message', role='assistant', status='completed',
                         content=[dict(type='output_text', text=json.dumps(self.wire()))])], usage={})
        task = copy.deepcopy(self.task)
        before = task['ledger']['used']['model_calls']
        with tempfile.TemporaryDirectory(prefix='point-reference-', dir=ROOT / 'result/acceptance') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash',
                               api_key='FAKE_POINT_REFERENCE_KEY', extraction_mode='direct'))
            service = ModelService(settings, offline=False)
            with patch.object(service, '_request_bytes', return_value=json.dumps(response).encode()) as transport:
                result, receipt = model_object(service, task, 'extract', self.literal.payload, {'evidence_specs'})
            body = json.loads(transport.call_args.args[1].data)
        self.assertEqual(result, self.decode())
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(task['ledger']['used']['model_calls'], before + 1)
        self.assertEqual(receipt['literal_quantity_reference_version'], 3)
        self.assertIn('version=8', body['input'][0]['content'])
