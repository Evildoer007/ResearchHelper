"""Original-only NAV supplements keep exact source references and gates."""
import copy
import json
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT, test_original_bound_choices as fixtures
from researchhelper import original_choices as choices
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, calculation_context_contract, fingerprint, validate_calculation_context
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import candidate_evidence
from researchhelper.model import ModelService, ModelSettings
from researchhelper.output_schema import (StructuredOutputError, original_span_output_schema,
    original_span_prompt, decode_original_spans)
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import extractable_source_views, evidence_contract, model_object, PHASE_FIELDS
from researchhelper.reports import compose


class OriginalOnlyChoiceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.OriginalBoundChoiceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.task = copy.deepcopy(self.fixture.task)
        self.task[choices.ORIGINAL_ONLY_POLICY] = 1
        self.views = [view for view in self.fixture.views if view['kind'] != 'api']
        self.found = choices.catalogue(self.task, self.views)
        self.schema = original_span_output_schema(self.task, self.views)
        nav = copy.deepcopy(self.fixture.wire['selections'][1])
        nav.update(source_ref='source_1',
            number_ref=next(ref for ref, value in self.found['numbers'].items()
                            if value['raw_token'] == '2.3272'),
            unit_ref=next(iter(self.found['units'])),
            context_refs={field: next(ref for ref, value in self.found['contexts'].items()
                if value['field'] == field and (field != 'valuation_date' or value['value'] == '2026-06-30'))
                for field in ('fund_id', 'share_class', 'currency', 'valuation_date')})
        self.wire = dict(version=5, binding_ref=self.found['binding_ref'], selections=[nav])

    def decode(self, wire=None, task=None):
        return decode_original_spans(wire or self.wire, self.schema, (task or self.task, self.views))

    def test_original_only_uses_same_bound_grammar_not_free_numeric_spans(self):
        self.assertEqual(self.schema['properties']['version']['enum'], [5])
        kinds = [variant['properties']['kind']['enum'][0]
                 for variant in self.schema['properties']['selections']['items']['anyOf']]
        self.assertIn('nav', kinds)
        self.assertNotIn('number', kinds)
        self.assertNotIn('api', kinds)

    def test_literal_original_is_decoded_and_financially_checked_not_adopted(self):
        before = fingerprint((self.task, self.views, self.wire))
        spec = self.decode()['evidence_specs'][0]
        source = next(item for item in self.task['source_records'] if item['id'] == spec['artifact_id'])
        fact = candidate_evidence(self.task, source, spec)
        self.assertEqual((fact['value'], fact['unit'], fact['period']), (2.3272, '元', '2026-06-30'))
        self.assertEqual(fact['calculation_context']['nav_kind'], 'official')
        self.assertEqual(len(self.task['evidence']), len(self.fixture.task['evidence']))
        self.assertFalse(self.task['calculations'])
        self.assertEqual(before, fingerprint((self.task, self.views, self.wire)))

    def test_prompt_exposes_one_number_address_and_preserves_exact_quotes(self):
        shown = original_span_prompt(dict(untrusted_sources=self.views,
            evidence_contract=evidence_contract(self.task, self.views)), self.task)
        numbers = shown['original_choices']['numbers']
        self.assertTrue(numbers)
        for item in numbers:
            self.assertTrue(item['ref'].startswith('number_'))
            self.assertNotIn('number_ref', item)
            self.assertIn(item['raw_token'], item['quote'])
        for before, after in zip(self.views, shown['untrusted_sources']):
            for original, presented in zip(before['parts'], after['parts']):
                self.assertEqual(original['text'], ''.join(line[1] for line in presented['indexed_lines']))

    def test_old_original_only_task_retains_legacy_grammar(self):
        task = dict(self.task); task.pop(choices.ORIGINAL_ONLY_POLICY)
        self.assertIsNone(choices.catalogue(task, self.views))
        self.assertEqual(original_span_output_schema(task, self.views)['properties']['version']['enum'], [4])
        for value in (True, False, 0, 2):
            task[choices.ORIGINAL_ONLY_POLICY] = value
            self.assertIsNone(choices.catalogue(task, self.views))

    def test_invalid_reference_has_safe_field_diagnostic_not_private_value(self):
        wire = copy.deepcopy(self.wire)
        wire['selections'][0]['number_ref'] = 'PRIVATE_REF_DO_NOT_ECHO'
        with self.assertRaises(StructuredOutputError) as caught:
            self.decode(wire)
        error = caught.exception
        self.assertEqual(error.category, 'invalid_original_choices')
        self.assertIn(dict(path='$.selections[*].number_ref', code='enum_mismatch'),
                      error.diagnostic['schema_issues'])
        self.assertFalse(error.diagnostic['submitted_values_retained'])
        self.assertFalse(error.diagnostic['automatic_repair'])
        self.assertNotIn('PRIVATE_REF_DO_NOT_ECHO', str(error) + json.dumps(error.diagnostic))

    def test_changed_binding_is_not_misreported_as_model_shape_error(self):
        with self.assertRaises(StructuredOutputError) as caught:
            self.decode(task=dict(self.task, input_hash='different_task_input'))
        self.assertEqual(caught.exception.category, 'original_choices_binding_changed')
        self.assertFalse(caught.exception.diagnostic)

    def test_wrong_document_date_cannot_pass_target_date_comparison(self):
        wire = copy.deepcopy(self.wire)
        wire['selections'][0]['context_refs']['valuation_date'] = next(
            ref for ref, value in self.found['contexts'].items()
            if value['field'] == 'valuation_date' and value['value'] != '2026-06-30')
        spec = self.decode(wire)['evidence_specs'][0]
        source = next(item for item in self.task['source_records'] if item['id'] == spec['artifact_id'])
        try:
            nav = candidate_evidence(self.task, source, spec)
        except ContractError:
            return  # The original evidence gate is stricter, never bypass it.
        price_spec = self.fixture.decode()['evidence_specs'][0]
        price = candidate_evidence(self.task,
            next(item for item in self.task['source_records'] if item['id'] == price_spec['artifact_id']), price_spec)
        with self.assertRaises(ContractError):
            validate_calculation_context(choices.METHOD, dict(price=price, nav=nav),
                dict(unit='元', currency='CNY', price_kind='close', nav_kind='official', valuation_date='2026-06-30'),
                context_contract=calculation_context_contract(self.task, choices.METHOD))

    def test_no_price_or_missing_unit_does_not_create_a_calculation(self):
        wire = copy.deepcopy(self.wire)
        wire['selections'][0]['unit_ref'] = 'unknown_unit'
        with self.assertRaises(StructuredOutputError):
            self.decode(wire)
        spec = self.decode()['evidence_specs'][0]
        self.assertNotIn('source_check', spec)
        self.assertFalse(self.task['calculations'])

    def test_new_task_flag_is_input_bound_and_frozen_not_added_to_cases(self):
        with tempfile.TemporaryDirectory(prefix='original-only-policy-') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            task = agent.create('解释ETF净值', questions=[dict(id='q1', kind='fact', question='解释ETF净值',
                answer_scope='general_definition', knowledge_needs=['rh_k_etf_premium_measures'])])
            self.assertEqual(task[choices.ORIGINAL_ONLY_POLICY], 1)
            legacy = dict(task); legacy.pop(choices.ORIGINAL_ONLY_POLICY)
            self.assertNotEqual(task['input_hash'], input_fingerprint(legacy))
            self.assertEqual(compose(agent.start(task['id']))[choices.ORIGINAL_ONLY_POLICY], 1)
            self.assertNotIn(choices.ORIGINAL_ONLY_POLICY, agent.create_case('mechanism'))

    def test_rejected_transport_retains_usage_and_safe_diagnostic(self):
        wire = copy.deepcopy(self.wire)
        wire['selections'][0]['number_ref'] = 'PRIVATE_REF_DO_NOT_ECHO'
        reply = dict(object='response', status='completed', error=None, incomplete_details=None,
            output=[dict(type='message', role='assistant', status='completed',
                content=[dict(type='output_text', text=json.dumps(wire))])], usage={})
        before = copy.deepcopy(self.task['ledger']['used'])
        self.task['ledger']['limits']['model_calls'] += 1
        with tempfile.TemporaryDirectory(prefix='original-only-transport-') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses',
                model='deepseek-flash', api_key='FAKE_ORIGINAL_ONLY_KEY'))
            service = ModelService(settings, offline=False)
            payload = dict(untrusted_sources=self.views, evidence_contract=evidence_contract(self.task, self.views))
            with patch.object(service, '_request_bytes', return_value=json.dumps(reply).encode()) as request:
                with self.assertRaises(ContractError):
                    model_object(service, self.task, 'extract', payload, PHASE_FIELDS['extract'])
            request.assert_called_once()
        self.assertEqual(self.task['ledger']['used']['model_calls'], before['model_calls'] + 1)
        event = self.task['ledger']['dispatches'][-1]
        self.assertEqual(event['structured_failure_category'], 'invalid_original_choices')
        self.assertNotIn('PRIVATE_REF_DO_NOT_ECHO', json.dumps(event))
        self.assertEqual(len(self.task['evidence']), len(self.fixture.task['evidence']))

    def test_retained_failure_prompt_keeps_schema_hash_but_removes_ambiguous_refs(self):
        path = ROOT / 'result/acceptance/one-sentence-research-c33y0hgo/task.json'
        before = path.read_bytes(); task = json.loads(before)
        views = extractable_source_views(task)
        schema = original_span_output_schema(task, views)
        first = next(row for row in task['ledger']['dispatches'] if row.get('research_phase') == 'extract')
        self.assertEqual(fingerprint(schema), first['output_schema_hash'])
        shown = original_span_prompt(dict(untrusted_sources=views,
            evidence_contract=evidence_contract(task, views)), task)
        self.assertTrue(shown['original_choices']['numbers'])
        self.assertTrue(all('number_ref' not in item for item in shown['original_choices']['numbers']))
        self.assertEqual(path.read_bytes(), before)
        # Matching the retained schema hash does not recover the model reply.
        self.assertFalse(task['evidence'])


if __name__ == '__main__':
    unittest.main()
