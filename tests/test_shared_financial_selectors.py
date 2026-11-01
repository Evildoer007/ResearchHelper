"""Source-local entity/standard selectors keep the full original binding."""
import copy
import importlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError
from researchhelper.financial_sources import financial_shared_context_choices
from researchhelper.output_schema import (
    decode_financial_references, financial_reference_prompt,
    financial_reference_output_schema,
)
from researchhelper.reasoning import evidence_contract

fixtures = importlib.import_module((__package__+'.' if __package__ else '')+
                                   'test_financial_reference_protocol')
ROOT = Path(__file__).resolve().parents[1]


class SharedFinancialSelectorTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.FinancialReferenceProtocolTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown if hasattr(self.fixture, 'tearDown') else lambda: None)
        self.task, self.views = self.fixture.task, self.fixture.views
        self.schema, self.wire = self.fixture.schema, self.fixture.wire
        self.contract = evidence_contract(self.task, self.views)
        self.source = self.contract['sources'][0]

    def test_short_typed_addresses_keep_full_bindings_and_literal_bases(self):
        parts = {part['locator']: part['text'] for part in self.views[0]['parts']}
        for field, prefix in (('entity_id', 'entity'), ('accounting_standard', 'standard')):
            choices = self.source['financial_shared_context_choices'][field]
            self.assertEqual([choice['candidate_id'] for choice in choices],
                             [prefix+'_'+str(i+1) for i in range(len(choices))])
            self.assertEqual(len({choice['candidate_binding'] for choice in choices}), len(choices))
            for choice in choices:
                self.assertEqual(len(choice['candidate_binding']), 64)
                self.assertEqual(choice['reference_version'], 2)
                self.assertFalse(choice['candidate_is_approval'])
                self.assertIn(choice['basis']['quote'], parts[choice['basis']['part']])

    def test_normal_decoder_produces_the_same_unapproved_financial_proposals(self):
        self.assertEqual(decode_financial_references(self.wire, self.schema, (self.task, self.views)),
                         self.fixture.expected)
        self.assertFalse(self.task['evidence'])
        self.assertFalse(self.task['calculations'])

    def test_semantic_value_is_not_a_hidden_alternative_selector(self):
        for field in ('entity_id', 'accounting_standard'):
            wire = copy.deepcopy(self.wire)
            wire['financial_cells'][0]['shared_context_references'][field] = (
                self.source['financial_shared_context_choices'][field][0]['value'])
            with self.subTest(field=field), self.assertRaises(ContractError):
                decode_financial_references(wire, self.schema, (self.task, self.views))

    def test_hash_is_not_accepted_as_an_address(self):
        wire = copy.deepcopy(self.wire)
        wire['financial_cells'][0]['shared_context_references']['accounting_standard'] = (
            self.source['financial_shared_context_choices']['accounting_standard'][0]['candidate_binding'])
        with self.assertRaises(ContractError):
            decode_financial_references(wire, self.schema, (self.task, self.views))

    def test_wrong_field_namespace_and_unknown_index_are_rejected(self):
        for replacement in ('entity_1', 'standard_9999', 'rh_finbasis_foreign'):
            wire = copy.deepcopy(self.wire)
            wire['financial_cells'][0]['shared_context_references']['accounting_standard'] = replacement
            with self.subTest(replacement=replacement), self.assertRaises(ContractError):
                decode_financial_references(wire, self.schema, (self.task, self.views))

    def test_local_address_cannot_reuse_an_old_task_version_or_binding(self):
        for field, value in (('id', 'foreign'), ('version', self.task['version']+1),
                             ('input_hash', 'changed'), ('batch', self.task['batch']+1)):
            task = copy.deepcopy(self.task)
            task[field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                decode_financial_references(self.wire, self.schema, (task, self.views))

    def test_source_reference_cannot_be_replaced_even_if_local_ids_match(self):
        wire = copy.deepcopy(self.wire)
        wire['source_reference']['binding_ref'] = 'foreign'
        with self.assertRaises(ContractError):
            decode_financial_references(wire, self.schema, (self.task, self.views))

    def test_prompt_retains_meaning_and_basis_but_not_transcription_hash(self):
        payload = dict(untrusted_sources=self.views, evidence_contract=self.contract)
        before = copy.deepcopy(payload)
        projected = financial_reference_prompt(payload, self.task)
        presented = projected['evidence_contract']['sources'][0]['financial_shared_context_choices']
        for field, choices in self.source['financial_shared_context_choices'].items():
            self.assertEqual(presented[field], [
                {key: value for key, value in choice.items() if key!='candidate_binding'}
                for choice in choices])
        self.assertEqual(payload, before)

    def test_actual_failed_view_has_identical_prompt_and_schema_addresses(self):
        path = ROOT/'result/acceptance/one-sentence-research-2bsp956w/task.json'
        original = path.read_bytes()
        task = json.loads(original)
        views = task['reasoning_state']['reading']['bundle']['views']
        schema = financial_reference_output_schema(task, views)
        payload = financial_reference_prompt(dict(untrusted_sources=views,
            evidence_contract=evidence_contract(task, views)), task)
        choices = payload['evidence_contract']['sources'][0]['financial_shared_context_choices']
        shared = schema['properties']['financial_cells']['items']['properties']['shared_context_references']
        for field in ('entity_id', 'accounting_standard'):
            self.assertEqual(shared['properties'][field]['enum'],
                             [choice['candidate_id'] for choice in choices[field]])
        self.assertEqual(shared['properties']['accounting_standard']['enum'],
                         ['standard_1', 'standard_2', 'standard_3', 'standard_4'])
        self.assertEqual(path.read_bytes(), original)

    def test_repeated_derivation_is_stable_without_mutating_originals(self):
        record = self.fixture.fixture.record
        before = copy.deepcopy((self.task, record, self.views))
        first = financial_shared_context_choices(self.task, record, self.views[0])
        second = financial_shared_context_choices(self.task, record, self.views[0])
        self.assertEqual(first, second)
        self.assertEqual((self.task, record, self.views), before)

    def test_same_local_ids_with_changed_meanings_cannot_reuse_source_binding(self):
        choices = copy.deepcopy(self.source['financial_shared_context_choices'])
        choices['accounting_standard'][0]['candidate_binding'] = 'changed-definition'
        with patch('researchhelper.reasoning.financial_shared_context_choices', return_value=choices):
            changed = financial_reference_output_schema(self.task, self.views)
            self.assertNotEqual(changed, self.schema)
            with self.assertRaises(ContractError):
                decode_financial_references(self.wire, self.schema, (self.task, self.views))
