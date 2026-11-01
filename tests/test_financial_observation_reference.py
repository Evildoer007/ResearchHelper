"""Financial observation addresses preserve the original date and exact span."""
import copy
import hashlib
import json
import tempfile
from types import SimpleNamespace
import unittest

from tests import ROOT
from tests.probe_etf_extraction_context import payload_for
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.financial_sources import original_date_anchors
from researchhelper.output_schema import (financial_reference_output_schema, financial_reference_prompt,
    decode_financial_references, original_batch_output_schema, original_batch_prompt, decode_original_batch)
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import evidence_contract, model_object
from researchhelper.reports import compose

POLICY = 'financial_observation_reference_version'
RETAINED = ROOT / 'result/acceptance/one-sentence-research-dpney28c/task.json'


class FinancialObservationReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.digest = hashlib.sha256(RETAINED.read_bytes()).hexdigest()
        cls.old = json.loads(RETAINED.read_text())
        cls.task = copy.deepcopy(cls.old)
        cls.task[POLICY] = 1
        cls.task['input_hash'] = input_fingerprint(cls.task)
        cls.views = cls.task['reasoning_state']['reading']['bundle']['views']
        cls.schema = financial_reference_output_schema(cls.task, cls.views)

    @classmethod
    def tearDownClass(cls):
        assert hashlib.sha256(RETAINED.read_bytes()).hexdigest() == cls.digest

    def selection(self, task=None):
        task = task or self.task
        contract = evidence_contract(task, self.views)
        source = contract['sources'][0]
        schema = financial_reference_output_schema(task, self.views)
        prompt = financial_reference_prompt(dict(untrusted_sources=self.views, evidence_contract=contract), task)
        date_index = next(index for index,date in enumerate(original_date_anchors(self.views[0]))
                          if date['date']=='2026-06-30')
        common = dict(object=source['object_binding']['value'], nature=contract['natures'][0],
            review=dict(scope='current_original_descriptive_only', need_links={qid:
                [contract['question_requirements'][qid][0]['id']] if qid=='q1' else None
                for qid in contract['question_ids']}))
        if task.get(POLICY) == 1:
            common['observation_reference'] = prompt['untrusted_sources'][0]['observation_choices'][date_index]['reference']
        else: common['observation_anchor'] = date_index
        number = dict(copy.deepcopy(common), id='observed_number',
            financial_context_reference=source['financial_context_cells'][0]['candidate_id'],
            shared_context_references={field:source['financial_shared_context_choices'][field][0]['candidate_id']
                                       for field in ('entity_id','accounting_standard')})
        quote = dict(copy.deepcopy(common), id='observed_quote', period_reference='observation_date',
                     quote_reference=prompt['untrusted_sources'][0]['parts'][0]['quote_choices'][0][0])
        return schema, dict(version=schema['properties']['version']['enum'][0],
            source_reference=prompt['financial_reference_binding'], current_version_only=True,
            financial_cells=[number], text_quotes=[quote]), prompt

    def decode(self, wire=None):
        if wire is None: _, wire, _ = self.selection()
        return decode_financial_references(wire, self.schema, (self.task,self.views))

    def test_both_numeric_and_text_items_choose_bound_string_addresses(self):
        _, wire, shown = self.selection()
        for name in ('financial_cells','text_quotes'):
            fields = self.schema['properties'][name]['items']['properties']
            self.assertNotIn('observation_anchor', fields)
            self.assertEqual(fields['observation_reference']['type'], 'string')
        view = shown['untrusted_sources'][0]
        self.assertNotIn('observation_anchors', view)
        self.assertEqual([{k:v for k,v in row.items() if k!='reference'} for row in view['observation_choices']],
                         original_date_anchors(self.views[0]))
        self.assertTrue(all(row['reference'].startswith('observation_') for row in view['observation_choices']))
        self.assertEqual({item['observation_date'] for item in self.decode(wire)['evidence_specs']}, {'2026-06-30'})

    def test_explicit_reference_preserves_exact_old_observation_and_original_position(self):
        old_schema, old_wire, _ = self.selection(self.old)
        old = decode_financial_references(old_wire, old_schema, (self.old,self.views))
        for current, previous in zip(self.decode()['evidence_specs'], old['evidence_specs']):
            for field in ('id','observation_date','record_locator','period','publication','availability','review'):
                self.assertEqual(current.get(field), previous.get(field), field)
            self.assertNotIn('source_check', current)

    def test_wrong_type_raw_date_unknown_address_and_redundant_index_are_rejected(self):
        for value in (0, None, '2026-06-30', 'observation_999999'):
            _, wire, _ = self.selection()
            wire['text_quotes'][0]['observation_reference'] = value
            with self.subTest(value=value), self.assertRaises(ContractError): self.decode(wire)
        _, wire, _ = self.selection()
        wire['text_quotes'][0]['observation_anchor'] = 0
        with self.assertRaises(ContractError): self.decode(wire)

    def test_batch_uses_each_financial_sources_date_choices_and_same_decoder(self):
        schema, selected, shown = self.selection()
        batch = original_batch_output_schema(self.task,self.views)
        wire = dict(version=1, binding_ref=batch['properties']['binding_ref']['enum'][0],
                    sources=[dict(source_ref='source_1',selection=selected)])
        self.assertEqual(decode_original_batch(wire,batch,(self.task,self.views)), self.decode(selected))
        payload = payload_for(self.task)
        projected = original_batch_prompt(payload,self.task)
        self.assertEqual(projected['untrusted_sources'][0]['observation_choices'],
                         shown['untrusted_sources'][0]['observation_choices'])

    def test_legacy_and_invalid_policy_keep_the_original_integer_contract(self):
        for value in (None, True, False, 0, 2, 1.0):
            task = copy.deepcopy(self.old)
            if value is not None: task[POLICY] = value
            task['input_hash'] = input_fingerprint(task)
            schema = financial_reference_output_schema(task,self.views)
            fields = schema['properties']['text_quotes']['items']['properties']
            self.assertIn('observation_anchor',fields)
            self.assertNotIn('observation_reference',fields)
        with self.assertRaises(ContractError):
            decode_financial_references(self.selection()[1], self.schema, (self.old,self.views))

    def test_actual_extraction_gateway_receives_the_new_bound_date_contract(self):
        captured = []
        def capture(task,messages,views):
            captured.append(messages)
            body = json.loads(messages[-1]['content'])
            self.assertEqual(body['questions'], payload['questions'])
            self.assertIn('observation_choices',body['untrusted_sources'][0])
            self.assertNotIn('observation_anchors',body['untrusted_sources'][0])
            self.assertIn('observation_reference', messages[0]['content'])
            raise RuntimeError('date_gateway_capture_only')
        model = SimpleNamespace(supports_structured_analysis=lambda:True, supports_financial_references=lambda:True,
            reason_structured_extraction=capture, reason_structured_financial_references=capture,
            reason_structured_original_batch=capture)
        payload = payload_for(self.task)
        with self.assertRaisesRegex(RuntimeError,'date_gateway_capture_only'):
            model_object(model,self.task,'extract',payload,{'evidence_specs'})
        self.assertEqual(len(captured),1)

    def test_formal_policy_is_input_bound_and_report_frozen(self):
        with tempfile.TemporaryDirectory(prefix='financial-observation-policy-') as folder:
            agent = ResearchAgent(ResearchArchive(folder),ROOT,offline=True,require_argument_support=True)
            try:
                task = agent.create('解释ETF折溢价',entry='ETF',questions=[dict(id='q1',kind='fact',
                    question='解释ETF折溢价',answer_scope='general_definition',knowledge_needs=['rh_k_etf_premium_measures'])])
                self.assertEqual(task[POLICY],1)
                old = copy.deepcopy(task); old.pop(POLICY)
                self.assertNotEqual(input_fingerprint(old),task['input_hash'])
                self.assertEqual(compose(agent.start(task['id']))[POLICY],1)
                self.assertNotIn(POLICY,agent.create_case('mechanism'))
            finally: agent.close_runtime()
