"""Exercise the real structured-model gate for mixed fact/definition tasks."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import test_definition_branch_routing as base
from researchhelper.contracts import context
from researchhelper.model import ModelService, ModelSettings
from researchhelper.output_schema import (analysis_output_schema, decode_analysis_citations,
    ANALYSIS_CITATION_FUNCTION, StructuredOutputError)


class DefinitionAnalysisScopeTests(unittest.TestCase):
    def setUp(self):
        fixture = base.DefinitionBranchRoutingTests()
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        self.task = fixture.task()
        self.schema = analysis_output_schema(self.task)
        self.wire = dict(version=2, binding=context(self.task), answers=[dict(question_id='q1',
            segments=[dict(text='尚需核验价格资料。', refs=[], counter_refs=[])],
            knowledge_refs=[], provenance_refs=[])], theses=[])

    def test_model_gate_reconstruction_is_idempotent(self):
        selected = self.schema['properties']['answers']['items']['properties']['question_id']['enum']
        self.assertEqual(self.schema, analysis_output_schema(self.task, selected))

    def test_definitions_cannot_become_model_theses(self):
        selected = self.schema['properties']['theses']['items']['properties']['question_ids']['items']['enum']
        self.assertEqual(selected, ['q1'])

    def test_decoder_accepts_valid_mixed_scope_without_approving_the_fact(self):
        result = decode_analysis_citations(self.wire, self.schema, self.task)
        self.assertEqual(list(result['answers']), ['q1'])
        self.assertFalse(result['answers']['q1']['refs'])
        self.assertNotIn('q2', result['answers'])

    def test_definition_or_unknown_answer_is_still_rejected(self):
        for ident in ('q2', 'other_task'):
            wire = copy.deepcopy(self.wire)
            wire['answers'][0]['question_id'] = ident
            with self.assertRaises(StructuredOutputError):
                decode_analysis_citations(wire, self.schema, self.task)

    def test_actual_model_entry_passes_schema_gate_and_calls_only_mock_transport(self):
        with tempfile.TemporaryDirectory(prefix='definition-model-') as folder:
            settings = ModelSettings(Path(folder))
            settings.save(dict(endpoint='https://api.deepseek.com/beta/chat/completions',
                model='deepseek-flash', api_key='FAKE_OFFLINE_DEFINITION_SCOPE_KEY'))
            service = ModelService(settings, offline=False)
            reply = dict(choices=[dict(finish_reason='tool_calls', message=dict(role='assistant', content=None,
                tool_calls=[dict(id='scope_fixture', type='function', function=dict(
                    name=ANALYSIS_CITATION_FUNCTION, arguments=json.dumps(self.wire)))]))],
                usage=dict(prompt_tokens=10, completion_tokens=10, total_tokens=20))
            with patch.object(service, '_request_bytes', return_value=json.dumps(reply).encode()) as transport:
                result = json.loads(service.reason_structured_phase(self.task, [], 'analyze', self.schema))
            transport.assert_called_once()
            self.assertEqual(list(result['answers']), ['q1'])
            self.assertEqual(self.task['ledger']['used']['model_calls'], 1)

    def test_responses_transport_uses_the_same_mixed_scope(self):
        with tempfile.TemporaryDirectory(prefix='definition-responses-') as folder:
            settings = ModelSettings(Path(folder))
            settings.save(dict(endpoint='https://api.deepseek.com/responses',
                model='deepseek-flash', api_key='FAKE_OFFLINE_SCOPE_RESPONSES_KEY'))
            service = ModelService(settings, offline=False)
            reply = dict(object='response', status='completed', error=None, incomplete_details=None,
                output=[dict(type='message', role='assistant', status='completed',
                    content=[dict(type='output_text', text=json.dumps(self.wire))])], usage={})
            with patch.object(service, '_request_bytes', return_value=json.dumps(reply).encode()) as transport:
                result = json.loads(service.reason_structured_phase(self.task, [], 'analyze', self.schema))
            transport.assert_called_once()
            self.assertEqual(list(result['answers']), ['q1'])
            self.assertEqual(self.task['ledger']['used']['model_calls'], 1)
