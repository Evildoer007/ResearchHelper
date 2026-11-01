"""Supplement API inputs with explicit same-object original anchors only."""
import copy
import json
import tempfile
import unittest
from unittest.mock import patch

from tests import test_mixed_original_selection as fixtures
import researchhelper.output_schema as schema_module
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.evidence import candidate_evidence
from researchhelper.model import ModelService, ModelSettings
from researchhelper.reasoning import evidence_contract, model_object, PHASE_FIELDS


class OriginalContextSelectionTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.MixedOriginalSelectionTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.task, self.views = fixture.task, fixture.views
        # Unit projection only: the closed study is neither revised nor run.
        self.task['original_context_selection_version'] = 1
        self.task['questions'][0]['method_needs'] = ['etf_daily_nav_premium_v1']
        self.schema = schema_module.original_span_output_schema(self.task, self.views)
        self.payload = dict(untrusted_sources=self.views, evidence_contract=evidence_contract(self.task, self.views))
        self.prompt = schema_module.original_span_prompt(self.payload, self.task)
        line = next(line for line in fixture.fixture.lines if '基金主代码' in line['text'] and '510500' in line['text'])
        self.context = dict(field='fund_id', value='510500', source_ref='source_2',
                            span=dict(start=line['index'], end=line['index']))
        self.wire = copy.deepcopy(fixture.wire)
        self.wire.update(version=3, binding_ref=self.prompt['span_binding_ref'])
        self.wire['selections'][0]['additional_contexts'] = [self.context]

    def decode(self, wire=None, views=None):
        return schema_module.decode_original_spans(wire or self.wire, self.schema, (self.task, views or self.views))

    def test_original_context_is_added_without_changing_price_or_existing_scope(self):
        before = fingerprint((self.task, self.views, self.wire))
        selected = self.decode()['evidence_specs'][0]
        self.assertEqual(selected['calculation_context']['fund_id'], '510500')
        self.assertEqual(selected['calculation_context']['currency'], 'CNY')
        self.assertEqual(selected['calculation_context']['price_kind'], 'close')
        self.assertEqual(selected['calculation_context']['corporate_action_status'], 'unknown')
        self.assertNotIn('api_context_reference', selected)
        self.assertEqual(selected['calculation_context_basis']['fund_id']['kind'], 'material')
        self.assertEqual(selected['calculation_context_basis']['currency']['kind'], 'api')
        self.assertNotIn('source_check', selected)
        self.assertEqual(before, fingerprint((self.task, self.views, self.wire)))

    def test_existing_currency_or_price_definition_cannot_be_overwritten_even_if_equal(self):
        for field, value in (('currency','CNY'), ('currency','USD'), ('price_kind','intraday')):
            wire = copy.deepcopy(self.wire)
            wire['selections'][0]['additional_contexts'][0].update(field=field, value=value)
            with self.subTest(field=field, value=value), self.assertRaises(ContractError): self.decode(wire)

    def test_duplicate_contexts_do_not_accumulate_or_partially_mutate_inputs(self):
        wire = copy.deepcopy(self.wire)
        wire['selections'][0]['additional_contexts'] *= 2
        before = fingerprint((wire, self.task, self.views))
        with self.assertRaises(ContractError): self.decode(wire)
        self.assertEqual(before, fingerprint((wire, self.task, self.views)))

    def test_api_source_cannot_be_submitted_as_an_original_anchor(self):
        wire = copy.deepcopy(self.wire)
        wire['selections'][0]['additional_contexts'][0]['source_ref'] = 'source_1'
        with self.assertRaises(ContractError): self.decode(wire)

    def test_metadata_enrichment_cannot_relabel_a_price_as_official_nav(self):
        wire = copy.deepcopy(self.wire)
        wire['selections'][0]['additional_contexts'][0].update(field='nav_kind', value='official')
        with self.assertRaises(ContractError): self.decode(wire)

    def test_even_a_structurally_matching_foreign_object_binding_is_rejected(self):
        choices = schema_module._original_span_choices(self.task, self.views)
        choices[1]['description']['source']['object_binding']['value'] = '600519.SH'
        with patch.object(schema_module, '_original_span_choices', return_value=choices):
            with self.assertRaisesRegex(ContractError, '不跨基金'):
                self.decode()

    def test_api_numeric_source_validation_still_checks_new_original_anchor(self):
        record = next(record for record in self.task['source_records'] if record['id'] == self.views[0]['id'])
        selected = self.decode()['evidence_specs'][0]
        valid = candidate_evidence(self.task, record, selected)
        self.assertEqual(valid['calculation_context']['fund_id'], '510500')
        self.assertEqual(valid['value'], self.fixture.task['evidence'][0]['value'])
        invalid = copy.deepcopy(selected); invalid['calculation_context']['fund_id'] = '510501'
        with self.assertRaises(ContractError): candidate_evidence(self.task, record, invalid)

    def test_absent_contexts_preserve_the_original_api_selection_not_a_fake_metadata_default(self):
        wire = copy.deepcopy(self.wire); wire['selections'][0]['additional_contexts'] = []
        result = self.decode(wire)['evidence_specs'][0]
        self.assertIn('api_cell_selections', result)
        self.assertNotIn('calculation_context', result)

    def test_legacy_mixed_tasks_keep_version_two_and_no_new_context_field(self):
        self.task.pop('original_context_selection_version')
        schema = schema_module.original_span_output_schema(self.task, self.views)
        self.assertEqual(schema['properties']['version']['enum'], [2])
        with self.assertRaises(ContractError): self.decode()

    def test_methods_without_context_keys_never_generate_an_empty_enum(self):
        self.task['questions'][0]['method_needs'] = ['fee_difference_v1']
        schema = schema_module.original_span_output_schema(self.task, self.views)
        self.assertIsNotNone(schema)
        def visit(value):
            if isinstance(value, dict):
                if 'enum' in value: self.assertTrue(value['enum'])
                for child in value.values(): visit(child)
            elif isinstance(value, list):
                for child in value: visit(child)
        visit(schema)

    def test_model_pipeline_uses_one_mixed_context_protocol_without_approving_facts(self):
        with tempfile.TemporaryDirectory(prefix='api-original-context-') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/beta/chat/completions',
                model='deepseek-flash', api_key='FAKE_ORIGINAL_CONTEXT_KEY'))
            model = ModelService(settings, offline=False)
            with patch.object(model, 'reason', return_value=json.dumps(self.decode())) as transport:
                result, receipt = model_object(model, self.task, 'extract', self.payload, PHASE_FIELDS['extract'])
            transport.assert_called_once()
            self.assertEqual(transport.call_args.kwargs['output_schema']['properties']['version']['enum'], [3])
            self.assertEqual(result['evidence_specs'][0]['calculation_context']['fund_id'], '510500')
            self.assertNotIn('source_check', result['evidence_specs'][0])
            prompt = transport.call_args.args[1][0]['content']
            self.assertNotIn('kind=api的条目仅含source_ref、kind、selection；', prompt)
            self.assertEqual(receipt['output_protocol'], 'strict_original_spans_v1')

    def test_new_context_policy_is_created_and_frozen_without_upgrading_cases(self):
        from tests import ROOT
        from researchhelper.archive import ResearchArchive
        from researchhelper.engine import ResearchAgent
        from researchhelper.reports import compose
        with tempfile.TemporaryDirectory(prefix='original-context-policy-') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            try:
                task = agent.create('解释ETF折溢价', entry='ETF', questions=[dict(id='q1', kind='fact',
                    question='解释ETF折溢价', answer_scope='general_definition',
                    knowledge_needs=['rh_k_etf_premium_measures'])])
                self.assertEqual(task['original_context_selection_version'], 1)
                done = agent.start(task['id'])
                self.assertEqual(done['status'], '已完成')
                self.assertEqual(compose(done)['original_context_selection_version'], 1)
                self.assertNotIn('original_context_selection_version', agent.create_case('mechanism'))
            finally:
                for worker in agent.close_runtime():
                    worker.join(timeout=5)
                    self.assertFalse(worker.is_alive())

    def strict_transport(self, responses=False):
        before_calls = self.task['ledger']['used']['model_calls']
        before_data = self.task['ledger']['used']['data_requests']
        # Mocked transport allowance on this disposable unit projection only.
        self.task['ledger']['limits']['model_calls'] += 1
        with tempfile.TemporaryDirectory(prefix='original-context-wire-') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses' if responses else
                'https://api.deepseek.com/beta/chat/completions', model='deepseek-flash', api_key='FAKE_CONTEXT_WIRE_KEY'))
            model = ModelService(settings, offline=False)
            if responses:
                reply = dict(object='response', status='completed', error=None, incomplete_details=None,
                    output=[dict(type='message',role='assistant',status='completed',
                        content=[dict(type='output_text',text=json.dumps(self.wire))])],usage={})
            else:
                reply = dict(choices=[dict(finish_reason='tool_calls',message=dict(role='assistant',content=None,
                    tool_calls=[dict(id='synthetic_context',type='function',function=dict(
                        name='rh_select_original_spans_v1',arguments=json.dumps(self.wire)))]))],usage={})
            with patch.object(model, '_request_bytes', return_value=json.dumps(reply).encode()) as transport:
                result, receipt = model_object(model, self.task, 'extract', self.payload, PHASE_FIELDS['extract'])
            transport.assert_called_once()
            self.assertEqual(result['evidence_specs'][0]['calculation_context']['fund_id'], '510500')
            self.assertEqual(receipt['output_protocol'], 'strict_original_spans_v1')
            self.assertEqual(self.task['ledger']['used']['model_calls'], before_calls + 1)
            self.assertEqual(self.task['ledger']['used']['data_requests'], before_data)

    def test_beta_actual_context_wire_and_decoder(self):
        self.strict_transport()

    def test_responses_actual_context_wire_and_decoder(self):
        self.strict_transport(responses=True)


if __name__ == '__main__':
    unittest.main()
