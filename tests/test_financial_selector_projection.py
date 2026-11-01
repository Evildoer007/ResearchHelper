"""The selector sees one wire grammar, while full local evidence gates remain."""
import copy
import hashlib
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests import ROOT
from tests.probe_etf_extraction_context import payload_for
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import canonical
from researchhelper.engine import ResearchAgent
from researchhelper.output_schema import (financial_reference_prompt, financial_reference_output_schema,
    original_batch_prompt, original_batch_output_schema, _financial_selector_projection)
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import evidence_contract, model_object, MAX_CONTEXT_CHARACTERS
from researchhelper.reports import compose

POLICY = 'financial_selector_projection_version'
RETAINED = ROOT / 'result/acceptance/one-sentence-research-k020e_gn/task.json'


class FinancialSelectorProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.digest = hashlib.sha256(RETAINED.read_bytes()).hexdigest()
        cls.old = json.loads(RETAINED.read_text())
        cls.task = copy.deepcopy(cls.old)
        cls.task[POLICY] = 1
        cls.task['input_hash'] = input_fingerprint(cls.task)
        cls.payload = payload_for(cls.task)
        cls.direct = financial_reference_prompt(cls.payload,cls.task)
        cls.batch = original_batch_prompt(cls.payload,cls.task)

    @classmethod
    def tearDownClass(cls):
        assert hashlib.sha256(RETAINED.read_bytes()).hexdigest() == cls.digest

    def test_only_alternative_format_instructions_are_removed_from_the_wire_prompt(self):
        for body in (self.direct,self.batch):
            contract = body['evidence_contract']
            for field in ('allowed_fields','api_candidate_contract','compact_api_value','api_row_groups',
                          'identity_fields_for_api','review_fields','review_types','publication_fields'):
                self.assertNotIn(field,contract)
            self.assertEqual(contract['selector_submission_contract']['version'],1)
            self.assertFalse(contract['selector_submission_contract']['automatic_approval'])
        # The authoritative local contract is not narrowed along with its UI.
        self.assertIn('allowed_fields',evidence_contract(self.task,self.payload['untrusted_sources']))

    def test_full_read_originals_questions_obligations_and_reading_coverage_are_preserved(self):
        for route,body in ((financial_reference_prompt,self.direct),(original_batch_prompt,self.batch)):
            # Disable only the display projection; keep the same task/input
            # fingerprint, source binding and ordinary route-specific fields.
            with patch('researchhelper.output_schema._financial_selector_projection',side_effect=lambda payload,task:payload):
                old = route(self.payload,self.task)
            for key in ('questions','calculations','source_reading','calculation_input_targets'):
                self.assertEqual(body[key],old[key],key)
            self.assertEqual(body['untrusted_sources'],old['untrusted_sources'])
            for key in ('knowledge_guidance','question_requirements','calculation_input_requirements',
                        'calculation_unit_rule','document_coverage_contract','sources','current_original_rule'):
                self.assertEqual(body['evidence_contract'][key],old['evidence_contract'][key],key)

    def test_binding_and_financial_candidates_stay_in_the_full_decoder_schema(self):
        schema = financial_reference_output_schema(self.task,self.payload['untrusted_sources'])
        self.assertIsNotNone(schema)
        for name in ('financial_cells','text_quotes'):
            fields = schema['properties'][name]['items']['properties']
            self.assertIn('observation_reference',fields)
            self.assertIn('review',fields)
            self.assertNotIn('value',fields)
        self.assertIn('financial_context_reference',schema['properties']['financial_cells']['items']['properties'])
        self.assertNotEqual(schema,financial_reference_output_schema(self.old,self.payload['untrusted_sources']))

    def test_batch_and_direct_routes_fit_the_original_capacity_without_trimming_originals(self):
        before = canonical(self.payload)
        self.assertLess(len(canonical(self.batch['evidence_contract'])),
                        len(canonical(original_batch_prompt(payload_for(self.old),self.old)['evidence_contract'])))
        schema = original_batch_output_schema(self.task,self.payload['untrusted_sources'])
        self.assertLessEqual(len(canonical(self.batch))+len(canonical(schema)),MAX_CONTEXT_CHARACTERS)
        self.assertEqual(canonical(self.payload),before)

    def test_nested_batch_projection_is_idempotent_and_preserves_each_source_binding(self):
        for body in (self.direct,self.batch):
            original = copy.deepcopy(body)
            self.assertEqual(_financial_selector_projection(copy.deepcopy(body),self.task),original)
            self.assertEqual(body,original)

    def test_final_gateway_has_no_competing_legacy_evidence_submission_commands(self):
        captured = []
        def capture(task,messages,views):
            captured.append(messages)
            system = messages[0]['content']
            body = json.loads(messages[-1]['content'])
            self.assertIn('selector_submission_contract',body['evidence_contract'])
            self.assertIn('Schema是唯一提交格式',system)
            for obsolete in ('record_locator另填','publication选择完整basis_ref','review含question_ids、allowed_natures',
                             'financial_value_reference选择来源financial_table_cells'):
                self.assertNotIn(obsolete,system)
            raise RuntimeError('selector_projection_capture_only')
        model = SimpleNamespace(supports_structured_analysis=lambda:True,supports_financial_references=lambda:True,
            reason_structured_extraction=capture,reason_structured_financial_references=capture,
            reason_structured_original_batch=capture)
        with self.assertRaisesRegex(RuntimeError,'selector_projection_capture_only'):
            model_object(model,self.task,'extract',self.payload,{'evidence_specs'})
        self.assertEqual(len(captured),1)

    def test_old_or_invalid_policy_keeps_all_legacy_contract_fields(self):
        for value in (None,True,False,0,2,1.0):
            task = copy.deepcopy(self.old)
            if value is not None: task[POLICY] = value
            task['input_hash'] = input_fingerprint(task)
            body = original_batch_prompt(payload_for(task),task)
            self.assertIn('allowed_fields',body['evidence_contract'])
            self.assertNotIn('selector_submission_contract',body['evidence_contract'])

    def test_formal_policy_is_input_bound_and_frozen(self):
        with tempfile.TemporaryDirectory(prefix='financial-selector-policy-') as folder:
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
