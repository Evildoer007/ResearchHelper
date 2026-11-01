"""No model replay: retain both closed-study originals and size the real wire."""
import copy
import hashlib
import json
from types import SimpleNamespace
import unittest

from tests import ROOT
from tests.probe_etf_extraction_context import payload_for
from researchhelper.contracts import canonical, ContractError
from researchhelper.output_schema import original_batch_prompt, original_batch_output_schema, provider_request_schema, ORIGINAL_BATCH_PROTOCOL
from researchhelper.reasoning import model_object, validate_model_context_capacity, MAX_CONTEXT_CHARACTERS

RETAINED = ROOT/'result/acceptance/one-sentence-research-9c9b25qu/task.json'


class FinalWireContextCapacityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = RETAINED.read_bytes()
        cls.original_hash = hashlib.sha256(raw).hexdigest()
        cls.task = json.loads(raw)
        cls.payload = payload_for(cls.task)
        cls.presented = original_batch_prompt(cls.payload, cls.task)
        cls.schema = original_batch_output_schema(cls.task, cls.payload['untrusted_sources'])

    @classmethod
    def tearDownClass(cls):
        assert hashlib.sha256(RETAINED.read_bytes()).hexdigest() == cls.original_hash

    def test_raw_internal_contract_is_large_but_lossless_final_wire_fits(self):
        self.assertGreater(len(canonical(self.payload)), MAX_CONTEXT_CHARACTERS)
        wire = provider_request_schema(self.task, self.schema, ORIGINAL_BATCH_PROTOCOL)
        self.assertLessEqual(len(canonical(self.presented))+len(canonical(wire)), MAX_CONTEXT_CHARACTERS)
        self.assertEqual(len(self.payload['untrusted_sources']), 2)
        self.assertEqual(self.presented['source_reading'], self.payload['source_reading'])
        validate_model_context_capacity(self.task, canonical(self.presented), self.schema, ORIGINAL_BATCH_PROTOCOL)

    def model(self, captured):
        def capture(task, messages, views):
            captured.append((messages, views))
            # JSON object keys are strings on the wire, including the parser's
            # numeric column indexes. Compare the exact serialized protocol.
            self.assertEqual(messages[-1]['content'], canonical(self.presented))
            self.assertEqual(views, self.payload['untrusted_sources'])
            raise RuntimeError('final_wire_capture_only')
        return SimpleNamespace(supports_structured_analysis=lambda: True, supports_financial_references=lambda: True,
            reason_structured_extraction=capture, reason_structured_financial_references=capture,
            reason_structured_original_batch=capture)

    def test_real_gateway_reaches_both_originals_without_changing_task_or_limits(self):
        captured = []
        before = canonical(self.task), canonical(self.payload)
        with self.assertRaisesRegex(RuntimeError, 'final_wire_capture_only'):
            model_object(self.model(captured), self.task, 'extract', self.payload, {'evidence_specs'})
        self.assertEqual(len(captured), 1)
        self.assertEqual((canonical(self.task), canonical(self.payload)), before)
        self.assertEqual(self.task['ledger']['limits'], dict(data_requests=100, model_calls=8, seconds=600))

    def test_actual_oversized_wire_is_rejected_before_any_dispatch(self):
        captured = []
        payload = copy.deepcopy(self.payload)
        payload['source_reading']['limitations'].append('容量测试'*MAX_CONTEXT_CHARACTERS)
        with self.assertRaisesRegex(ContractError, '上下文上限'):
            model_object(self.model(captured), self.task, 'extract', payload, {'evidence_specs'})
        self.assertEqual(captured, [])

    def test_ordinary_body_exact_boundary_and_overflow(self):
        validate_model_context_capacity({}, 'x'*MAX_CONTEXT_CHARACTERS)
        with self.assertRaises(ContractError):
            validate_model_context_capacity({}, 'x'*(MAX_CONTEXT_CHARACTERS+1))

    def test_real_provider_schema_is_counted_at_exact_boundary(self):
        schema = dict(type='object', properties=dict(value=dict(type='string')), required=['value'], additionalProperties=False)
        size = len(canonical(provider_request_schema({}, schema, 'capacity_fixture')))
        validate_model_context_capacity({}, 'x'*(MAX_CONTEXT_CHARACTERS-size), schema, 'capacity_fixture')
        with self.assertRaises(ContractError):
            validate_model_context_capacity({}, 'x'*(MAX_CONTEXT_CHARACTERS-size+1), schema, 'capacity_fixture')
