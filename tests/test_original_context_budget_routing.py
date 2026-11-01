"""Read-only capacity regression on retained real HTML, not a study retry."""
import copy
import json
import socket
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.contracts import canonical, ContractError, fingerprint
from researchhelper.model import ModelSettings, ModelService
from researchhelper.output_schema import material_extraction_output_schema, original_span_output_schema, original_span_prompt
from researchhelper.planning import question_specs, input_fingerprint
from researchhelper.reasoning import (MAX_CONTEXT_CHARACTERS, PHASE_FIELDS,
    evidence_contract, extractable_source_views, model_object, material_prompt_projection)


RETAINED = ROOT / 'result/acceptance/one-sentence-research-j2jqn2i2/task.json'


class OriginalContextBudgetRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = RETAINED.read_bytes()
        cls.saved = json.loads(cls.original)

    @classmethod
    def tearDownClass(cls):
        if RETAINED.read_bytes() != cls.original:
            raise AssertionError('Closed macro study changed')

    def setUp(self):
        self.task = copy.deepcopy(self.saved)
        self.task['general_original_selection_version'] = 1
        self.task['statistical_period_version'] = 1
        self.task['input_hash'] = input_fingerprint(self.task)
        self.views = extractable_source_views(self.task)
        contract = evidence_contract(self.task, self.views)
        self.payload = dict(question=self.task['question'], intent=self.task['intent'],
            objects=self.task['objects'], cutoff=self.task['cutoff'], questions=question_specs(self.task),
            calculations=self.task['calculation_specs'], evidence_contract=contract,
            field_registry={}, untrusted_sources=self.views, source_reading=None,
            calculation_input_targets=copy.deepcopy(contract['calculation_input_requirements']['proposal_requirements']))
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No external calls'))
        guard.start()
        self.addCleanup(guard.stop)

    def test_compact_existing_protocol_fits_without_removing_read_characters(self):
        generic = material_extraction_output_schema(self.task, self.views)
        self.assertIsNotNone(generic)
        full, _ = material_prompt_projection(self.payload)
        self.assertGreater(len(canonical(full)) + len(canonical(generic)), MAX_CONTEXT_CHARACTERS)
        compact = original_span_output_schema(self.task, self.views)
        self.assertIsNotNone(compact)
        prompt = original_span_prompt(self.payload, self.task)
        size = len(canonical(prompt)) + len(canonical(compact))
        self.assertLessEqual(size, MAX_CONTEXT_CHARACTERS, f'Compact envelope: {size}')
        for source, projected in zip(self.views, prompt['untrusted_sources']):
            self.assertEqual(len(source['parts']), len(projected['parts']))
            parts = [dict(zip(projected['part_columns'], row)) for row in projected['parts']]
            for original, part in zip(source['parts'], parts):
                self.assertEqual(original['text'], ''.join(line[1] for line in part['indexed_lines']))
                for key in set(original) - {'text'}:
                    self.assertEqual(original[key], part[key])

    def test_model_entry_chooses_fitting_existing_container_before_rejecting(self):
        with tempfile.TemporaryDirectory(prefix='context-routing-', dir=ROOT / 'result/acceptance') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash', api_key='FAKE_OFFLINE_KEY'))
            model = ModelService(settings, offline=False)
            with patch.object(model, 'reason', return_value=json.dumps(dict(evidence_specs=[]))) as transport:
                result, receipt = model_object(model, self.task, 'extract', self.payload, PHASE_FIELDS['extract'])
            transport.assert_called_once()
            self.assertEqual(result, dict(evidence_specs=[]))
            self.assertIn('original', transport.call_args.kwargs['output_protocol'])

    def test_shared_part_addresses_reconstruct_every_original_scope(self):
        prompt = original_span_prompt(self.payload, self.task)
        original = evidence_contract(self.task, self.views)['sources']
        for view, source, before in zip(prompt['untrusted_sources'], prompt['evidence_contract']['sources'], original):
            index = source['passage_context_index']
            parts = [dict(zip(view['part_columns'], row)) for row in view['parts']]
            restored = [dict(part=part['locator'], physical_part=part['locator'], section_ref=section)
                        for part, section in zip(parts, index['section_refs'])]
            self.assertEqual(restored, before['passage_context_index']['parts'])
            self.assertEqual(index['sections'], before['passage_context_index']['sections'])

    def test_oversized_compact_input_still_stops_without_a_request(self):
        self.payload['necessary_context'] = 'x' * MAX_CONTEXT_CHARACTERS
        with tempfile.TemporaryDirectory(prefix='context-guard-', dir=ROOT / 'result/acceptance') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash', api_key='FAKE_OFFLINE_KEY'))
            model = ModelService(settings, offline=False)
            with patch.object(model, 'reason') as transport, self.assertRaises(ContractError):
                model_object(model, self.task, 'extract', self.payload, PHASE_FIELDS['extract'])
            transport.assert_not_called()

    def test_old_task_keeps_its_existing_protocol_and_all_inputs_are_unchanged(self):
        old = copy.deepcopy(self.saved)
        self.assertIsNone(original_span_output_schema(old, self.views))
        before = fingerprint((self.task, self.payload))
        original_span_prompt(self.payload, self.task)
        self.assertEqual(before, fingerprint((self.task, self.payload)))

    def test_additional_necessary_questions_do_not_reintroduce_address_explosion(self):
        task = copy.deepcopy(self.task)
        for index in range(3):
            question = copy.deepcopy(task['questions'][0])
            question['id'] = 'capacity_' + str(index)
            task['questions'].append(question)
        task['input_hash'] = input_fingerprint(task)
        payload = copy.deepcopy(self.payload)
        payload.update(questions=question_specs(task), evidence_contract=evidence_contract(task, self.views))
        prompt = original_span_prompt(payload, task)
        schema = original_span_output_schema(task, self.views)
        self.assertLessEqual(len(canonical(prompt)) + len(canonical(schema)), MAX_CONTEXT_CHARACTERS)
        self.assertEqual(len(prompt['questions']), len(task['questions']))
