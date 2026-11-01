"""Fixed format-rejection diagnostics without response text or private values."""
import json
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.output_schema import (object_schema, array_schema, schema_diagnostic,
    decode_planning_output, planning_output_schema, StructuredOutputError)
from researchhelper.contracts import budget
from researchhelper.model import ModelService, ModelSettings, ModelCompletionError


class SchemaDiagnosticTests(unittest.TestCase):
    def test_unknown_names_and_values_not_retained(self):
        schema = object_schema({'version': {'type': 'integer', 'enum': [1]}})
        value = {'private-key-name': 'PRIVATE_UNTRUSTED_VALUE', 'version': 'PRIVATE_ENUM_VALUE'}
        diagnostic = schema_diagnostic(value, schema)
        self.assertNotIn('PRIVATE', json.dumps(diagnostic))
        self.assertNotIn('private-key-name', json.dumps(diagnostic))
        self.assertEqual(diagnostic['schema_issues'], [{'path': '$', 'code': 'unknown_fields'},
                                                      {'path': '$.version', 'code': 'type_mismatch'}])

    def test_missing_registered_field_located_without_defaulting(self):
        value = {}
        diagnostic = schema_diagnostic(value, object_schema({'version': {'type': 'integer'}}))
        self.assertEqual(diagnostic['schema_issues'][0], {'path': '$.version', 'code': 'missing_field'})
        self.assertEqual(value, {})

    def test_union_selects_location_not_corrected_content(self):
        schema = {'anyOf': [object_schema({'mode': {'type': 'string', 'enum': ['a']}}),
                           object_schema({'mode': {'type': 'string', 'enum': ['b']}, 'extra': {'type': 'string'}})]}
        self.assertEqual(schema_diagnostic({'mode': 'PRIVATE'}, schema)['schema_issues'],
                         [{'path': '$.mode', 'code': 'enum_mismatch'}])

    def test_array_paths_are_wildcards_not_values_or_field_names(self):
        schema = object_schema({'items': array_schema(object_schema({'id': {'type': 'integer'}}))})
        diagnostic = schema_diagnostic({'items': [{'id': 'PRIVATE'}]}, schema)
        self.assertEqual(diagnostic['schema_issues'], [{'path': '$.items[*].id', 'code': 'type_mismatch'}])

    def test_issues_bounded(self):
        schema = object_schema({str(i): {'type': 'integer'} for i in range(40)})
        self.assertEqual(len(schema_diagnostic({}, schema)['schema_issues']), 8)

    def model(self):
        temporary = tempfile.TemporaryDirectory(prefix='rh-format-diagnostic-')
        self.addCleanup(temporary.cleanup)
        settings = ModelSettings(temporary.name)
        settings.save({'endpoint': 'https://api.deepseek.com/beta/chat/completions',
                       'model': 'deepseek-flash', 'api_key': 'FAKE_KEY', 'reasoning_effort': 'none'})
        task = {'id': 'fixture', 'version': 1, 'batch': 1, 'input_hash': 'fixed', 'ledger': budget(),
                'cutoff': '2026-10-14T00:00:00+08:00', 'research_mode': 'current',
                'question': '合成格式检查', 'intent': '解释变化', 'entry': 'ETF', 'objects': [],
                'questions': [], 'materials': [], 'source_records': []}
        return ModelService(settings, offline=False), task

    def call(self, arguments):
        model, task = self.model()
        response = {'choices': [{'index': 0, 'finish_reason': 'tool_calls', 'message': {
            'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'fixture', 'type': 'function',
            'function': {'name': 'rh_submit_plan_v1', 'arguments': arguments}}]}}]}
        with patch.object(model, '_request_bytes', return_value=json.dumps(response).encode()):
            with self.assertRaises(ModelCompletionError):
                model.reason_structured_plan(task, [])
        return task['ledger']['dispatches'][0]

    def test_actual_transport_retains_only_registered_failure_path(self):
        record = self.call(json.dumps({'version': 'PRIVATE', 'private-field': 'PRIVATE',
                                      'questions': [], 'data_requests': [], 'calculations': []}))
        self.assertEqual(record['structured_failure_category'], 'schema_mismatch')
        self.assertTrue(record['structured_output_diagnostic']['schema_issues'])
        self.assertNotIn('PRIVATE', json.dumps(record))
        self.assertNotIn('private-field', json.dumps(record))

    def test_actual_malformed_json_keeps_only_error_position(self):
        record = self.call('{"version":1 "private":"PRIVATE"}')
        self.assertEqual(record['structured_output_diagnostic']['json_error'], 'expected_comma')
        self.assertNotIn('PRIVATE', json.dumps(record))
        self.assertFalse(record['structured_output_diagnostic']['automatic_repair'])

    def test_ambiguity_does_not_store_duplicate_key_or_value(self):
        record = self.call('{"private":"PRIVATE","private":"PRIVATE"}')
        self.assertEqual(record['structured_failure_category'], 'invalid_argument_json')
        self.assertNotIn('PRIVATE', json.dumps(record))
        self.assertNotIn('private', json.dumps(record))


if __name__ == '__main__':
    unittest.main()
