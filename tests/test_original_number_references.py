"""Exact number selection without ordinal counting or financial inference."""
import copy
import json
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT, test_listed_etf_unit_context as fixtures
from tests.legacy_replay_fixtures import ETF_CLOSE, ETF_NAV, ETF_VALUATION_DATE
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint, calculation_context_contract, validate_calculation_context
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import candidate_evidence, validate_official_nav_number
from researchhelper.model import ModelService, ModelSettings
from researchhelper.output_schema import original_span_output_schema, original_span_prompt, decode_original_spans
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import model_object, PHASE_FIELDS
from researchhelper.reports import compose


POLICY = 'original_number_reference_version'


class OriginalNumberReferenceTests(unittest.TestCase):
    def setUp(self):
        fixtures.ListedETFUnitContextTests.setUpClass()
        self.fixture = fixtures.ListedETFUnitContextTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.task, self.views = self.fixture.task, self.fixture.views
        self.task[POLICY] = 1
        self.schema = original_span_output_schema(self.task, self.views)
        self.prompt = original_span_prompt(self.fixture.payload, self.task)
        self.wire = self.fixture.selection(); self.wire['version'] = 4
        self.wire['binding_ref'] = self.prompt['span_binding_ref']
        original = next(view for view in self.prompt['untrusted_sources'] if view.get('kind') == 'filing')
        self.tokens = [row for part in original['parts'] for row in part.get('number_tokens', [])]
        self.number = self.wire['selections'][1]
        self.number.pop('number_occurrence')
        self.nav = next((row for row in self.tokens if row[2] == ETF_NAV
                         and row[1] == self.number['text_span']['start']), None)
        self.number['number_ref'] = self.nav[0] if self.nav else 'missing_new_reference'

    def decode(self, wire=None, views=None):
        return decode_original_spans(wire or self.wire, self.schema, (self.task, views or self.views))

    def test_v4_offers_source_bound_literal_tokens_and_no_ordinal_field(self):
        self.assertEqual(self.schema['properties']['version']['enum'], [4])
        self.assertIsNotNone(self.nav)
        variants = self.schema['properties']['selections']['items']['anyOf']
        numeric = [v['properties'] for v in variants if v['properties']['kind']['enum'] == ['number']]
        self.assertTrue(numeric)
        self.assertTrue(all('number_ref' in shape and 'number_occurrence' not in shape for shape in numeric))
        original = next(view for view in self.prompt['untrusted_sources'] if view.get('kind') == 'filing')
        actual = next(view for view in self.views if view['id'] == original['id'])
        for before, after in zip(actual['parts'], original['parts']):
            self.assertEqual(before['text'], ''.join(line[1] for line in after['indexed_lines']))

    def test_exact_nav_survives_dates_and_other_numbers_on_same_line(self):
        before = fingerprint((self.task, self.views, self.wire))
        result = self.decode()['evidence_specs'][1]
        self.assertEqual(result['raw_token'], ETF_NAV)
        self.assertEqual(result['value'], float(ETF_NAV))
        self.assertEqual(before, fingerprint((self.task, self.views, self.wire)))
        self.assertNotIn('source_check', result)

    def test_real_source_gates_still_require_both_comparable_inputs(self):
        result = self.decode()['evidence_specs']
        adopted = [candidate_evidence(self.task, next(s for s in self.task['source_records']
            if s['id'] == spec['artifact_id']), spec) for spec in result]
        from researchhelper.contracts import METHOD_REGISTRY
        method = 'etf_daily_nav_premium_v1'
        validate_calculation_context(method, dict(price=adopted[0], nav=adopted[1]),
            dict(METHOD_REGISTRY[method]['parameters'], valuation_date=ETF_VALUATION_DATE),
            context_contract=calculation_context_contract(self.task, method))
        self.assertEqual([item['value'] for item in adopted], [ETF_CLOSE, float(ETF_NAV)])
        self.assertFalse(self.task['calculations'])

    def test_wrong_source_unknown_token_and_unread_span_are_not_repaired(self):
        for ref in ('foreign_number', 'num999_0_0'):
            wire = copy.deepcopy(self.wire); wire['selections'][1]['number_ref'] = ref
            with self.subTest(ref=ref), self.assertRaises(ContractError): self.decode(wire)
        other = next(row for row in self.tokens if row[1] != self.nav[1])
        wire = copy.deepcopy(self.wire); wire['selections'][1]['number_ref'] = other[0]
        with self.assertRaises(ContractError): self.decode(wire)
        views = copy.deepcopy(self.views); views[-1]['snapshot_hash'] = 'changed'
        with self.assertRaises(ContractError): self.decode(views=views)

    def test_date_token_is_never_silently_replaced_with_nav(self):
        date = next(row for row in self.tokens if row[1] == self.nav[1] and row[2] == '2026')
        wire = copy.deepcopy(self.wire); wire['selections'][1]['number_ref'] = date[0]
        selected = self.decode(wire)['evidence_specs'][1]
        self.assertEqual(selected['value'], 2026)
        with self.assertRaises(ContractError): validate_official_nav_number(selected, selected['text'])

    def test_only_an_explicit_single_date_label_is_normalized(self):
        wire = copy.deepcopy(self.wire); wire['selections'][1]['period'] = '2026年6月30日'
        result = self.decode(wire)['evidence_specs'][1]
        self.assertEqual(result['period'], '2026-06-30')
        self.assertIn('6', result['record_locator']['period_quote'])
        for period in ('2026年4月1日-2026年6月30日', '2026年6月29日', '2026-06-30T15:00:00+08:00'):
            wire['selections'][1]['period'] = period
            result = self.decode(wire)['evidence_specs'][1]
            self.assertEqual(result['period'], period)
            with self.assertRaises(ContractError):
                candidate_evidence(self.task, next(s for s in self.task['source_records']
                    if s['id'] == result['artifact_id']), result)

    def test_registered_context_values_are_codes_not_display_labels(self):
        for field, value in (('nav_kind', '单位净值'), ('fund_id', '基金名称'), ('currency', '人民币')):
            wire = copy.deepcopy(self.wire)
            next(c for c in wire['selections'][1]['contexts'] if c['field'] == field)['value'] = value
            with self.subTest(field=field), self.assertRaises(ContractError): self.decode(wire)

    def test_legacy_schema_and_ordinal_wire_remain_version_three(self):
        self.task.pop(POLICY)
        old = original_span_output_schema(self.task, self.views)
        self.assertEqual(old['properties']['version']['enum'], [3])
        decode_original_spans(self.fixture.selection(), old, (self.task, self.views))
        with self.assertRaises(ContractError): self.decode()

    def test_model_entry_uses_v4_and_records_version_without_approving_values(self):
        with tempfile.TemporaryDirectory(prefix='number-reference-model-') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash', api_key='FAKE_NUMBER_REF'))
            service = ModelService(settings, offline=False)
            with patch.object(service, 'reason', return_value=json.dumps(self.decode())) as call:
                _, receipt = model_object(service, self.task, 'extract', self.fixture.payload, PHASE_FIELDS['extract'])
            self.assertEqual(call.call_args.kwargs['output_schema']['properties']['version']['enum'], [4])
            self.assertIn('number_ref', call.call_args.args[1][0]['content'])
            self.assertNotIn('number_occurrence是', call.call_args.args[1][0]['content'])
            self.assertEqual(receipt[POLICY], 1)

    def test_actual_responses_request_and_decoder_use_the_same_numeric_address(self):
        before = copy.deepcopy(self.task['ledger'])
        with tempfile.TemporaryDirectory(prefix='number-reference-response-') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash', api_key='FAKE_NUMBER_WIRE'))
            service = ModelService(settings, offline=False)
            response = dict(object='response', status='completed', error=None, incomplete_details=None,
                output=[dict(type='message', role='assistant', status='completed',
                    content=[dict(type='output_text', text=json.dumps(self.wire))])], usage={})
            with patch.object(service, '_request_bytes', return_value=json.dumps(response).encode()) as transport:
                result = json.loads(service.reason_structured_original_spans(self.task, [], self.views))
            request = json.loads(transport.call_args.args[1].data)
            self.assertEqual(request['text']['format']['schema']['properties']['version']['enum'], [4])
            self.assertEqual(result['evidence_specs'][1]['raw_token'], ETF_NAV)
        self.assertEqual(self.task['ledger']['used']['model_calls'], before['used']['model_calls'] + 1)
        self.assertEqual(self.task['ledger']['used']['data_requests'], before['used']['data_requests'])
        self.assertEqual(self.task['ledger']['limits'], before['limits'])

    def test_new_task_policy_is_versioned_and_frozen(self):
        with tempfile.TemporaryDirectory(prefix='number-reference-policy-') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            try:
                task = agent.create('解释ETF净值', questions=[dict(id='q1', kind='fact', question='解释ETF净值',
                    answer_scope='general_definition', knowledge_needs=['rh_k_etf_premium_measures'])])
                self.assertEqual(task[POLICY], 1)
                changed = copy.deepcopy(task); changed.pop(POLICY)
                self.assertNotEqual(task['input_hash'], input_fingerprint(changed))
                done = compose(agent.start(task['id']))
                self.assertEqual(done[POLICY], 1)
                self.assertEqual(done['evidence_gap_lifecycle_version'], 1)
                self.assertNotIn(POLICY, agent.create_case('mechanism'))
            finally:
                agent.close_runtime()


if __name__ == '__main__':
    unittest.main()
