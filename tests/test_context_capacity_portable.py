"""Portable capacity/routing checks, with no retained study or financial verdict.

These synthetic protocol projections exercise dispatch order only. They do not
replace the separate original-preservation and live supplier acceptance gates.
"""
import copy
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import canonical, ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.reasoning import model_object, validate_model_context_capacity, MAX_CONTEXT_CHARACTERS


class PortableContextCapacityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='rh-portable-capacity-')
        self.addCleanup(temporary.cleanup)
        agent = ResearchAgent(ResearchArchive(temporary.name), ROOT, offline=True, require_argument_support=True)
        self.addCleanup(agent.close_runtime)
        self.task = agent.create('提取两份材料的实际内容，不作投资判断。')
        self.sources = [dict(id='synthetic-a', kind='filing', text='第一份完整合成材料。'),
                        dict(id='synthetic-b', kind='filing', text='第二份完整合成材料。')]
        self.payload = dict(question=self.task['question'], intent=self.task['intent'],
            untrusted_sources=copy.deepcopy(self.sources), legacy_grammar='x'*(MAX_CONTEXT_CHARACTERS+1))
        self.presented = dict(question=self.task['question'], intent=self.task['intent'],
            untrusted_sources=copy.deepcopy(self.sources), synthetic_protocol_fixture=True)
        self.schema = dict(type='object', properties=dict(version=dict(type='integer', enum=[1])),
            required=['version'], additionalProperties=False)
        self.dispatches = []
        def capture(task, messages, views):
            self.dispatches.append((messages, views))
            raise RuntimeError('portable_protocol_capture_only')
        self.model = SimpleNamespace(supports_structured_analysis=lambda: True,
            supports_financial_references=lambda: True, reason_structured_extraction=capture,
            reason_structured_original_batch=capture)

    def invoke(self):
        # Substitute protocol builders, not capacity limits or the actual
        # dispatch/measurement implementation being tested.
        with patch.multiple('researchhelper.output_schema',
                _general_original_selection=lambda task: False,
                financial_extraction_output_schema=lambda *args: None,
                material_extraction_output_schema=lambda *args: None,
                api_extraction_output_schema=lambda *args: None,
                original_batch_output_schema=lambda *args: copy.deepcopy(self.schema),
                original_batch_prompt=lambda *args: copy.deepcopy(self.presented)):
            return model_object(self.model, self.task, 'extract', self.payload, {'evidence_specs'})

    def test_projection_precedes_capacity_and_preserves_every_fixture_original(self):
        before = canonical(self.task), canonical(self.payload)
        self.assertGreater(len(canonical(self.payload)), MAX_CONTEXT_CHARACTERS)
        with self.assertRaisesRegex(RuntimeError, 'portable_protocol_capture_only'):
            self.invoke()
        self.assertEqual(len(self.dispatches), 1)
        messages, views = self.dispatches[0]
        self.assertEqual(messages[-1]['content'], canonical(self.presented))
        self.assertEqual(views, self.sources)
        self.assertEqual((canonical(self.task), canonical(self.payload)), before)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 0)

    def test_actual_oversize_never_reaches_dispatch(self):
        self.presented['necessary_material'] = 'x'*(MAX_CONTEXT_CHARACTERS+1)
        with self.assertRaisesRegex(ContractError, '上下文上限'):
            self.invoke()
        self.assertEqual(self.dispatches, [])
        self.assertEqual(self.task['ledger']['used']['model_calls'], 0)

    def test_body_boundary_without_a_structured_protocol(self):
        validate_model_context_capacity({}, 'x'*MAX_CONTEXT_CHARACTERS)
        with self.assertRaises(ContractError):
            validate_model_context_capacity({}, 'x'*(MAX_CONTEXT_CHARACTERS+1))

    def test_actual_protocol_counts_towards_the_unchanged_limit(self):
        with self.assertRaises(ContractError):
            validate_model_context_capacity(self.task, 'x'*MAX_CONTEXT_CHARACTERS, self.schema, 'synthetic')

    def test_missing_binding_schema_remains_a_pre_dispatch_error(self):
        self.model.reason_structured_calculation_binding = lambda *args: self.fail('Unexpected dispatch')
        with patch('researchhelper.output_schema.deferred_calculation_output_schema', return_value=None):
            with self.assertRaises(ContractError):
                model_object(self.model, self.task, 'bind_calculations', {}, {'calculations', 'reason'})
        self.assertEqual(self.dispatches, [])
