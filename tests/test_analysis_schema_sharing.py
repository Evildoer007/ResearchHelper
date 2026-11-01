"""Lossless schema sharing for the actual retained 31-evidence analysis.

No network or model is used. The failed study remains immutable; only detached
copies exercise the same payload and the existing exact-reference factoring.
"""
import copy
import hashlib
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests import ROOT, test_shared_schema_transport as shared
from researchhelper.contracts import ContractError, canonical
from researchhelper.engine import ResearchAgent
from researchhelper.archive import ResearchArchive
from researchhelper.reports import compose
from researchhelper.output_schema import analysis_output_schema, analysis_protocol, provider_output_schema, provider_request_schema
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import model_object, analysis_fields, MAX_CONTEXT_CHARACTERS

POLICY = 'analysis_schema_sharing_version'
RETAINED = ROOT / 'result/acceptance/one-sentence-research-nnrx4a9v/task.json'


class AnalysisSchemaSharingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_hash = hashlib.sha256(RETAINED.read_bytes()).hexdigest()
        cls.task = json.loads(RETAINED.read_text())
        agent = ResearchAgent.__new__(ResearchAgent)
        agent.model = None
        payloads = []
        def capture(model, task, phase, payload, fields, **kwargs):
            payloads.append(payload)
            raise RuntimeError('payload_capture_only')
        with patch('researchhelper.engine.model_object', side_effect=capture):
            try:
                agent._reason_on_verified_material(cls.task)
            except RuntimeError as error:
                assert str(error) == 'payload_capture_only'
        cls.payload = payloads[0]
        # Only the detached transport probe opts in after the original payload
        # has passed its unchanged provenance validation. Never reapprove old
        # evidence under a different task hash or submit this copy to a model.
        cls.task[POLICY] = 1
        cls.schema = analysis_output_schema(cls.task)
        cls.protocol = analysis_protocol(cls.task)[0]

    @classmethod
    def tearDownClass(cls):
        assert hashlib.sha256(RETAINED.read_bytes()).hexdigest() == cls.original_hash

    def test_all_constraints_expand_exactly_and_input_is_not_mutated(self):
        before = canonical(self.schema)
        compact = provider_request_schema(self.task, self.schema, self.protocol)
        self.assertIn('$defs', compact)
        self.assertEqual(shared.expanded(compact), provider_output_schema(self.schema))
        self.assertEqual(canonical(self.schema), before)

    def test_actual_complete_material_and_contract_fit_without_trimming_or_more_budget(self):
        body = canonical(self.payload)
        compact = provider_request_schema(self.task, self.schema, self.protocol)
        self.assertGreater(len(body) + len(canonical(self.schema)), MAX_CONTEXT_CHARACTERS)
        self.assertLessEqual(len(body) + len(canonical(compact)), MAX_CONTEXT_CHARACTERS)
        self.assertEqual(len(self.payload['evidence']), 31)
        self.assertEqual(len(self.payload['questions']), 3)
        self.assertEqual(len(self.payload['calculations']), 2)
        self.assertEqual(self.task['ledger']['limits'], dict(data_requests=100, model_calls=8, seconds=600))

    def test_real_preflight_reaches_gateway_with_canonical_schema_and_full_material(self):
        before = canonical(self.payload)
        calls = []
        def capture(task, messages, phase, schema):
            calls.append(messages)
            self.assertEqual(phase, 'analyze')
            self.assertEqual(schema, self.schema)
            body = json.loads(messages[-1]['content'])
            self.assertEqual([item['id'] for item in body['evidence']], [item['id'] for item in self.payload['evidence']])
            self.assertEqual(body['questions'], self.payload['questions'])
            raise RuntimeError('gateway_capture_only')
        model = SimpleNamespace(supports_structured_analysis=lambda: True, reason_structured_phase=capture)
        with self.assertRaisesRegex(RuntimeError, 'gateway_capture_only'):
            model_object(model, self.task, 'analyze', copy.deepcopy(self.payload), analysis_fields(self.task))
        self.assertEqual(len(calls), 1)
        self.assertEqual(canonical(self.payload), before)

    def test_legacy_invalid_policy_and_other_protocols_keep_their_original_encoding(self):
        expected = provider_output_schema(self.schema)
        for value in (None, True, False, 0, 2, 1.0):
            task = dict(self.task, **{POLICY:value})
            self.assertEqual(provider_request_schema(task, self.schema, self.protocol), expected)
        self.assertEqual(provider_request_schema(self.task, self.schema, 'strict_plan_v2'), expected)

    def test_shared_graph_is_valid_and_deterministic(self):
        from jsonschema import Draft202012Validator
        first = provider_request_schema(self.task, self.schema, self.protocol)
        Draft202012Validator.check_schema(first)
        self.assertEqual(first, provider_request_schema(self.task, copy.deepcopy(self.schema), self.protocol))

    def test_material_still_over_the_limit_is_not_sent(self):
        payload = copy.deepcopy(self.payload)
        payload['gaps'].append('x' * MAX_CONTEXT_CHARACTERS)
        gateway = Mock()
        model = SimpleNamespace(supports_structured_analysis=lambda: True, reason_structured_phase=gateway)
        with self.assertRaises(ContractError):
            model_object(model, self.task, 'analyze', payload, analysis_fields(self.task))
        gateway.assert_not_called()

    def test_legacy_preflight_retains_its_original_expanded_limit(self):
        task = copy.deepcopy(self.task)
        task.pop(POLICY)
        gateway = Mock()
        model = SimpleNamespace(supports_structured_analysis=lambda: True, reason_structured_phase=gateway)
        with self.assertRaises(ContractError):
            model_object(model, task, 'analyze', copy.deepcopy(self.payload), analysis_fields(task))
        gateway.assert_not_called()

    def test_policy_changes_the_input_dependency(self):
        old = copy.deepcopy(self.task)
        old.pop(POLICY)
        self.assertNotEqual(input_fingerprint(old), input_fingerprint(self.task))

    def test_new_formal_tasks_freeze_the_policy_without_reusing_retained_evidence(self):
        with tempfile.TemporaryDirectory(prefix='analysis-sharing-policy-') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            try:
                task = agent.create('解释ETF折溢价', entry='ETF', questions=[dict(id='q1', kind='fact',
                    question='解释ETF折溢价', answer_scope='general_definition', knowledge_needs=['rh_k_etf_premium_measures'])])
                self.assertEqual(task[POLICY], 1)
                old = copy.deepcopy(task)
                old.pop(POLICY)
                self.assertNotEqual(input_fingerprint(old), task['input_hash'])
                complete = agent.start(task['id'])
                self.assertEqual(compose(complete)[POLICY], 1)
                self.assertEqual(complete['ledger']['used']['model_calls'], 0)
            finally:
                agent.close_runtime()
