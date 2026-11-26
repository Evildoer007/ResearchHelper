"""Missing-only context choices, without reopening or repairing live studies."""
import copy
import json
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT, test_original_context_selection as fixtures
from researchhelper import output_schema as schema_module
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.model import ModelService, ModelSettings
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import model_object, PHASE_FIELDS
from researchhelper.reports import compose


POLICY = 'missing_api_context_selection_version'


def fields(variant):
    return {field for option in variant['properties']['additional_contexts']['items'].get('anyOf', [])
            for field in option['properties']['field']['enum']}


class MissingAPIContextSelectionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.OriginalContextSelectionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.task, self.views = self.fixture.task, self.fixture.views
        self.task[POLICY] = 1
        self.schema = schema_module.original_span_output_schema(self.task, self.views)

    def api_variants(self, schema=None):
        return [variant for variant in (schema or self.schema)['properties']['selections']['items']['anyOf']
                if variant['properties']['kind']['enum'] == ['api']]

    def test_existing_currency_is_not_a_candidate_but_missing_valuation_date_is(self):
        offered = set().union(*(fields(variant) for variant in self.api_variants()))
        self.assertNotIn('currency', offered)
        self.assertNotIn('fund_id', offered)
        self.assertIn('valuation_date', offered)
        self.assertNotIn('price_kind', offered)

    def test_missing_valuation_date_remains_explicit_and_does_not_change_price(self):
        result = schema_module.decode_original_spans(self.fixture.wire, self.schema,
                                                    (self.task, self.views))['evidence_specs'][0]
        self.assertEqual(result['calculation_context']['fund_id'], '510500.SH')
        self.assertEqual(result['calculation_context']['currency'], 'CNY')
        self.assertEqual(result['calculation_context_basis']['currency']['kind'], 'api')
        self.assertEqual(result['calculation_context_basis']['valuation_date']['kind'], 'material')
        self.assertEqual(result['calculation_context']['valuation_date'], '2026-06-30')
        self.assertNotIn('source_check', result)
        self.assertFalse(self.task['calculations'])

    def test_existing_currency_even_equal_is_rejected_not_ignored(self):
        wire = copy.deepcopy(self.fixture.wire)
        wire['selections'][0]['additional_contexts'][0].update(field='currency', value='CNY')
        with self.assertRaises(ContractError):
            schema_module.decode_original_spans(wire, self.schema, (self.task, self.views))

    def test_closed_live_study_offers_only_actual_cell_missing_fields(self):
        path = ROOT / 'result/acceptance/one-sentence-research-im8psgsm/task.json'
        original = path.read_bytes()
        task = json.loads(original); task[POLICY] = 1
        views = task['reasoning_state']['reading']['bundle']['views']
        before = copy.deepcopy((task, views))
        schema = schema_module.original_span_output_schema(task, views)
        offered = set().union(*(fields(variant) for variant in self.api_variants(schema)))
        self.assertEqual(offered, {'fund_id', 'share_class', 'valuation_date'})
        self.assertEqual((task, views), before)
        self.assertEqual(path.read_bytes(), original)

    def test_cells_with_different_context_keys_get_distinct_selection_groups(self):
        choice, original = schema_module._original_span_choices(self.task, self.views)
        choice = copy.deepcopy(choice)
        first = choice['source']['cell_choices'][0]
        second = copy.deepcopy(first); second['candidate_id'] = 'synthetic_missing_currency_cell'
        second['calculation_context'].pop('currency')
        choice['source']['cell_choices'].append(second)
        enum = choice['api_schema']['properties']['evidence_specs']['items']['anyOf'][0]['properties']['api_cell_selections']['items']['enum']
        enum.append(second['candidate_id'])
        groups = schema_module._missing_api_context_variants(choice, [choice, original],
                    ['currency', 'valuation_date', 'price_kind'], False, self.task)
        self.assertEqual(len(groups), 2)
        grouped = {}
        for group in groups:
            options = group['properties']['selection']['properties']['evidence_specs']['items']['anyOf']
            ids = {ident for option in options
                for ident in option['properties']['api_cell_selections']['items']['enum']}
            for ident in ids:
                self.assertNotIn(ident, grouped)
                grouped[ident] = fields(group)
        self.assertNotIn('currency', grouped[first['candidate_id']])
        self.assertIn('currency', grouped[second['candidate_id']])
        self.assertEqual(set(grouped), {first['candidate_id'], second['candidate_id']})

    def test_other_fund_original_never_supplies_current_cell_context(self):
        choice, original = copy.deepcopy(schema_module._original_span_choices(self.task, self.views))
        original['description']['source']['object_binding']['value'] = '600519.SH'
        variants = schema_module._missing_api_context_variants(choice, [choice, original], ['valuation_date'], False, self.task)
        self.assertTrue(variants)
        self.assertTrue(all(not fields(variant) for variant in variants))

    def test_legacy_schema_and_input_binding_are_preserved(self):
        legacy = copy.deepcopy(self.task); legacy.pop(POLICY)
        old_schema = schema_module.original_span_output_schema(legacy, self.views)
        self.assertIn('currency', set().union(*(fields(variant) for variant in self.api_variants(old_schema))))
        self.assertNotEqual(input_fingerprint(self.task), input_fingerprint(legacy))
        for value in (True, 0, 2):
            task = copy.deepcopy(legacy); task[POLICY] = value
            self.assertEqual(schema_module.original_span_output_schema(task, self.views), old_schema)

    def test_formal_tasks_and_frozen_content_keep_explicit_policy(self):
        with tempfile.TemporaryDirectory(prefix='missing-context-policy-') as directory:
            agent = ResearchAgent(ResearchArchive(directory), ROOT, offline=True, require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            task = agent.create('解释ETF净值', questions=[dict(id='q1', kind='fact', question='解释ETF净值',
                answer_scope='general_definition', knowledge_needs=['rh_k_etf_premium_measures'])])
            self.assertEqual(task[POLICY], 1)
            done = agent.start(task['id'])
            self.assertEqual(done['status'], '已完成')
            self.assertEqual(compose(done)[POLICY], 1)
            self.assertNotIn(POLICY, agent.create_case('mechanism'))

    def check_transport(self, responses):
        # Isolated protocol fixture only. Never alters a real study's budget.
        self.task['ledger']['limits']['model_calls'] += 1
        body = json.dumps(self.fixture.wire)
        if responses:
            reply = dict(object='response', status='completed', error=None, incomplete_details=None,
                output=[dict(type='message', role='assistant', status='completed',
                    content=[dict(type='output_text', text=body)])], usage={})
        else:
            reply = dict(choices=[dict(finish_reason='tool_calls', message=dict(role='assistant', content=None,
                tool_calls=[dict(id='synthetic_missing_context', type='function', function=dict(
                    name='rh_select_original_spans_v1', arguments=body))]))], usage={})
        with tempfile.TemporaryDirectory(prefix='missing-context-transport-') as directory:
            settings = ModelSettings(directory)
            settings.save(dict(endpoint='https://api.deepseek.com/responses' if responses else
                'https://api.deepseek.com/beta/chat/completions', model='deepseek-flash', api_key='FAKE_CONTEXT_KEY'))
            service = ModelService(settings, offline=False)
            with patch.object(service, '_request_bytes', return_value=json.dumps(reply).encode()) as request:
                result, receipt = model_object(service, self.task, 'extract', self.fixture.payload, PHASE_FIELDS['extract'])
            request.assert_called_once()
            self.assertEqual(result['evidence_specs'][0]['calculation_context']['currency'], 'CNY')
            self.assertEqual(result['evidence_specs'][0]['calculation_context']['fund_id'], '510500.SH')
            self.assertEqual(receipt['output_protocol'], 'strict_original_spans_v1')

    def test_new_missing_only_schema_uses_actual_responses_decoder(self):
        self.check_transport(True)

    def test_new_missing_only_schema_uses_actual_beta_decoder(self):
        self.check_transport(False)


if __name__ == '__main__':
    unittest.main()
