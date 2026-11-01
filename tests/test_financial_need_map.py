"""Explicit financial need maps preserve original evidence and scope gates."""
import copy
import json
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT, test_financial_text_period_reference as fixtures
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, canonical
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import candidate_evidence
from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.financial_sources import expand_financial_value_references
from researchhelper.model import ModelService, ModelSettings
from researchhelper.output_schema import (financial_reference_output_schema, financial_reference_prompt,
    decode_financial_references, original_batch_output_schema, decode_original_batch, structured_json_completion)
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import evidence_contract, model_object, MAX_CONTEXT_CHARACTERS
from researchhelper.reports import compose

POLICY = 'financial_need_map_version'


class FinancialNeedMapTests(unittest.TestCase):
    def setUp(self):
        self.base = fixtures.FinancialTextPeriodReferenceTests()
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.task = copy.deepcopy(self.base.task)
        self.task[POLICY] = 1
        self.task['input_hash'] = input_fingerprint(self.task)
        self.views = self.base.views
        self.schema, self.wire = self.selection(self.task)

    def selection(self, task):
        schema = financial_reference_output_schema(task, self.views)
        original_source = evidence_contract(self.base.task, self.views)['sources'][0]
        current_source = evidence_contract(task, self.views)['sources'][0]
        prompt = financial_reference_prompt(dict(untrusted_sources=self.views,
            evidence_contract=evidence_contract(task, self.views)), task)
        wire = copy.deepcopy(self.base.wire)
        wire['source_reference'] = prompt['financial_reference_binding']
        questions = schema['properties']['financial_cells']['items']['properties'][
            'review']['properties']['need_links']['properties']
        for name in ('financial_cells', 'text_quotes'):
            for item in wire[name]:
                if name == 'financial_cells':
                    original = next(cell for cell in original_source['financial_context_cells']
                        if cell['candidate_id'] == item['financial_context_reference'])
                    item['financial_context_reference'] = next(cell['candidate_id'] for cell
                        in current_source['financial_context_cells']
                        if cell['context'] == original['context'] and cell['bases'] == original['bases'])
                    for field, reference in item['shared_context_references'].items():
                        old = next(choice for choice in original_source['financial_shared_context_choices'][field]
                            if choice['candidate_id'] == reference)
                        item['shared_context_references'][field] = next(choice['candidate_id'] for choice
                            in current_source['financial_shared_context_choices'][field]
                            if choice['value'] == old['value'] and choice['basis'] == old['basis'])
                item['review'] = copy.deepcopy(item['review'])
                pairs = item['review']['need_links']
                item['review']['need_links'] = {qid: next((pair['need_ids'] for pair in pairs
                    if pair['question_id'] == qid), []) for qid in questions}
        return schema, wire

    def decode(self, wire=None):
        return decode_financial_references(self.wire if wire is None else wire,
                                           self.schema, (self.task, self.views))

    def test_selected_needs_roundtrip_without_changing_original_business_proposals(self):
        current = expand_financial_value_references(self.task, self.decode()['evidence_specs'], self.views)
        previous = expand_financial_value_references(self.base.task, self.base.decode()['evidence_specs'], self.views)
        self.assertEqual(current, previous)
        mapping = self.schema['properties']['financial_cells']['items']['properties'][
            'review']['properties']['need_links']
        self.assertEqual(mapping['type'], 'object')
        self.assertTrue(all(value['uniqueItems'] for value in mapping['properties'].values()))
        self.assertNotIn('source_check', self.decode()['evidence_specs'][0])

    def test_duplicate_unknown_or_legacy_pairs_are_rejected_not_merged(self):
        for issue in ('duplicate', 'unknown_need', 'unknown_question', 'missing_question', 'legacy'):
            wire = copy.deepcopy(self.wire)
            links = wire['financial_cells'][0]['review']['need_links']
            qid = next(iter(links))
            if issue == 'duplicate': links[qid] *= 2
            elif issue == 'unknown_need': links[qid] = ['unknown']
            elif issue == 'unknown_question': links['foreign'] = []
            elif issue == 'missing_question': links.pop(qid)
            else: wire['financial_cells'][0]['review']['need_links'] = self.base.wire['financial_cells'][0]['review']['need_links']
            with self.subTest(issue=issue), self.assertRaises(ContractError): self.decode(wire)

    def test_explicit_empty_selection_adds_no_need_coverage(self):
        wire = copy.deepcopy(self.wire)
        for name in ('financial_cells', 'text_quotes'):
            for item in wire[name]:
                item['review']['need_links'] = dict.fromkeys(item['review']['need_links'], [])
        result = self.decode(wire)
        self.assertTrue(result['evidence_specs'])
        self.assertTrue(all('evidence_need_texts' not in item['review'] for item in result['evidence_specs']))

    def test_repeated_question_keys_are_rejected_before_json_object_decoding(self):
        text = json.dumps(self.wire)
        first = next(iter(self.wire['financial_cells'][0]['review']['need_links']))
        marker = '"need_links": {' + json.dumps(first) + ':'
        duplicate = '"need_links": {' + json.dumps(first) + ': [], ' + json.dumps(first) + ':'
        self.assertIn(marker, text)
        with self.assertRaisesRegex(ContractError, '重复键'):
            structured_json_completion(text.replace(marker, duplicate, 1), self.schema,
                protocol='strict_financial_reference_v1', context=(self.task, self.views))

    def test_multi_question_links_cannot_borrow_another_questions_need_or_owner(self):
        task = copy.deepcopy(self.task)
        question = copy.deepcopy(task['questions'][0])
        question.update(id='q2', question='核对另一项独立文字需求', evidence_needs=['本问题独立的原件需求'])
        task['questions'].append(question)
        task['input_hash'] = input_fingerprint(task)
        schema, wire = self.selection(task)
        self.assertIn('q2', wire['financial_cells'][0]['review']['need_links'])
        item = wire['financial_cells'][0]
        q2_need = schema['properties']['financial_cells']['items']['properties']['review'][
            'properties']['need_links']['properties']['q2']['items']['enum'][0]
        item['review']['need_links']['q2'] = [q2_need]
        with self.assertRaisesRegex(ContractError, '超出本摘录适用问题'):
            decode_financial_references(wire, schema, (task, self.views))
        item['review']['need_links']['q2'] = []
        item['review']['need_links']['q1'] = [q2_need]
        with self.assertRaises(ContractError): decode_financial_references(wire, schema, (task, self.views))

    def test_actual_financial_source_and_applicability_checks_are_still_required(self):
        specs = expand_financial_value_references(self.task, self.decode()['evidence_specs'], self.views)
        record = next(row for row in self.task['source_records'] if row['kind'] == 'filing')
        for spec in specs:
            evidence = candidate_evidence(self.task, record, spec)
            self.assertEqual(evidence['source_check'], '通过')
            assessed = ResearchAnalyzer().assess(self.task, evidence, spec['review'], self.task['source_records'])
            self.assertEqual(assessed['applicability_check'], '通过', assessed.get('rejection'))
        wire = copy.deepcopy(self.wire)
        wire['financial_cells'][0]['value'] = 1
        with self.assertRaises(ContractError): self.decode(wire)

    def test_batch_uses_the_same_map_and_canonical_decoder(self):
        task = copy.deepcopy(self.task)
        task['financial_quote_fallback_version'] = 1
        task['input_hash'] = input_fingerprint(task)
        _, selected = self.selection(task)
        schema = original_batch_output_schema(task, self.views)
        wire = dict(version=1, binding_ref=schema['properties']['binding_ref']['enum'][0],
                    sources=[dict(source_ref='source_1', selection=selected)])
        expected = decode_financial_references(selected, financial_reference_output_schema(task, self.views), (task, self.views))
        self.assertEqual(decode_original_batch(wire, schema, (task, self.views)), expected)

    def test_old_and_invalid_flags_do_not_gain_a_new_wire_shape(self):
        expected = financial_reference_output_schema(self.base.task, self.views)
        for value in (None, True, False, 0, 2, 1.0):
            task = copy.deepcopy(self.base.task)
            if value is not None: task[POLICY] = value
            task['input_hash'] = input_fingerprint(task)
            schema = financial_reference_output_schema(task, self.views)
            links = schema['properties']['financial_cells']['items']['properties']['review']['properties']['need_links']
            self.assertEqual(links, expected['properties']['financial_cells']['items']['properties']['review']['properties']['need_links'])
            with self.assertRaises(ContractError): decode_financial_references(self.wire, self.schema, (task, self.views))

    def test_real_model_adapter_sends_the_fixed_map_and_decodes_once_offline(self):
        response = dict(object='response', status='completed', error=None, incomplete_details=None,
            output=[dict(type='message', role='assistant', status='completed',
                content=[dict(type='output_text', text=json.dumps(self.wire))])], usage={})
        payload = dict(untrusted_sources=self.views, evidence_contract=evidence_contract(self.task, self.views))
        self.task['ledger']['used']['model_calls'] = 0
        with tempfile.TemporaryDirectory(prefix='financial-need-map-') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash',
                               api_key='LOCAL_MAP_TEST', extraction_mode='direct'))
            with patch.object(ModelService, '_request_bytes', return_value=json.dumps(response).encode()) as call:
                result, receipt = model_object(ModelService(settings, offline=False), self.task,
                                                'extract', payload, {'evidence_specs'})
            self.assertEqual(call.call_count, 1)
            body = json.loads(call.call_args.args[1].data)
            shape = body['text']['format']['schema']
            links = shape['properties']['financial_cells']['items']['properties']['review']['properties']['need_links']
            self.assertEqual(links['type'], 'object')
        expected = expand_financial_value_references(self.task, self.decode()['evidence_specs'], self.views)
        self.assertEqual(result, {'evidence_specs': expected})
        self.assertLessEqual(len(canonical(body)), MAX_CONTEXT_CHARACTERS)

    def test_formal_version_is_input_bound_and_report_frozen(self):
        with tempfile.TemporaryDirectory(prefix='financial-map-policy-') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            try:
                task = agent.create('解释ETF折溢价', entry='ETF', questions=[dict(id='q1',kind='fact',
                    question='解释ETF折溢价',answer_scope='general_definition',knowledge_needs=['rh_k_etf_premium_measures'])])
                self.assertEqual(task[POLICY], 1)
                old = copy.deepcopy(task); old.pop(POLICY)
                self.assertNotEqual(input_fingerprint(old), task['input_hash'])
                self.assertEqual(compose(agent.start(task['id']))[POLICY], 1)
                self.assertNotIn(POLICY, agent.create_case('mechanism'))
            finally: agent.close_runtime()
