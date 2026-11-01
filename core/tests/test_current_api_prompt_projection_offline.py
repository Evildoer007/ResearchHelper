"""Actual extraction prompt projection, without changing evidence authority."""
import copy
import json
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, canonical, fingerprint
from researchhelper.reasoning import (
    PHASE_FIELDS, PHASE_RULES, evidence_contract, extractable_source_views,
    model_object, phase_output_example, validate_model_specs,
)
from tests import test_api_compact_context_offline as compact


class CapturingModel:
    def __init__(self, value=None):
        self.output = {'evidence_specs': []} if value is None else value
        self.calls = []

    def reason_phase(self, task, messages, phase):
        self.calls.append(copy.deepcopy(messages))
        return json.dumps(self.output)


class CurrentAPIPromptProjectionTests(unittest.TestCase):
    def setUp(self):
        fixture = compact.AcquiredCompactAPITests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture, self.task, self.record = fixture, fixture.task, fixture.record
        views = extractable_source_views(self.task)
        contract = evidence_contract(self.task, views)
        self.payload = {'evidence_contract': contract, 'untrusted_sources': views,
                        'calculation_input_targets': copy.deepcopy(
                            contract['calculation_input_requirements']['proposal_requirements'])}

    def call(self, payload=None, phase='extract', value=None):
        payload = self.payload if payload is None else payload
        model = CapturingModel(value or phase_output_example(phase))
        result, receipt = model_object(model, self.task, phase, payload, PHASE_FIELDS[phase])
        return json.loads(model.calls[0][1]['content']), model.calls[0][0]['content'], result, receipt

    def test_current_api_prompt_selects_explicit_compact_shape(self):
        body, system, _, receipt = self.call()
        contract = body['evidence_contract']
        self.assertNotIn('material_numeric_contract', contract)
        self.assertNotIn('document_coverage_contract', contract)
        shape = contract['api_candidate_contract']
        self.assertIn('api_value_reference', shape['required_spec_fields'])
        self.assertIn('identity', shape['required_spec_fields'])
        self.assertIn('review', shape['required_spec_fields'])
        self.assertNotIn('raw_value', shape['required_spec_fields'])
        self.assertNotIn('unit', shape['required_spec_fields'])
        self.assertIn(PHASE_RULES['extract'], system)
        self.assertIn('本批次仅包含当前取得的已登记API', system)
        self.assertEqual(receipt['extraction_prompt_version'], 1)

    def test_prompt_is_smaller_without_losing_any_original_or_binding(self):
        original = copy.deepcopy(self.payload)
        task = copy.deepcopy(self.task)
        body, _, _, receipt = self.call()
        self.assertLess(len(canonical(body)), len(canonical(original)) * .9)
        self.assertEqual(body['untrusted_sources'], original['untrusted_sources'])
        for name in ('binding', 'research_scope', 'question_requirements', 'question_binding_contract',
                     'question_coverage_contract', 'current_publication_template', 'current_availability_rule'):
            self.assertEqual(body['evidence_contract'][name], original['evidence_contract'][name])
        self.assertEqual(body['calculation_input_targets'], original['calculation_input_targets'])
        self.assertEqual(self.payload, original)
        self.assertEqual(self.task, task)
        self.assertEqual(receipt['context_hash'], fingerprint(original))
        self.assertEqual(receipt['prompt_context_hash'], fingerprint(body))

    def test_current_source_values_and_uncertainty_are_not_filled_or_removed(self):
        body, _, _, _ = self.call()
        prior = self.payload['evidence_contract']['sources'][0]
        projected = body['evidence_contract']['sources'][0]
        for name in ('artifact_id', 'snapshot_hash', 'value_reference', 'compact_context_reference',
                     'current_availability_reference', 'api_cells', 'publication_limitation'):
            self.assertEqual(projected[name], prior[name])
        for name in ('fixed_fields', 'row_fields', 'column_fields', 'unavailable_fields',
                     'unverified_context', 'context_is_approval'):
            self.assertEqual(projected['calculation_context_contract'][name],
                             prior['calculation_context_contract'][name])
        self.assertFalse(projected['calculation_context_contract']['context_is_approval'])

    def test_non_current_unregistered_missing_or_mixed_sources_keep_full_grammar(self):
        changes = (
            lambda p: p['evidence_contract']['sources'][0].pop('current_availability_reference'),
            lambda p: p['evidence_contract']['sources'][0].pop('value_reference'),
            lambda p: p['evidence_contract']['sources'][0].update(snapshot_hash='foreign'),
            lambda p: p['evidence_contract']['sources'][0]['value_reference'].update(snapshot_hash='foreign'),
            lambda p: p['evidence_contract']['sources'][0]['current_availability_reference'].update(kind='historical'),
            lambda p: p['evidence_contract']['sources'][0]['calculation_context_contract'].update(status='unavailable'),
            lambda p: p['evidence_contract']['api_candidate_contract'].pop('required_spec_fields'),
            lambda p: p['evidence_contract']['api_candidate_contract'].update(required_spec_fields=None),
            lambda p: p['evidence_contract']['api_candidate_contract'].update(required_spec_fields=[False]),
            lambda p: p['untrusted_sources'][0].update(kind='material'),
            lambda p: p['untrusted_sources'].append({'id': 'other', 'kind': 'material'}),
        )
        for change in changes:
            with self.subTest(change=change):
                payload = copy.deepcopy(self.payload)
                change(payload)
                body, _, _, receipt = self.call(payload)
                self.assertEqual(body, payload)
                self.assertNotIn('extraction_prompt_version', receipt)

    def test_other_stages_do_not_use_extraction_projection(self):
        for phase in ('plan', 'read', 'analyze', 'supplement'):
            with self.subTest(phase=phase):
                body, _, _, receipt = self.call(phase=phase)
                self.assertEqual(body, self.payload)
                self.assertNotIn('extraction_prompt_version', receipt)

    def test_valid_legacy_proposal_still_reaches_same_independent_checks(self):
        proposal = self.fixture.spec()
        _, _, result, _ = self.call(value={'evidence_specs': [proposal]})
        self.assertEqual(result['evidence_specs'], [proposal])
        validate_model_specs(self.task, result['evidence_specs'], schema_only=True)
        verified = self.fixture.fixture.approved(self.task, self.record, proposal)
        self.assertEqual(verified['source_check'], '通过')
        self.assertFalse(self.task['evidence'])

    def test_wrong_identity_context_and_approval_fields_are_not_repaired(self):
        for mutate in (lambda p: p.update(source_check='通过'),
                       lambda p: p['api_context_reference'].update(snapshot_hash='foreign'),
                       lambda p: p['calculation_context'].update(currency='USD')):
            proposal = copy.deepcopy(self.fixture.spec())
            mutate(proposal)
            with self.assertRaises(ContractError):
                _, _, result, _ = self.call(value={'evidence_specs': [proposal]})
                validate_model_specs(self.task, result['evidence_specs'], schema_only=True)
                self.fixture.fixture.approved(self.task, self.record, result['evidence_specs'][0])
            self.assertFalse(self.task['evidence'])

    def test_actual_engine_retains_checkpoint_and_ledger(self):
        agent = self.fixture.fixture.agent
        agent.model = CapturingModel()
        self.task['host_result'] = {}
        ledger = copy.deepcopy(self.task['ledger'])
        with patch.object(agent, '_reasoning_available', return_value=True), patch.object(agent, '_save_model_checkpoint'):
            agent._extract_model_sources(self.task)
            agent._extract_model_sources(self.task)
        self.assertEqual(len(agent.model.calls), 1)
        self.assertEqual(self.task['ledger'], ledger)
        self.assertFalse(self.task['evidence'])

    def test_full_original_over_context_limit_never_dispatches(self):
        payload = copy.deepcopy(self.payload)
        payload['untrusted_sources'][0]['raw_response'] = {'original': 'x' * 170000}
        model = CapturingModel()
        with self.assertRaises(ContractError):
            model_object(model, self.task, 'extract', payload, PHASE_FIELDS['extract'])
        self.assertFalse(model.calls)


if __name__ == '__main__':
    unittest.main()
