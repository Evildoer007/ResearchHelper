"""Read paired number/unit addresses, not model guesses or financial approval."""
import copy
import json
import socket
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT, test_multi_original_capacity as fixtures
from researchhelper.contracts import ContractError, canonical, fingerprint
from researchhelper.model import ModelSettings, ModelService
from researchhelper.output_schema import (StructuredOutputError, original_span_output_schema, original_span_prompt,
    decode_original_spans, _original_span_choices, _adjacent_quantity_units, _original_number_tokens)
from researchhelper.reasoning import model_object, MAX_CONTEXT_CHARACTERS


class LiteralQuantityReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.MultiOriginalCapacityTests.setUpClass()
        cls.fixture = fixtures.MultiOriginalCapacityTests()
        cls.task = dict(copy.deepcopy(cls.fixture.task), literal_quantity_reference_version=1)
        cls.views = cls.fixture.views
        cls.payload = cls.fixture.payload
        cls.schema = original_span_output_schema(cls.task, cls.views)
        cls.prompt = original_span_prompt(cls.payload, cls.task)
        cls.choices = _original_span_choices(cls.task, cls.views)

    @classmethod
    def tearDownClass(cls):
        fixtures.MultiOriginalCapacityTests.tearDownClass()

    def setUp(self):
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline literal-unit test'))
        guard.start()
        self.addCleanup(guard.stop)

    def wire(self):
        wire = self.fixture.wire()
        item = wire['selections'][0]
        ref = item.pop('number_ref')
        for field in ('unit', 'unit_span', 'contexts'):
            item.pop(field)
        item['number_reference'] = dict(quantity_ref=ref)
        wire.update(version=6, binding_ref=self.prompt['span_binding_ref'])
        return wire

    def decode(self, wire=None):
        return decode_original_spans(wire or self.wire(), self.schema, (self.task, self.views))

    def test_literal_number_and_its_own_unit_decode_without_a_model_unit_span(self):
        result = self.decode()['evidence_specs'][0]
        self.assertEqual((result['raw_token'], result['value'], result['unit']), ('50.1', 50.1, '%'))
        self.assertEqual(result['raw_unit'], '%')
        self.assertEqual(result['conversion_factor'], 1)
        self.assertNotIn('source_check', result)
        self.assertNotIn('applicability_check', result)

    def test_percentage_point_change_keeps_its_different_unit(self):
        choice = self.choices[0]
        units = _adjacent_quantity_units(choice)
        token = next(token for token in choice['number_tokens']
                     if token['ref'] in units and units[token['ref']]['unit'] == '个百分点')
        wire = self.wire()
        item = wire['selections'][0]
        item['number_reference'] = dict(quantity_ref=token['ref'])
        item['text_span'] = dict(start=token['line'], end=token['line'])
        result = self.decode(wire)['evidence_specs'][0]
        self.assertEqual(result['unit'], '个百分点')
        self.assertEqual(result['raw_token'], token['token'])

    def test_compound_per_period_units_are_not_shortened_to_percent(self):
        lines = [dict(index=0, text='费用0.5%/年，0.1%／月，0.2% / 年，年差1.2个百分点/年。本期50.1%，下降0.3个百分点。')]
        tokens = _original_number_tokens(lines, 1, compact=True)
        units = _adjacent_quantity_units(dict(lines=lines, number_tokens=tokens))
        actual = {token['token']: units[token['ref']]['unit'] for token in tokens if token['ref'] in units}
        self.assertEqual(actual, {'50.1': '%', '0.3': '个百分点'})

    def test_unknown_foreign_or_date_only_quantity_is_not_guessed(self):
        units = _adjacent_quantity_units(self.choices[0])
        date = next(token['ref'] for token in self.choices[0]['number_tokens'] if token['ref'] not in units)
        foreign = next(iter(_adjacent_quantity_units(self.choices[1])))
        for ref in ('invented', date, foreign):
            wire = self.wire()
            wire['selections'][0]['number_reference'] = dict(quantity_ref=ref)
            with self.subTest(ref=ref), self.assertRaises(StructuredOutputError):
                self.decode(wire)

    def test_model_cannot_override_bound_unit_or_borrow_unread_number_span(self):
        wire = self.wire()
        wire['selections'][0]['number_reference']['unit'] = '元'
        with self.assertRaises(StructuredOutputError):
            self.decode(wire)
        wire = self.wire()
        wire['selections'][0]['text_span'] = dict(start=0, end=0)
        with self.assertRaises(StructuredOutputError):
            self.decode(wire)

    def test_explicit_header_unit_branch_remains_available_and_equivalent(self):
        old = self.fixture.wire()['selections'][0]
        wire = self.wire()
        wire['selections'][0]['number_reference'] = {key: old[key] for key in ('number_ref', 'unit', 'unit_span', 'contexts')}
        self.assertEqual(self.decode(wire), self.decode())

    def test_all_original_characters_and_number_addresses_remain_in_the_view(self):
        for choice, view in zip(self.choices, self.prompt['untrusted_sources']):
            parts = [dict(zip(view['part_columns'], row)) for row in view['parts']]
            for original, shown in zip(choice['view']['parts'], parts):
                self.assertEqual(original['text'], ''.join(row[1] for row in shown['indexed_lines']))
            displayed = {row[0] for part in parts for line in part['indexed_lines'] for row in line[2]}
            self.assertEqual(displayed, {token['ref'] for token in choice['number_tokens']})
            registered = _adjacent_quantity_units(choice)
            self.assertEqual(view['number_token_columns'], ['number_ref', 'raw_token', 'adjacent_unit'])
            self.assertTrue(view['adjacent_unit_optional'])
            displayed_units = {row[0]: row[2] for part in parts for line in part['indexed_lines'] for row in line[2] if len(row) == 3}
            self.assertEqual(displayed_units, {ref: value['unit'] for ref, value in registered.items()})

    def test_three_full_originals_and_all_questions_fit_without_budget_change(self):
        self.assertEqual(self.schema['properties']['version']['enum'], [6])
        self.assertEqual(len(self.views), 3)
        size = len(canonical(self.prompt)) + len(canonical(self.schema))
        self.assertLessEqual(size, MAX_CONTEXT_CHARACTERS, size)
        self.assertEqual(self.task['ledger'], self.fixture.task['ledger'])

    def test_old_tasks_and_manually_located_bad_units_still_stop(self):
        self.assertEqual(original_span_output_schema(self.fixture.task, self.views)['properties']['version']['enum'], [4])
        wire = self.wire()
        old = self.fixture.wire()['selections'][0]
        wire['selections'][0]['number_reference'] = {key: old[key] for key in ('number_ref', 'unit', 'unit_span', 'contexts')}
        wire['selections'][0]['number_reference']['unit'] = 'UNREGISTERED_UNIT'
        with self.assertRaises(ContractError):
            self.decode(wire)

    def test_real_model_transport_and_decoder_keep_one_call_and_no_evidence_adoption(self):
        response = dict(object='response', status='completed', error=None, incomplete_details=None,
            output=[dict(type='message', role='assistant', status='completed',
                         content=[dict(type='output_text', text=json.dumps(self.wire()))])], usage={})
        task = copy.deepcopy(self.task)
        before = fingerprint(task['evidence'])
        count = task['ledger']['used']['model_calls']
        with tempfile.TemporaryDirectory(prefix='literal-quantity-', dir=ROOT / 'result/acceptance') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash',
                               api_key='FAKE_LITERAL_QUANTITY_KEY', extraction_mode='direct'))
            model = ModelService(settings, offline=False)
            with patch.object(model, '_request_bytes', return_value=json.dumps(response).encode()) as transport:
                result, receipt = model_object(model, task, 'extract', self.payload, {'evidence_specs'})
        self.assertEqual(result, self.decode())
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(task['ledger']['used']['model_calls'], count + 1)
        self.assertEqual(receipt['literal_quantity_reference_version'], 1)
        self.assertEqual(fingerprint(task['evidence']), before)
