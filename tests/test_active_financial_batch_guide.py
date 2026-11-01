"""Capture the actual failed study's next-version gateway without a model call."""
import copy
import hashlib
import json
from pathlib import Path
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from researchhelper.contracts import canonical
from researchhelper.reasoning import evidence_contract, model_object
from researchhelper.model import ModelService
from researchhelper.output_schema import original_batch_prompt, financial_amount_submission_rule
from researchhelper.model_presentation import restore

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'result/acceptance/one-sentence-research-u_16dpec/task.json'


class ActiveFinancialBatchGuideTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = SOURCE.read_bytes()
        cls.task = json.loads(cls.original)
        cls.views = cls.task['reasoning_state']['reading']['bundle']['views']
        cls.payload = dict(untrusted_sources=cls.views, evidence_contract=evidence_contract(cls.task, cls.views))
        before = canonical(cls.payload)
        captured = []
        active_batches = []
        def active_batch(payload, task):
            result = original_batch_prompt(payload, task)
            active_batches.append(hashlib.sha256(canonical(result).encode()).hexdigest())
            return result
        def capture(task, messages, views):
            captured.append(messages)
            raise RuntimeError('offline_gateway_capture')
        model = SimpleNamespace(supports_structured_analysis=lambda: True, supports_financial_references=lambda: True,
            input_budget=lambda: ModelService.documented_input_budget(dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash')),
            reason_structured_extraction=capture, reason_structured_financial_references=capture,
            reason_structured_original_batch=capture)
        with patch.object(socket.socket, 'connect', side_effect=AssertionError('No external I/O')), \
             patch('researchhelper.output_schema.original_batch_prompt', side_effect=active_batch):
            try:
                model_object(model, cls.task, 'extract', cls.payload, {'evidence_specs'})
            except RuntimeError as error:
                assert str(error) == 'offline_gateway_capture'
        assert len(captured) == 1 and canonical(cls.payload) == before
        cls.messages = captured[0]
        cls.system = cls.messages[0]['content']
        document = json.loads(cls.messages[-1]['content'])
        cls.body = restore(document) if document.get('format') == 'researchhelper.table_input' else document
        assert len(active_batches) == 1
        cls.active_batch_sha256 = active_batches[0]

    @classmethod
    def tearDownClass(cls):
        assert SOURCE.read_bytes() == cls.original

    def test_active_amount_instruction_names_the_actual_single_reference(self):
        self.assertIn('amount_reference', self.system)
        self.assertIn('financial_amount_choices', self.system)
        self.assertNotIn('amount_cells只选本来源financial_amount_tables', self.system)
        self.assertNotIn('column_reference和row_reference', self.system)

    def test_context_selector_has_one_address_namespace(self):
        self.assertIn('financial_context_reference', self.system)
        for source in self.body['evidence_contract']['sources']:
            self.assertFalse('financial_table_cells' in source)
            self.assertTrue(source['financial_context_cells'])
            self.assertTrue(all('candidate_id' in cell and 'financial_value_reference' not in cell
                for cell in source['financial_context_cells']))

    def test_all_four_actual_question_keys_and_needs_remain_available(self):
        self.assertEqual(set(self.body['evidence_contract']['question_ids']), {'q1', 'q2', 'q3', 'q4'})
        self.assertEqual(self.body['evidence_contract']['question_requirements'], self.payload['evidence_contract']['question_requirements'])
        for source in self.body['source_choices']:
            self.assertIn('[]表示关联该题但不覆盖任何登记需求', source['financial_need_link_rule'])

    def test_original_quotes_and_source_bindings_are_unchanged(self):
        self.assertEqual({source['artifact_id'] for source in self.body['evidence_contract']['sources']}, {view['id'] for view in self.views})
        for shown in self.body['untrusted_sources']:
            original = next(view for view in self.views if view['id'] == shown['id'])
            self.assertEqual(shown['snapshot_hash'], original['snapshot_hash'])
            for part in shown['parts']:
                text = next(value['text'] for value in original['parts'] if value['locator'] == part['locator'])
                self.assertTrue(all(quote in text for _, quote in part['quote_choices']))

    def test_final_gateway_encodes_the_active_batch_without_a_stale_route(self):
        self.assertEqual(hashlib.sha256(canonical(self.body).encode()).hexdigest(), self.active_batch_sha256)

    def test_legacy_task_keeps_its_original_catalogue_and_address_fields(self):
        old = copy.deepcopy(self.task)
        old.pop('financial_amount_reference_version')
        before = canonical(old)
        shown = original_batch_prompt(self.payload, old)
        for source in shown['evidence_contract']['sources']:
            self.assertTrue(all('financial_value_reference' in cell for cell in source['financial_context_cells']))
        for source in shown['source_choices']:
            self.assertIn('financial_amount_tables', source)
            self.assertNotIn('financial_amount_choices', source)
        self.assertEqual(canonical(old), before)

    def test_legacy_and_current_rules_use_the_corresponding_catalogue(self):
        old = dict(self.task)
        old.pop('financial_amount_reference_version')
        legacy = financial_amount_submission_rule(old)
        self.assertIn('table_reference', legacy)
        self.assertIn('row_reference', legacy)
        current = financial_amount_submission_rule(self.task)
        self.assertIn('amount_reference', current)
        self.assertNotIn('table_reference', current)


if __name__ == '__main__':
    unittest.main()
