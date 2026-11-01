"""Container observations never expose, reinterpret or approve model content."""
import json
import unittest

from tests.probe_financial_response_container import container_diagnostic


class FinancialResponseContainerDiagnosticTests(unittest.TestCase):
    def observe(self, text):
        payload = dict(output=[dict(type='message', content=[dict(type='output_text', text=text)])])
        return container_diagnostic(json.dumps(payload).encode())

    def test_json_object_is_only_a_shape_not_schema_or_financial_approval(self):
        result = self.observe('{"private":"DO_NOT_EXPOSE_THIS"}')
        self.assertEqual(result['kind'], 'json_object_start')
        self.assertFalse(result['body_retained'])
        self.assertNotIn('DO_NOT_EXPOSE_THIS', json.dumps(result))
        self.assertNotIn('approved', result)

    def test_complete_explicit_json_fence_is_distinct_from_other_fences(self):
        self.assertEqual(self.observe('```json\n{}\n```')['kind'], 'single_json_fence')
        self.assertEqual(self.observe('```python\nDO_NOT_EXPOSE_THIS\n```')['kind'], 'other_fenced_start')
        self.assertEqual(self.observe('```json\n{}')['kind'], 'other_fenced_start')

    def test_non_json_and_empty_text_are_not_repaired_or_retained(self):
        self.assertEqual(self.observe('DO_NOT_EXPOSE_THIS')['kind'], 'non_json_start')
        self.assertEqual(self.observe('   ')['kind'], 'empty_text')
        self.assertNotIn('DO_NOT_EXPOSE_THIS', json.dumps(self.observe('DO_NOT_EXPOSE_THIS')))

    def test_ambiguous_or_malformed_envelopes_reveal_no_values(self):
        self.assertEqual(container_diagnostic(b'invalid')['kind'], 'unrecognized_completion_envelope')
        value = dict(output=[dict(type='message', content=[
            dict(type='output_text', text='DO_NOT_EXPOSE_THIS'), dict(type='output_text', text='{}')])])
        result = container_diagnostic(json.dumps(value).encode())
        self.assertEqual(result['kind'], 'unrecognized_completion_envelope')
        self.assertNotIn('DO_NOT_EXPOSE_THIS', json.dumps(result))
