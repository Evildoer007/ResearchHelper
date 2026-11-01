"""Part addresses and literal numeric lines, never cross-paragraph repair."""
import copy
import json
import socket
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT, test_literal_quantity_references as fixtures
from researchhelper.contracts import ContractError, canonical
from researchhelper.output_schema import (StructuredOutputError, original_span_output_schema,
    original_span_prompt, decode_original_spans)
from researchhelper.reasoning import MAX_CONTEXT_CHARACTERS, model_object
from researchhelper.model import ModelService, ModelSettings


class OriginalPartReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.LiteralQuantityReferenceTests.setUpClass()
        cls.fixture = fixtures.LiteralQuantityReferenceTests()
        cls.original = cls.fixture.fixture
        cls.task = dict(copy.deepcopy(cls.fixture.task), literal_quantity_reference_version=2)
        cls.schema = original_span_output_schema(cls.task, cls.fixture.views)
        cls.prompt = original_span_prompt(cls.fixture.payload, cls.task)

    @classmethod
    def tearDownClass(cls):
        fixtures.LiteralQuantityReferenceTests.tearDownClass()

    def wire(self):
        wire = self.fixture.wire()
        wire['version'] = 7
        wire['binding_ref'] = self.prompt['span_binding_ref']
        wire['selections'][0].pop('text_span')
        return wire

    def setUp(self):
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline part selection'))
        guard.start()
        self.addCleanup(guard.stop)

    def decode(self, wire=None):
        return decode_original_spans(wire or self.wire(), self.schema, (self.task, self.fixture.views))

    def text_wire(self):
        wire = self.wire()
        item = wire['selections'][0]
        item.pop('number_reference')
        item['kind'] = 'text'
        line = self.original.wire()['selections'][0]['text_span']['start']
        item['text_reference'] = dict(part=self.fixture.choices[0]['lines'][line]['part'])
        return wire

    def test_literal_numeric_body_comes_from_its_actual_line_without_model_range(self):
        result = self.decode()['evidence_specs'][0]
        expected = self.fixture.decode()['evidence_specs'][0]
        self.assertEqual(result, expected)
        self.assertEqual(result['value'], 50.1)
        self.assertEqual(result['unit'], '%')
        self.assertNotIn('source_check', result)

    def test_part_reference_resolves_only_the_actual_same_source_paragraph(self):
        wire = self.text_wire()
        part = wire['selections'][0]['text_reference']['part']
        actual = next(part_record['text'] for part_record in self.fixture.views[0]['parts'] if part_record['locator'] == part)
        result = self.decode(wire)['evidence_specs'][0]
        self.assertEqual(result['record_locator']['part'], part)
        self.assertEqual(result['text'], actual.rstrip('\r\n'))
        self.assertEqual(result['unit'], '不适用')

    def test_invented_or_combined_paragraph_names_are_not_fuzzily_resolved(self):
        for name in ('unknown', '段落1-段落30'):
            wire = self.text_wire()
            wire['selections'][0]['text_reference']['part'] = name
            with self.subTest(name=name), self.assertRaises(StructuredOutputError):
                self.decode(wire)

    def test_exact_within_part_range_remains_available_but_cross_part_range_rejects(self):
        wire = self.text_wire()
        line = self.original.wire()['selections'][0]['text_span']['start']
        wire['selections'][0]['text_reference'] = dict(span=dict(start=line, end=line))
        self.assertEqual(self.decode(wire)['evidence_specs'][0]['text'], self.decode()['evidence_specs'][0]['text'])
        wire['selections'][0]['text_reference'] = dict(span=dict(start=line, end=line + 1))
        with self.assertRaises(ContractError):
            self.decode(wire)

    def test_manual_header_unit_and_body_range_still_use_the_full_original_rules(self):
        wire = self.wire()
        old = self.original.wire()['selections'][0]
        wire['selections'][0]['number_reference'] = {key: old[key] for key in ('number_ref', 'unit', 'unit_span', 'contexts', 'text_span')}
        self.assertEqual(self.decode(wire), self.decode())
        wire['selections'][0]['number_reference']['text_span']['end'] += 1
        with self.assertRaises(ContractError):
            self.decode(wire)

    def test_numeric_location_cannot_be_overridden_by_an_outer_range(self):
        wire = self.wire()
        wire['selections'][0]['text_span'] = dict(start=0, end=0)
        with self.assertRaises(StructuredOutputError):
            self.decode(wire)

    def test_old_v6_retains_explicit_body_and_v7_retains_complete_capacity(self):
        self.assertEqual(original_span_output_schema(self.fixture.task, self.fixture.views)['properties']['version']['enum'], [6])
        self.fixture.decode()
        self.assertEqual(self.schema['properties']['version']['enum'], [7])
        self.assertLessEqual(len(canonical(self.prompt)) + len(canonical(self.schema)), MAX_CONTEXT_CHARACTERS)

    def test_actual_transport_has_one_coherent_v7_prompt_and_no_extra_call(self):
        task = copy.deepcopy(self.task)
        before = task['ledger']['used']['model_calls']
        response = dict(object='response', status='completed', error=None, incomplete_details=None,
            output=[dict(type='message', role='assistant', status='completed',
                         content=[dict(type='output_text', text=json.dumps(self.wire()))])], usage={})
        with tempfile.TemporaryDirectory(prefix='part-reference-', dir=ROOT / 'result/acceptance') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash',
                               api_key='FAKE_PART_REFERENCE_KEY', extraction_mode='direct'))
            service = ModelService(settings, offline=False)
            with patch.object(service, '_request_bytes', return_value=json.dumps(response).encode()) as transport:
                result, receipt = model_object(service, task, 'extract', self.fixture.payload, {'evidence_specs'})
            body = json.loads(transport.call_args.args[1].data)
        self.assertEqual(result, self.decode())
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(task['ledger']['used']['model_calls'], before + 1)
        self.assertEqual(receipt['literal_quantity_reference_version'], 2)
        instructions = body['input'][0]['content']
        self.assertIn('version=7', instructions)
        self.assertNotIn('提交version=1', instructions)
        self.assertNotIn('number_occurrence', instructions)
