"""Short quote choices preserve exact originals, not model-created evidence."""
import copy
import importlib
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.model import ModelService, ModelSettings
from researchhelper.output_schema import (financial_reference_output_schema, financial_reference_prompt,
    decode_financial_references, decode_financial_extraction_output,
    FINANCIAL_REFERENCE_PROTOCOL, FINANCIAL_REFERENCE_FUNCTION_NAME)
from researchhelper.reasoning import model_object, evidence_contract

fixtures = importlib.import_module((__package__ + '.' if __package__ else '')+'test_financial_context_choices')
ROOT = Path(__file__).resolve().parents[1]


class FinancialReferenceProtocolTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.FinancialContextChoicesTests()
        self.fixture.setUp(); self.addCleanup(self.fixture.tearDown)
        self.task, self.views = self.fixture.task, self.fixture.views
        from researchhelper.argument_support import VERSION
        self.task['argument_support_contract'] = VERSION
        self.payload = dict(untrusted_sources=self.views, evidence_contract=self.fixture.contract)
        self.schema = financial_reference_output_schema(self.task, self.views)
        self.presented = financial_reference_prompt(self.payload, self.task)
        self.wire = copy.deepcopy(self.fixture.wire)
        self.wire['version'] = 8
        self.wire['source_reference'] = self.presented['financial_reference_binding']
        numeric = self.wire['financial_cells'][0]
        self.wire['text_quotes'] = [dict(id='quote_probe', object=numeric['object'],
            nature=numeric['nature'], question_ids=numeric['question_ids'], review=numeric['review'],
            observation_anchor=numeric['observation_anchor'], period='2026年半年度', quote_reference='quote_1')]
        original = copy.deepcopy(self.fixture.wire)
        original['text_quotes'] = copy.deepcopy(self.wire['text_quotes'])
        original['text_quotes'][0]['quote_reference'] = self.fixture.schema['properties']['text_quotes'][
            'items']['properties']['quote_reference']['enum'][0]
        self.expected = decode_financial_extraction_output(original, self.fixture.schema, (self.task, self.views))
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No network'))
        guard.start(); self.addCleanup(guard.stop)

    def decode(self, wire=None):
        return decode_financial_references(self.wire if wire is None else wire, self.schema, (self.task, self.views))

    def service(self):
        temporary = tempfile.TemporaryDirectory(prefix='financial-ref-service-', dir=ROOT/'result/acceptance')
        self.addCleanup(temporary.cleanup)
        settings = ModelSettings(temporary.name)
        settings.save(dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash',
            api_key='FAKE_FINANCIAL_REF_KEY', extraction_mode='direct', reasoning_effort='low'))
        self.task['ledger']['used']['model_calls'] = 0
        return ModelService(settings, offline=False)

    def response(self):
        return dict(object='response', status='completed', error=None, incomplete_details=None,
            output=[dict(type='message', role='assistant', status='completed',
                content=[dict(type='output_text', text=json.dumps(self.wire))])],
            usage=dict(input_tokens=10, output_tokens=10))

    def test_short_quotes_decode_to_exact_old_original_and_financial_proposals(self):
        self.assertEqual(self.decode(), self.expected)

    def test_wire_needs_no_source_hash_or_long_quote_identifier(self):
        self.assertEqual(set(self.wire['source_reference']), {'source_ref','binding_ref'})
        offered = self.schema['properties']['text_quotes']['items']['properties']['quote_reference']['enum']
        self.assertTrue(all(ref.startswith('quote_') and len(ref)<12 for ref in offered))

    def test_unknown_quote_is_rejected_not_guessed(self):
        wire = copy.deepcopy(self.wire); wire['text_quotes'][0]['quote_reference'] = 'quote_999999'
        with self.assertRaises(ContractError): self.decode(wire)

    def test_previous_long_identifier_is_not_a_hidden_alternate_wire(self):
        wire = copy.deepcopy(self.wire)
        wire['text_quotes'][0]['quote_reference'] = self.fixture.schema['properties']['text_quotes'][
            'items']['properties']['quote_reference']['enum'][0]
        with self.assertRaises(ContractError): self.decode(wire)

    def test_wrong_binding_is_rejected_without_replacement(self):
        wire = copy.deepcopy(self.wire); wire['source_reference']['binding_ref'] = 'wrong'
        with self.assertRaises(ContractError): self.decode(wire)

    def test_different_task_or_version_invalidates_short_reference(self):
        for field, value in (('id','foreign'), ('version',99), ('batch',99), ('input_hash','changed')):
            task = copy.deepcopy(self.task); task[field] = value
            with self.assertRaises(ContractError):
                decode_financial_references(self.wire, self.schema, (task, self.views))

    def test_changed_read_view_invalidates_previous_alias_catalog(self):
        views = copy.deepcopy(self.views); views[0]['parts'][0]['text'] += 'changed'
        with self.assertRaises(ContractError):
            decode_financial_references(self.wire, self.schema, (self.task, views))

    def test_an_extra_raw_value_cannot_override_the_financial_cell(self):
        wire = copy.deepcopy(self.wire); wire['financial_cells'][0]['value'] = 1
        with self.assertRaises(ContractError): self.decode(wire)

    def test_model_cannot_replace_selected_quote_text(self):
        wire = copy.deepcopy(self.wire); wire['text_quotes'][0]['text'] = 'fabricated'
        with self.assertRaises(ContractError): self.decode(wire)

    def test_presentation_preserves_every_read_character_in_each_fragment(self):
        for before, after in zip(self.views[0]['parts'], self.presented['untrusted_sources'][0]['parts']):
            self.assertEqual(''.join(text for _,text in after['quote_choices']), before['text'])

    def test_decoding_changes_neither_wire_nor_evidence_approval(self):
        before = copy.deepcopy((self.wire, self.task, self.views))
        decoded = self.decode()
        self.assertEqual((self.wire, self.task, self.views), before)
        self.assertTrue(all('source_check' not in spec and 'applicability_check' not in spec
                            for spec in decoded['evidence_specs']))

    def test_nonfinancial_methods_do_not_silently_drop_obligations(self):
        task = copy.deepcopy(self.task); task['questions'][0]['method_needs'].append('fee_difference_v1')
        self.assertIsNone(financial_reference_output_schema(task, self.views))

    def test_latest_failed_actual_view_can_be_addressed_without_reopening_research(self):
        path = ROOT/'result/acceptance/one-sentence-research-rsxwvrir/task.json'
        before = path.read_bytes(); task = json.loads(before)
        views = task['reasoning_state']['reading']['bundle']['views']
        schema = financial_reference_output_schema(task, views)
        presented = financial_reference_prompt(dict(untrusted_sources=views,
            evidence_contract=evidence_contract(task, views)), task)
        self.assertEqual(schema['properties']['version']['enum'], [8])
        self.assertTrue(all(ref.startswith('quote_') for part in presented['untrusted_sources'][0]['parts']
                            for ref,_ in part['quote_choices']))
        self.assertEqual(path.read_bytes(), before)

    def test_official_transport_decodes_the_new_protocol_and_keeps_extraction_mode(self):
        service = self.service()
        with patch.object(service, '_request_bytes', return_value=json.dumps(self.response()).encode()) as request:
            value = service.reason_structured_financial_references(self.task, [], self.views)
        self.assertEqual(json.loads(value), self.expected)
        body = json.loads(request.call_args.args[1].data)
        self.assertEqual(body['text']['format']['name'], FINANCIAL_REFERENCE_FUNCTION_NAME)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 1)
        self.assertEqual(self.task['ledger']['dispatches'][-1]['extraction_mode'], 'direct')

    def test_normal_model_phase_presents_and_records_the_same_short_catalog(self):
        service = self.service()
        with patch.object(service, '_request_bytes', return_value=json.dumps(self.response()).encode()):
            value, _ = model_object(service, self.task, 'extract', self.payload, {'evidence_specs'})
        from researchhelper.financial_sources import expand_financial_value_references
        expected = {'evidence_specs':expand_financial_value_references(
            self.task, self.expected['evidence_specs'], self.views)}
        self.assertEqual(value, expected)
        record = self.task['ledger']['dispatches'][-1]
        self.assertEqual(record['output_protocol'], FINANCIAL_REFERENCE_PROTOCOL)
        self.assertEqual(record['output_schema_hash'], fingerprint(self.schema))


if __name__=='__main__': unittest.main()
