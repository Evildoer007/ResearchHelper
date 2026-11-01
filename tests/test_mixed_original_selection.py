"""A mixed API/original batch keeps both sources and both original validators."""
import copy
import json
import tempfile
import unittest
from unittest.mock import patch

from tests import test_original_span_selection as fixtures
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.output_schema import original_span_output_schema, original_span_prompt, decode_original_spans
from researchhelper.reasoning import extractable_source_views, evidence_contract, expand_api_cell_selections, model_object, PHASE_FIELDS
from researchhelper.model import ModelService, ModelSettings


class MixedOriginalSelectionTests(unittest.TestCase):
    def setUp(self):
        fixtures.OriginalSpanSelectionTests.setUpClass()
        fixture = fixtures.OriginalSpanSelectionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.addCleanup(fixtures.OriginalSpanSelectionTests.tearDownClass)
        self.fixture = fixture
        self.task = fixture.task
        self.task['mixed_original_selection_version'] = 1
        api = next(view for view in extractable_source_views(self.task) if view['kind'] == 'api')
        self.views = [api] + fixture.views
        self.schema = original_span_output_schema(self.task, self.views)
        self.payload = dict(untrusted_sources=self.views, evidence_contract=evidence_contract(self.task, self.views))
        self.prompt = original_span_prompt(self.payload, self.task)
        source = self.prompt['evidence_contract']['sources'][0]
        self.price_selection = dict(api_cell_selections=[source['cell_choices'][0]['candidate_id']], question_ids=['q1'],
            review=dict(question_ids=['q1'], allowed_natures=['已实现事实'], conditions=['仅当前同源行情'],
                reason='混合选择协议夹具，不是金融结论。', evidence_need_texts=[dict(question_id='q1',
                    texts=[self.payload['evidence_contract']['question_requirements']['q1'][0]['text']])]))
        original = dict(fixture.item, source_ref='source_2')
        self.wire = dict(version=2, binding_ref=self.prompt['span_binding_ref'], selections=[
            dict(source_ref='source_1', kind='api', selection=dict(version=2, evidence_specs=[self.price_selection])), original])

    def decode(self, wire=None, views=None, schema=None):
        return decode_original_spans(wire or self.wire, schema or self.schema, (self.task, views or self.views))

    def test_both_original_and_api_survive_one_bound_selection(self):
        before = fingerprint((self.task, self.views, self.wire))
        result = self.decode()
        self.assertEqual(len(result['evidence_specs']), 2)
        self.assertIn('api_cell_selections', result['evidence_specs'][0])
        self.assertEqual(result['evidence_specs'][1]['value'], 9.1808)
        expanded = expand_api_cell_selections(self.task, result['evidence_specs'])
        self.assertEqual(len(expanded), 2)
        self.assertEqual(expanded[0]['record_locator']['field'], 'close')
        self.assertEqual(expanded[1]['text'], self.fixture.nav_line['text'].rstrip('\r\n'))
        self.assertFalse(any('source_check' in spec or 'applicability_check' in spec for spec in expanded))
        self.assertEqual(before, fingerprint((self.task, self.views, self.wire)))

    def test_no_source_is_removed_from_model_input(self):
        self.assertEqual([view['id'] for view in self.prompt['untrusted_sources']], [view['id'] for view in self.views])
        self.assertEqual({key: value for key, value in self.prompt['untrusted_sources'][0].items()
                          if key not in ('source_ref', 'selection_kind')}, self.views[0])
        self.assertEqual(len(self.prompt['untrusted_sources'][1]['parts']), len(self.views[1]['parts']))

    def test_legacy_and_all_api_batches_do_not_gain_new_protocol(self):
        self.task.pop('mixed_original_selection_version')
        self.assertIsNone(original_span_output_schema(self.task, self.views))
        self.assertEqual(original_span_output_schema(self.task, self.fixture.views)['properties']['version']['enum'], [1])
        self.task['mixed_original_selection_version'] = 1
        self.assertIsNone(original_span_output_schema(self.task, self.views[:1]))

    def test_raw_only_or_unknown_api_cannot_be_discarded_to_fit_batch(self):
        raw = next(view for view in self.task['source_records'] if view['operation'] == 'date_sequence')
        self.assertIsNone(original_span_output_schema(self.task, self.views + [dict(
            id=raw['id'], kind='api', snapshot_hash=raw['hash'], data=raw['snapshot'])]))

    def test_cross_source_selection_and_changed_api_binding_are_rejected(self):
        wire = copy.deepcopy(self.wire); wire['selections'][0]['source_ref'] = 'source_2'
        with self.assertRaises(ContractError): self.decode(wire)
        views = copy.deepcopy(self.views); views[0]['snapshot_hash'] = 'changed'
        with self.assertRaises(ContractError): self.decode(views=views)

    def test_invalid_api_selection_is_not_silently_dropped(self):
        wire = copy.deepcopy(self.wire)
        wire['selections'][0]['selection']['evidence_specs'][0]['api_cell_selections'] = ['foreign_cell']
        with self.assertRaises(ContractError): self.decode(wire)

    def test_real_model_entry_uses_mixed_schema_and_expands_api_without_second_model(self):
        with tempfile.TemporaryDirectory(prefix='mixed-span-model-') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/beta/chat/completions',
                model='deepseek-flash', api_key='FAKE_MIXED_SPAN_KEY'))
            model = ModelService(settings, offline=False)
            with patch.object(model, 'reason', return_value=json.dumps(self.decode())) as transport:
                result, receipt = model_object(model, self.task, 'extract', self.payload, PHASE_FIELDS['extract'])
            transport.assert_called_once()
            self.assertEqual(transport.call_args.kwargs['output_schema']['properties']['version']['enum'], [2])
            self.assertEqual(len(result['evidence_specs']), 2)
            self.assertEqual(result['evidence_specs'][0]['record_locator']['field'], 'close')
            self.assertEqual(result['evidence_specs'][1]['raw_token'], '9.1808')
            self.assertEqual(receipt['output_protocol'], 'strict_original_spans_v1')

    def strict_transport(self, responses=False):
        # Keep the existing transport assertions; select original first so
        # their NAV check stays unchanged while exercising a mixed envelope.
        fixture = self.fixture
        fixture.views, fixture.schema, fixture.payload = self.views, self.schema, self.payload
        fixture.wire = dict(self.wire, selections=list(reversed(self.wire['selections'])))
        fixture.strict_transport(responses=responses)

    def test_mixed_beta_wire_uses_actual_request_decoder(self):
        self.strict_transport()

    def test_mixed_responses_wire_uses_actual_request_decoder(self):
        self.strict_transport(responses=True)

    def test_new_policy_is_frozen_without_upgrading_fixture_tasks(self):
        from tests import ROOT
        from researchhelper.archive import ResearchArchive
        from researchhelper.engine import ResearchAgent
        from researchhelper.reports import compose
        with tempfile.TemporaryDirectory(prefix='mixed-policy-') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            try:
                task = agent.create('解释折溢价', entry='ETF', questions=[dict(id='q1', question='解释折溢价',
                    kind='fact', answer_scope='general_definition', knowledge_needs=['rh_k_etf_premium_measures'])])
                self.assertEqual(task['mixed_original_selection_version'], 1)
                done = agent.start(task['id'])
                self.assertEqual(done['status'], '已完成')
                self.assertEqual(compose(done)['mixed_original_selection_version'], 1)
                self.assertNotIn('mixed_original_selection_version', agent.create_case('mechanism'))
            finally:
                for worker in agent.close_runtime(): worker.join(timeout=5)


class DisclosureWindowTests(unittest.TestCase):
    def setUp(self):
        fixtures.OriginalSpanSelectionTests.setUpClass()
        self.task = copy.deepcopy(fixtures.OriginalSpanSelectionTests.saved)
        self.addCleanup(fixtures.OriginalSpanSelectionTests.tearDownClass)

    def test_disclosure_contract_is_detached_and_preserves_actual_request_grammar(self):
        from researchhelper.evidence import data_request_contract, IFIND_REPORT_FIELDS
        before = fingerprint(self.task)
        tool = next(tool for tool in data_request_contract(self.task)['tools'] if tool['operation'] == 'report_query')
        self.assertEqual(tool['parameter_keys'], ['codes', 'functionpara', 'beginrDate', 'endrDate', 'outputpara'])
        self.assertEqual(tool['fixed_parameters']['outputpara'], ','.join(field + ':Y' for field in IFIND_REPORT_FIELDS))
        self.assertEqual(tool['parameter_options']['functionpara'], [{}, {'reportType': '901'}])
        self.assertFalse(tool['date_semantics']['automatic_window'])
        self.assertFalse(tool['date_semantics']['financial_approval'])
        tool['date_semantics']['fields'].clear()
        self.assertEqual(before, fingerprint(self.task))
        again = next(tool for tool in data_request_contract(self.task)['tools'] if tool['operation'] == 'report_query')
        self.assertEqual(again['date_semantics']['fields'], ['beginrDate', 'endrDate'])

    def test_current_observation_day_does_not_limit_later_disclosure_search(self):
        from researchhelper.evidence import validate_ifind_report_request, IFIND_REPORT_FIELDS
        parameters = dict(codes='510500.SH', functionpara={}, beginrDate='2026-07-01', endrDate='2026-09-30',
            outputpara=','.join(field + ':Y' for field in IFIND_REPORT_FIELDS))
        self.assertEqual(validate_ifind_report_request(self.task, parameters), ['2026-07-01', '2026-09-30'])
        with self.assertRaises(ContractError):
            validate_ifind_report_request(dict(self.task, research_mode='historical', cutoff='2026-06-30T18:00:00+08:00'), parameters)


if __name__ == '__main__':
    unittest.main()
