"""Synthetic selector faults on R119 originals, never a repaired real run."""
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import Mock, patch
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.output_schema import (original_batch_output_schema, original_batch_prompt,
    decode_original_batch, ORIGINAL_SELECTION_REJECTIONS, validated_original_selection_rejections)
from researchhelper.reasoning import evidence_contract, model_object, PHASE_FIELDS, expand_financial_value_references
from researchhelper.model import ModelSettings, ModelService
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import candidate_evidence
from researchhelper.analyzer import ResearchAnalyzer

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get('RESEARCHHELPER_FINANCIAL_BATCH_INPUT', str(
    ROOT/'result/acceptance/one-sentence-research-2l129r9v/task.json')))


class IndependentFinancialBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = SOURCE.read_bytes()
        cls.original = json.loads(cls.raw)

    def setUp(self):
        self.task = copy.deepcopy(self.original)
        self.task['independent_financial_selections_version'] = 1
        self.views = self.task['reasoning_state']['reading']['bundle']['views']
        self.payload = dict(question=self.task['question'], intent=self.task['intent'],
            objects=self.task['objects'], cutoff=self.task['cutoff'], questions=self.task['questions'],
            calculations=[], untrusted_sources=self.views,
            evidence_contract=evidence_contract(self.task, self.views), field_registry={})
        self.schema = original_batch_output_schema(self.task, self.views)
        self.prompt = original_batch_prompt(self.payload, self.task)
        self.source = self.payload['evidence_contract']['sources'][0]
        self.choice = self.prompt['source_choices'][0]
        self.financial_schema = self.schema['properties']['sources']['items']['anyOf'][0]['properties']['selection']
        properties = self.financial_schema['properties']['financial_cells']['items']['properties']
        self.observation = next(item['reference'] for item in self.prompt['untrusted_sources'][0]['observation_choices']
                                if item['date'] == '2026-06-30')
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No real I/O'))
        guard.start(); self.addCleanup(guard.stop)
        if os.environ.get('RESEARCHHELPER_FROZEN_TEST_EXECUTABLE'):
            from frozen_archive_test_loader import frozen_pdf_worker
            from researchhelper.material_parser import MaterialParser
            worker = patch.object(MaterialParser, 'worker_target', staticmethod(frozen_pdf_worker))
            worker.start(); self.addCleanup(worker.stop)

    def tearDown(self):
        self.assertEqual(SOURCE.read_bytes(), self.raw)

    def selection(self):
        cells = []
        for metric in ('revenue', 'net_profit', 'operating_cash_flow'):
            candidate = next(item for item in self.source['financial_context_cells']
                             if item['context']['metric'] == metric and '2026' in item['context']['period'])
            labels = dict(revenue='营业收入', net_profit='净利润', operating_cash_flow='经营活动产生的现金流量净额')
            question = next(q for q in self.task['questions'] if q['id'] == 'q1')
            need = next(text for text in question['evidence_needs']
                        if question['evidence_need_constraints'][text]['value_kind'] == 'number'
                        and labels[metric] in text)
            cells.append(dict(id='synthetic_'+metric, object=self.source['object_binding']['value'],
                nature='已实现事实', financial_context_reference=candidate['candidate_id'],
                shared_context_references={key: self.source['financial_shared_context_choices'][key][0]['candidate_id']
                                           for key in ('entity_id', 'accounting_standard')},
                observation_reference=self.observation,
                review=dict(scope='current_original_descriptive_only',
                            need_links={qid: ([fingerprint(need)] if qid == 'q1' else None)
                                        for qid in self.payload['evidence_contract']['question_ids']})))
        selected = dict(version=9, source_reference=copy.deepcopy(self.choice['binding']), current_version_only=True,
                        financial_cells=cells, text_quotes=[])
        return dict(version=1, binding_ref=self.prompt['batch_binding_ref'],
                    sources=[dict(source_ref=self.choice['source_ref'], selection=selected)])

    def bad_quote(self):
        source = self.prompt['untrusted_sources'][0]
        quote = source['parts'][0]['quote_choices'][0][0]
        links = {qid: None for qid in self.payload['evidence_contract']['question_ids']}
        links['q2'] = ['synthetic_unknown_need']
        return dict(id='synthetic_bad_quote', object=self.source['object_binding']['value'],
            nature='已实现事实', quote_reference=quote,
            period_reference=self.choice['reporting_period_choices'][0]['reference'],
            observation_reference=self.observation,
            review=dict(scope='current_original_descriptive_only', need_links=links))

    def wire(self):
        wire = self.selection()
        wire['sources'][0]['selection']['text_quotes'].append(self.bad_quote())
        return wire

    def decode(self, wire=None):
        return decode_original_batch(self.wire() if wire is None else wire, self.schema, (self.task, self.views))

    def test_unknown_local_need_does_not_erase_three_valid_financial_selections(self):
        wire = self.wire(); before = copy.deepcopy(wire)
        result = self.decode(wire)
        self.assertEqual({item['calculation_context']['metric'] for item in result['evidence_specs']},
                         {'revenue', 'net_profit', 'operating_cash_flow'})
        warning = result[ORIGINAL_SELECTION_REJECTIONS]
        self.assertEqual((warning['submitted_items'], warning['accepted_items']), (4, 3))
        self.assertEqual(warning['rejected_items'], [dict(
            location='$.sources[0].selection.text_quotes[0]', category='selector_schema_mismatch')])
        self.assertFalse(warning['evidence_approved'] or warning['coverage_approved'] or warning['automatic_repair'])
        self.assertNotIn('synthetic_unknown_need', json.dumps(result))
        self.assertEqual(wire, before)
        self.assertTrue(all('source_check' not in item for item in result['evidence_specs']))

    def test_valid_batch_retains_identical_decoded_values_and_no_diagnostic(self):
        wire = self.selection()
        old = copy.deepcopy(self.task); old.pop('independent_financial_selections_version')
        self.assertEqual(self.decode(wire), decode_original_batch(wire, self.schema, (old, self.views)))
        self.assertNotIn(ORIGINAL_SELECTION_REJECTIONS, self.decode(wire))

    def test_retained_valid_numbers_still_have_to_pass_actual_source_and_applicability_checks(self):
        analyzer = ResearchAnalyzer()
        specs = expand_financial_value_references(self.task, self.decode()['evidence_specs'], self.views)
        for spec in specs:
            source = next(record for record in self.task['source_records'] if record['id'] == spec['artifact_id'])
            item = analyzer.assess(self.task, candidate_evidence(self.task, source, spec), spec['review'],
                                   self.task['source_records'])
            self.assertEqual((item['source_check'], item['applicability_check']), ('通过', '通过'))
        self.assertEqual(self.task['status'], '部分完成')
        self.assertFalse(self.task['evidence'])

    def test_invalid_local_quote_reference_is_quarantined_without_repair(self):
        wire = self.wire(); item = wire['sources'][0]['selection']['text_quotes'][0]
        item['quote_reference'] = 'synthetic_unknown_quote'
        item['review']['need_links'] = {qid: [] for qid in item['review']['need_links']}
        self.assertEqual(len(self.decode(wire)['evidence_specs']), 3)

    def test_changed_binding_authority_object_or_duplicate_identifiers_fail_the_whole_batch(self):
        for issue in ('binding', 'source', 'source_binding', 'duplicate_id', 'approval', 'object',
                      'unknown_question', 'unknown_top_field', 'non_scalar_id'):
            wire = self.wire(); selected = wire['sources'][0]['selection']
            if issue == 'binding': wire['binding_ref'] = 'foreign'
            elif issue == 'source': wire['sources'][0]['source_ref'] = 'foreign'
            elif issue == 'source_binding': selected['source_reference']['binding_ref'] = 'foreign'
            elif issue == 'duplicate_id': selected['text_quotes'][0]['id'] = selected['financial_cells'][0]['id']
            elif issue == 'approval': selected['text_quotes'][0]['source_check'] = '通过'
            elif issue == 'object': selected['text_quotes'][0]['object'] = 'foreign'
            elif issue == 'unknown_question': selected['text_quotes'][0]['review']['need_links']['foreign'] = []
            elif issue == 'non_scalar_id': selected['financial_cells'][0]['id'] = []
            else: wire['anything'] = True
            with self.subTest(issue=issue), self.assertRaises(ContractError): self.decode(wire)

    def test_old_failure_and_invalid_policy_are_not_silently_upgraded(self):
        for value in (None, 0, 2, True, '1'):
            task = copy.deepcopy(self.task)
            if value is None: task.pop('independent_financial_selections_version')
            else: task['independent_financial_selections_version'] = value
            with self.subTest(policy=value), self.assertRaises(ContractError):
                decode_original_batch(self.wire(), self.schema, (task, self.views))

    def test_diagnostic_remains_bound_to_schema_count_location_and_no_approval(self):
        original = self.decode()[ORIGINAL_SELECTION_REJECTIONS]
        self.assertEqual(validated_original_selection_rejections(original, self.schema), original)
        for field, value in (('schema_hash', 'foreign'), ('accepted_items', 4), ('evidence_approved', True)):
            warning = copy.deepcopy(original); warning[field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                validated_original_selection_rejections(warning, self.schema)

    def test_real_model_transport_wrapper_keeps_one_charge_and_separate_diagnostic(self):
        response = dict(object='response', status='completed', error=None, incomplete_details=None,
            output=[dict(type='message', role='assistant', status='completed',
                         content=[dict(type='output_text', text=json.dumps(self.wire()))])], usage={})
        before = copy.deepcopy(self.task['ledger'])
        with tempfile.TemporaryDirectory(prefix='financial-batch-wrapper-', dir=ROOT/'result/acceptance') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash',
                               api_key='SYNTHETIC_OFFLINE_BATCH_KEY'))
            service = ModelService(settings, offline=False)
            with patch.object(service, '_request_bytes', return_value=json.dumps(response).encode()) as transport:
                result, receipt = model_object(service, self.task, 'extract', self.payload, PHASE_FIELDS['extract'])
            transport.assert_called_once()
        self.assertEqual(len(result['evidence_specs']), 3)
        self.assertEqual(receipt['output_protocol'], 'strict_original_batch_v1')
        self.assertEqual(receipt['original_selection_rejections']['accepted_items'], 3)
        self.assertEqual(self.task['ledger']['used']['model_calls'], before['used']['model_calls']+1)
        self.assertEqual(self.task['ledger']['used']['data_requests'], before['used']['data_requests'])
        agent = ResearchAgent.__new__(ResearchAgent)
        agent._save_model_checkpoint = Mock()
        agent._retain_original_selection_diagnostics(self.task, receipt, 'synthetic_input_digest')
        self.assertTrue(self.task['reasoning_state']['original_selection_rejections'])
        self.assertFalse(self.task.get('evidence'))


if __name__ == '__main__':
    unittest.main()
