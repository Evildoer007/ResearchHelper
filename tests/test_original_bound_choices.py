"""Finite source addresses with unchanged source and financial validation."""
import copy
import json
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT, test_original_number_references as fixtures
from tests.legacy_replay_fixtures import ETF_CLOSE, ETF_NAV, ETF_VALUATION_DATE, listed_etf_task
from researchhelper import original_choices as choices
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, calculation_context_contract, validate_calculation_context
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import candidate_evidence
from researchhelper.model import ModelService, ModelSettings
from researchhelper.output_schema import original_span_output_schema, original_span_prompt, decode_original_spans
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import extractable_source_views, evidence_contract, model_object, PHASE_FIELDS
from researchhelper.reports import compose


class OriginalBoundChoiceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.OriginalNumberReferenceTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.task, self.views = self.fixture.task, self.fixture.views
        self.task.update(nav_original_choice_version=1, daily_nav_date_roles_version=1)
        self.found = choices.catalogue(self.task, self.views)
        self.schema = original_span_output_schema(self.task, self.views)
        self.wire = dict(version=5, binding_ref=self.found['binding_ref'], selections=[])
        price = copy.deepcopy(self.fixture.wire['selections'][0])
        price.pop('additional_contexts', None)
        price_shape = next(variant['properties'] for variant in self.schema['properties']['selections']['items']['anyOf']
            if variant['properties']['kind']['enum'] == ['api'])
        price['context_refs'] = {field:self.ref(field) for field in price_shape['context_refs']['properties']}
        nav = copy.deepcopy(self.fixture.wire['selections'][1])
        for field in ('number_ref', 'text_span', 'period', 'unit', 'unit_span', 'contexts', 'observation_anchor'):
            nav.pop(field, None)
        nav.update(kind='nav', number_ref=next(ident for ident, item in self.found['numbers'].items()
            if item['raw_token'] == ETF_NAV), unit_ref=next(iter(self.found['units'])),
            context_refs={field:self.ref(field) for field in ('fund_id', 'share_class', 'currency', 'valuation_date')})
        self.wire['selections'] = [price, nav]

    def ref(self, field, date='2026-06-30'):
        return next(ident for ident, item in self.found['contexts'].items()
            if item['field'] == field and (field != 'valuation_date' or item['value'] == date))

    def decode(self, wire=None, task=None, views=None):
        return decode_original_spans(wire or self.wire, self.schema, (task or self.task, views or self.views))

    def test_nav_grammar_has_bound_references_not_free_positions_or_values(self):
        self.assertEqual(self.schema['properties']['version']['enum'], [5])
        nav = next(variant['properties'] for variant in self.schema['properties']['selections']['items']['anyOf']
                   if variant['properties']['kind']['enum'] == ['nav'])
        self.assertTrue({'number_ref', 'unit_ref', 'context_refs'} <= set(nav))
        self.assertFalse({'value', 'text_span', 'unit_span', 'contexts', 'period'} & set(nav))
        self.assertNotIn('observation_date', nav['context_refs']['properties'])

    def test_literal_choices_still_pass_actual_source_and_comparison_gates(self):
        before = copy.deepcopy((self.task, self.views, self.wire))
        specs = self.decode()['evidence_specs']
        facts = [candidate_evidence(self.task, next(s for s in self.task['source_records'] if s['id'] == spec['artifact_id']), spec)
                 for spec in specs]
        self.assertEqual([item['value'] for item in facts], [ETF_CLOSE, float(ETF_NAV)])
        validate_calculation_context(choices.METHOD, dict(price=facts[0], nav=facts[1]),
            dict(unit='元', currency='CNY', price_kind='close', nav_kind='official', valuation_date=ETF_VALUATION_DATE),
            context_contract=calculation_context_contract(self.task, choices.METHOD))
        self.assertEqual((self.task, self.views, self.wire), before)
        self.assertFalse(self.task['calculations'])
        self.assertTrue(all('source_check' not in spec for spec in specs))

    def test_unknown_number_reference_and_added_position_are_rejected(self):
        for field, value in (('number_ref', 'foreign_number'), ('text_span', dict(start=0, end=1)), ('value', 123)):
            wire = copy.deepcopy(self.wire); wire['selections'][1][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError): self.decode(wire)

    def test_context_field_cannot_use_another_fields_reference(self):
        wire = copy.deepcopy(self.wire)
        wire['selections'][1]['context_refs']['fund_id'] = self.ref('currency')
        with self.assertRaises(ContractError): self.decode(wire)

    def test_different_date_reference_does_not_override_observation_anchor(self):
        different = next(ident for ident, item in self.found['contexts'].items()
                         if item['field'] == 'valuation_date' and item['value'] != '2026-06-30')
        wire = copy.deepcopy(self.wire); wire['selections'][1]['context_refs']['valuation_date'] = different
        with self.assertRaises(ContractError): self.decode(wire)

    def test_duplicate_api_additions_are_not_ignored(self):
        wire = copy.deepcopy(self.wire)
        wire['selections'][0]['context_refs'] = list(wire['selections'][0]['context_refs'].values()) * 2
        with self.assertRaises(ContractError): self.decode(wire)

    def test_changed_task_or_view_cannot_reuse_old_choices(self):
        task = dict(self.task, input_hash='changed')
        with self.assertRaises(ContractError): self.decode(task=task)
        views = copy.deepcopy(self.views)
        next(view for view in views if view['kind']=='filing')['parts'][0]['text'] += '\nchanged'
        with self.assertRaises(ContractError): self.decode(views=views)

    def test_prompt_preserves_all_sources_and_explains_choices_are_not_approval(self):
        payload = dict(untrusted_sources=self.views, evidence_contract=evidence_contract(self.task, self.views))
        prompt = original_span_prompt(payload, self.task)
        self.assertEqual(len(prompt['untrusted_sources']), len(self.views))
        self.assertFalse(prompt['choice_policy']['source_or_financial_approval'])
        self.assertTrue(prompt['original_choices']['numbers'])
        self.assertTrue(all(item['raw_token'] in item['quote'] for item in prompt['original_choices']['numbers']))
        for original, shown in zip(self.views, prompt['untrusted_sources']):
            for before, after in zip(original.get('parts', []), shown.get('parts', [])):
                self.assertEqual(before['text'], ''.join(line[1] for line in after['indexed_lines']))

    def test_report_without_trading_code_does_not_get_an_invented_share_class(self):
        task = listed_etf_task(has_trading_code=False)
        task.update(nav_original_choice_version=1, original_number_reference_version=1)
        before = copy.deepcopy(task)
        found = choices.catalogue(task, extractable_source_views(task))
        self.assertTrue(found['numbers'])
        self.assertFalse(any(item['field'] == 'share_class' for item in found['contexts'].values()))
        self.assertEqual(task, before)

    def test_legacy_and_unrelated_methods_keep_their_existing_grammar(self):
        legacy = dict(self.task); legacy.pop(choices.POLICY)
        self.assertEqual(original_span_output_schema(legacy, self.views)['properties']['version']['enum'], [4])
        for value in (False, True, 0, 2):
            task = dict(self.task); task[choices.POLICY] = value
            self.assertIsNone(choices.catalogue(task, self.views))
        task = copy.deepcopy(self.task); task['questions'][0]['method_needs'] = ['observed_price_return_v1']
        self.assertIsNone(choices.catalogue(task, self.views))

    def test_split_cumulative_nav_prefix_is_not_treated_as_official_nav(self):
        for prefix in ('累计\n', '参考 ', '预计\n', 'IOPV '):
            text = prefix + '单位净值 0.6151'
            self.assertTrue(choices.nonofficial_prefix(text, len(prefix)))
        self.assertFalse(choices.nonofficial_prefix('期末基金份额净值 0.6151', 0))

    def test_new_task_policy_is_input_bound_and_frozen(self):
        legacy = dict(self.task); legacy.pop(choices.POLICY)
        self.assertNotEqual(input_fingerprint(self.task), input_fingerprint(legacy))
        with tempfile.TemporaryDirectory(prefix='original-choice-policy-') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            task = agent.create('解释ETF净值', questions=[dict(id='q1', kind='fact', question='解释ETF净值',
                answer_scope='general_definition', knowledge_needs=['rh_k_etf_premium_measures'])])
            self.assertEqual(task[choices.POLICY], 1)
            self.assertEqual(compose(agent.start(task['id']))[choices.POLICY], 1)
            self.assertNotIn(choices.POLICY, agent.create_case('mechanism'))

    def transport(self, responses):
        self.task['ledger']['limits']['model_calls'] += 1
        body = json.dumps(self.wire)
        if responses:
            reply = dict(object='response', status='completed', error=None, incomplete_details=None,
                output=[dict(type='message', role='assistant', status='completed', content=[dict(type='output_text', text=body)])], usage={})
        else:
            reply = dict(choices=[dict(finish_reason='tool_calls', message=dict(role='assistant', content=None,
                tool_calls=[dict(id='synthetic_bound_choices', type='function', function=dict(
                    name='rh_select_original_spans_v1', arguments=body))]))], usage={})
        with tempfile.TemporaryDirectory(prefix='original-choice-transport-') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses' if responses else
                'https://api.deepseek.com/beta/chat/completions', model='deepseek-flash', api_key='FAKE_CHOICE_KEY'))
            service = ModelService(settings, offline=False)
            payload = dict(untrusted_sources=self.views, evidence_contract=evidence_contract(self.task, self.views))
            with patch.object(service, '_request_bytes', return_value=json.dumps(reply).encode()) as request:
                result, receipt = model_object(service, self.task, 'extract', payload, PHASE_FIELDS['extract'])
            request.assert_called_once()
            self.assertEqual(result['evidence_specs'][1]['raw_token'], ETF_NAV)
            self.assertEqual(receipt['output_protocol'], 'strict_original_spans_v1')

    def test_actual_responses_transport_decodes_bound_choices(self): self.transport(True)

    def test_actual_beta_transport_decodes_bound_choices(self): self.transport(False)


if __name__ == '__main__':
    unittest.main()
