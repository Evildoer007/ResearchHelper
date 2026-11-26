"""Lossless positions over a fixed, synthetic ETF original."""
import copy
import json
import socket
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from tests.legacy_replay_fixtures import ETF_ORIGINAL, etf_task
from researchhelper.contracts import ContractError, fingerprint, budget
from researchhelper.financial_sources import original_line_index
from researchhelper.output_schema import (original_span_output_schema, original_span_prompt,
    decode_original_spans, structured_json_completion, ORIGINAL_SPAN_PROTOCOL)
from researchhelper.reasoning import evidence_contract, extractable_source_views, model_object, PHASE_FIELDS
from researchhelper.model import ModelSettings, ModelService


class OriginalSpanSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_bytes = ETF_ORIGINAL.read_bytes()

    @classmethod
    def tearDownClass(cls):
        if ETF_ORIGINAL.read_bytes() != cls.original_bytes:
            raise AssertionError('Fixed original was modified')

    def setUp(self):
        self.task = etf_task()
        self.views = [view for view in extractable_source_views(self.task) if view['kind'] == 'filing']
        self.lines = original_line_index(self.views[0])
        self.schema = original_span_output_schema(self.task, self.views)
        self.payload = dict(untrusted_sources=self.views, evidence_contract=evidence_contract(self.task, self.views))
        self.prompt = original_span_prompt(self.payload, self.task)
        self.nav_line = next(line for line in self.lines if line['part'] == '第2页'
                             and '期末基金份额净值' in line['text'] and '9.1808' in line['text'])
        self.unit_line = next(line for line in self.lines if line['part'] == '第2页' and '金额单位' in line['text'])
        self.date_index = next(anchor['index'] for anchor in self.prompt['untrusted_sources'][0]['observation_anchors']
                               if anchor['date'] == '2026-06-30')
        requirements = self.payload['evidence_contract']['question_requirements']['q1']
        self.item = dict(id='nav_from_exact_lines', source_ref='source_1', object='510500.SH', nature='已实现事实',
            question_ids=['q1'], period='2026-06-30', observation_anchor=self.date_index,
            text_span=dict(start=self.nav_line['index'], end=self.nav_line['index']), kind='number',
            number_occurrence=0, unit='元', unit_span=dict(start=self.unit_line['index'], end=self.unit_line['index']),
            contexts=[], review=dict(scope='current_original_descriptive_only',
                need_links=[dict(question_id='q1', need_ids=[requirements[-1]['id']])]))
        self.wire = dict(version=1, binding_ref=self.prompt['span_binding_ref'], selections=[self.item])
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No external calls'))
        guard.start()
        self.addCleanup(guard.stop)

    def decode(self, wire=None, task=None, views=None, schema=None):
        return decode_original_spans(wire or self.wire, schema or self.schema,
                                     (task or self.task, views or self.views))

    def test_actual_nav_restores_exact_text_and_number_without_fuzzy_matching(self):
        before = fingerprint((self.task, self.views, self.wire))
        spec = self.decode()['evidence_specs'][0]
        self.assertEqual(spec['text'], self.nav_line['text'].rstrip('\r\n'))
        self.assertEqual(spec['raw_token'], '9.1808')
        self.assertEqual(spec['value'], 9.1808)
        self.assertNotIn('calculation_context', spec)
        self.assertIsNone(spec['published_at'])
        self.assertNotIn('source_check', spec)
        self.assertNotIn('applicability_check', spec)
        self.assertEqual(before, fingerprint((self.task, self.views, self.wire)))

    def test_indices_preserve_every_read_character(self):
        for original, projected in zip(self.views[0]['parts'], self.prompt['untrusted_sources'][0]['parts']):
            self.assertEqual(''.join(line[1] for line in projected['indexed_lines']), original['text'])
            self.assertNotIn('text', projected)

    def test_task_version_batch_cutoff_and_input_changes_reject_stale_wire(self):
        for key, value in (('id', 'foreign'), ('version', 999), ('batch', 999),
                           ('cutoff', '2026-10-18'), ('input_hash', 'different')):
            task = dict(self.task, **{key: value})
            with self.subTest(key=key), self.assertRaises(ContractError):
                self.decode(task=task)

    def test_changed_or_unread_fragments_cannot_reuse_binding(self):
        for change in ('text', 'parts', 'hash'):
            views = copy.deepcopy(self.views)
            if change == 'text': views[0]['parts'][0]['text'] += 'changed'
            elif change == 'parts': views[0]['parts'] = views[0]['parts'][1:]
            else: views[0]['snapshot_hash'] = 'changed'
            with self.subTest(change=change), self.assertRaises(ContractError):
                self.decode(views=views)

    def test_cross_fragment_reversed_and_non_integer_ranges_are_rejected(self):
        boundary = next(index for index in range(len(self.lines) - 1)
                        if self.lines[index]['fragment_index'] != self.lines[index + 1]['fragment_index'])
        for span in (dict(start=boundary, end=boundary + 1), dict(start=2, end=1),
                     dict(start=True, end=1), dict(start=0, end=200), dict(start=0, end=99999)):
            wire = copy.deepcopy(self.wire)
            wire['selections'][0]['text_span'] = span
            with self.subTest(span=span), self.assertRaises(ContractError): self.decode(wire)

    def test_missing_number_or_unselected_unit_is_not_repaired(self):
        for field, value in (('number_occurrence', 999), ('unit', '美元')):
            wire = copy.deepcopy(self.wire)
            wire['selections'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError): self.decode(wire)

    def test_literal_context_span_restores_full_basis_without_approving_value(self):
        wire = copy.deepcopy(self.wire)
        wire['selections'][0]['contexts'] = [dict(field='currency', value='CNY',
            span=self.item['unit_span'])]
        spec = self.decode(wire)['evidence_specs'][0]
        self.assertEqual(spec['calculation_context'], {'currency': 'CNY'})
        basis = spec['calculation_context_basis']['currency']
        self.assertEqual(basis['quote'], self.unit_line['text'].rstrip('\r\n'))
        self.assertEqual(basis['artifact_id'], self.views[0]['id'])
        self.assertEqual(basis['snapshot_hash'], self.views[0]['snapshot_hash'])

    def test_duplicate_contexts_ids_and_inconsistent_question_links_rejected(self):
        variants = []
        wire = copy.deepcopy(self.wire); wire['selections'] *= 2; variants.append(wire)
        wire = copy.deepcopy(self.wire)
        wire['selections'][0]['contexts'] = [dict(field='currency', value='CNY', span=self.item['unit_span'])] * 2
        variants.append(wire)
        wire = copy.deepcopy(self.wire); wire['selections'][0]['question_ids'] = ['q2']; variants.append(wire)
        for wire in variants:
            with self.assertRaises(ContractError): self.decode(wire)

    def test_no_silent_legacy_historical_or_mixed_source_upgrade(self):
        for changes in ({'original_span_selection_version': None}, {'research_mode': 'historical'}):
            self.assertIsNone(original_span_output_schema(dict(self.task, **changes), self.views))
        self.assertIsNone(original_span_output_schema(self.task, self.views + [dict(id='api', kind='api')]))
        self.assertIsNone(original_span_output_schema(self.task, self.views * 2))

    def test_existing_calculation_and_evidence_obligations_remain_in_prompt(self):
        self.assertEqual(self.prompt['evidence_contract']['calculation_input_requirements'],
                         self.payload['evidence_contract']['calculation_input_requirements'])
        self.assertEqual(self.prompt['evidence_contract']['question_requirements'],
                         self.payload['evidence_contract']['question_requirements'])

    def test_official_json_decoder_uses_exact_bound_protocol(self):
        value = structured_json_completion(json.dumps(self.wire), self.schema,
            protocol=ORIGINAL_SPAN_PROTOCOL, context=(self.task, self.views))
        self.assertEqual(json.loads(value), self.decode())

    def test_real_model_service_entry_selects_same_schema_without_external_call(self):
        with tempfile.TemporaryDirectory(prefix='span-model-') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/beta/chat/completions',
                model='deepseek-flash', api_key='FAKE_SPAN_KEY'))
            model = ModelService(settings, offline=False)
            with patch.object(model, 'reason', return_value=json.dumps(self.decode())) as transport:
                result, receipt = model_object(model, self.task, 'extract', self.payload, PHASE_FIELDS['extract'])
            self.assertEqual(result['evidence_specs'][0]['raw_token'], '9.1808')
            self.assertEqual(transport.call_args.kwargs['output_protocol'], ORIGINAL_SPAN_PROTOCOL)
            self.assertEqual(transport.call_args.kwargs['output_schema'], self.schema)
            self.assertEqual(receipt['original_span_selection_version'], 1)
            prompt = json.loads(transport.call_args.args[1][-1]['content'])
            self.assertIn('span_binding_ref', prompt)
            self.assertNotIn('raw_value', transport.call_args.kwargs['output_schema']['properties'])

    def strict_transport(self, responses=False, wire=None):
        """Mocked allowance on a unit copy; keep original acquisition receipts."""
        before_calls = self.task['ledger']['used']['model_calls']
        before_data = self.task['ledger']['used']['data_requests']
        self.task['ledger']['limits']['model_calls'] += 1
        with tempfile.TemporaryDirectory(prefix='span-transport-') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses' if responses else
                'https://api.deepseek.com/beta/chat/completions', model='deepseek-flash', api_key='FAKE_SPAN_KEY'))
            model = ModelService(settings, offline=False)
            body = json.dumps(wire or self.wire)
            if responses:
                reply = dict(object='response', status='completed', error=None, incomplete_details=None,
                    output=[dict(type='message', role='assistant', status='completed',
                        content=[dict(type='output_text', text=body)])], usage={})
            else:
                reply = dict(choices=[dict(finish_reason='tool_calls', message=dict(role='assistant', content=None,
                    tool_calls=[dict(id='synthetic_span_call', type='function', function=dict(
                        name='rh_select_original_spans_v1', arguments=body))]))], usage={})
            with patch.object(model, '_request_bytes', return_value=json.dumps(reply).encode()) as request:
                result, receipt = model_object(model, self.task, 'extract', self.payload, PHASE_FIELDS['extract'])
            request.assert_called_once()
            self.assertEqual(self.task['ledger']['used']['model_calls'], before_calls + 1)
            self.assertEqual(self.task['ledger']['used']['data_requests'], before_data)
            self.assertEqual(result['evidence_specs'][0]['raw_token'], '9.1808')
            self.assertEqual(receipt['output_protocol'], ORIGINAL_SPAN_PROTOCOL)
            self.assertEqual(receipt['original_span_selection_version'], 1)

    def test_beta_actual_request_and_decoder_are_consistent(self):
        self.strict_transport()

    def test_responses_actual_request_and_decoder_are_consistent(self):
        self.strict_transport(responses=True)

    def test_decoder_requires_explicit_reading_context(self):
        with self.assertRaises(ContractError):
            decode_original_spans(self.wire, self.schema, None)

    def test_original_without_its_acquisition_ledger_has_no_selector(self):
        task = dict(self.task, ledger=budget())
        source = evidence_contract(task, self.views)['sources'][0]
        self.assertNotIn('current_availability_reference', source)
        self.assertFalse(source['financial_amount_tables']['scan_complete'])
        self.assertIsNone(original_span_output_schema(task, self.views))

    def test_missing_receipt_on_another_disclosure_does_not_affect_the_valid_original(self):
        record = next(row for row in self.task['source_records'] if row['kind'] == 'filing')
        unverified = dict(record, id='missing_peer_receipt')
        unverified.pop('acquisition')
        self.task['source_records'].append(unverified)
        source = evidence_contract(self.task, self.views)['sources'][0]
        self.assertEqual(source['current_availability_reference']['artifact_id'], record['id'])
        self.assertTrue(source['financial_amount_tables']['scan_complete'])
        self.assertEqual(self.decode()['evidence_specs'][0]['raw_token'], '9.1808')

    def test_new_task_flags_and_frozen_report_preserve_both_source_policies(self):
        from researchhelper.archive import ResearchArchive
        from researchhelper.engine import ResearchAgent
        from researchhelper.reports import compose
        with tempfile.TemporaryDirectory(prefix='span-creation-') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            try:
                task = agent.create('解释ETF折溢价', entry='ETF', questions=[dict(id='q1', question='解释ETF折溢价',
                    kind='fact', answer_scope='general_definition', knowledge_needs=['rh_k_etf_premium_measures'])])
                self.assertEqual(task['original_span_selection_version'], 1)
                self.assertEqual(task['initial_acquisition_scope_version'], 1)
                done = agent.start(task['id'])
                self.assertEqual(done['status'], '已完成')
                content = compose(done)
                self.assertEqual(content['original_span_selection_version'], 1)
                self.assertEqual(content['initial_acquisition_scope_version'], 1)
                self.assertNotIn('original_span_selection_version', agent.create_case('mechanism'))
            finally:
                for worker in agent.close_runtime(): worker.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
