"""Read-only regression for a real borderless report, without any provider."""
import json
import socket
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.contracts import ContractError, evidence_requirements
from researchhelper.output_schema import (
    decode_original_batch, financial_reference_output_schema,
    original_batch_output_schema, original_batch_prompt,
)
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import evidence_contract, validate_model_specs
from researchhelper.source_reader import binding, read, validate_read_specs


RETAINED = ROOT / 'result/acceptance/autonomous-data-phase-yrequfxl/task.json'


class BorderlessFinancialQuoteTests(unittest.TestCase):
    def setUp(self):
        self.original = RETAINED.read_bytes()
        self.task = json.loads(self.original)
        self.task['financial_quote_fallback_version'] = 1
        self.task['input_hash'] = input_fingerprint(self.task)
        previous = self.task['reasoning_state']['reading']['bundle']
        # Re-read the same retained bytes through the normal reader on the
        # projected memory task; do not transplant an old input-bound bundle.
        self.bundle = read(self.task, previous['selection'], binding(self.task),
                           previous['artifact_ids'], reader_version=previous['reader_version'])
        self.views = self.bundle['views']
        self.payload = dict(untrusted_sources=self.views,
                            evidence_contract=evidence_contract(self.task, self.views))
        self.schema = original_batch_output_schema(self.task, self.views)
        self.presented = original_batch_prompt(self.payload, self.task)
        self.addCleanup(lambda: self.assertEqual(RETAINED.read_bytes(), self.original))
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No network'))
        guard.start()
        self.addCleanup(guard.stop)

    def wire(self):
        choice = self.presented['source_choices'][0]
        view = self.presented['untrusted_sources'][0]
        source = self.payload['evidence_contract']['sources'][0]
        question = next(q for q in self.task['questions'] if not q.get('method_needs'))
        quote = next(ref for part in view['parts'] for ref, text in part['quote_choices']
                     if '风险' in text)
        anchor = next(a['index'] for a in view['observation_anchors'] if a['date'] == '2026-06-30')
        return dict(version=1, binding_ref=self.presented['batch_binding_ref'], sources=[dict(
            source_ref=choice['source_ref'], selection=dict(binding_ref=choice['binding']['binding_ref'],
                text_quotes=[dict(id='borderless_risk_quote', object=source['object_binding']['value'],
                    nature='已实现事实', question_ids=[question['id']], quote_reference=quote,
                    observation_anchor=anchor, review=dict(scope='current_original_descriptive_only',
                        need_links=[dict(question_id=question['id'], need_ids=[
                            n['id'] for n in evidence_requirements(question)])]))]))])

    def test_actual_report_has_no_typed_financial_selector(self):
        self.assertIsNone(financial_reference_output_schema(self.task, self.views))
        self.assertEqual([c['selection_kind'] for c in self.presented['source_choices']], ['qualitative'])

    def test_all_read_characters_survive_without_reconstructing_a_table(self):
        for before, after in zip(self.views[0]['parts'], self.presented['untrusted_sources'][0]['parts']):
            self.assertEqual(before['text'], ''.join(text for _, text in after['quote_choices']))
        self.assertNotIn('financial_table_cells', self.payload['evidence_contract']['sources'][0])

    def test_decoded_prose_is_literal_and_has_no_calculation_input(self):
        specs = decode_original_batch(self.wire(), self.schema, (self.task, self.views))['evidence_specs']
        validate_model_specs(self.task, specs)
        validate_read_specs(self.task, specs, self.bundle)
        for item in specs:
            self.assertEqual(item['unit'], '不适用')
            self.assertTrue(all(key not in item for key in ('value', 'raw_value', 'calculation_context')))
        self.assertTrue(any(q.get('method_needs') for q in self.task['questions']))
        self.assertFalse(self.task['evidence'])

    def test_old_tasks_do_not_change_protocol(self):
        self.task.pop('financial_quote_fallback_version')
        self.assertIsNone(original_batch_output_schema(self.task, self.views))

    def test_foreign_binding_is_rejected_not_repaired(self):
        wire = self.wire()
        wire['binding_ref'] = 'foreign'
        with self.assertRaises(ContractError):
            decode_original_batch(wire, self.schema, (self.task, self.views))

    def test_new_policy_participates_in_input_fingerprint(self):
        before = input_fingerprint(self.task)
        self.task.pop('financial_quote_fallback_version')
        self.assertNotEqual(input_fingerprint(self.task), before)
