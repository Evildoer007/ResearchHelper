"""Retained market failure recovery text; no mode change or paid retry."""
import copy
import json
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, budget
from researchhelper.model import ModelService


class ExtractionMajorityDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.config = {'endpoint': 'https://api.deepseek.com/v1', 'model': 'deepseek-flash',
                       'api_key': 'OFFLINE_MAJORITY_FIXTURE'}
        self.record = {'finish_reason': 'length', 'max_output_tokens': 16384,
                       'usage': {'completion_tokens': 16384,
                                 'completion_tokens_details': {'reasoning_tokens': 15705}}}

    def message(self, config=None, phase='extract', record=None):
        return ModelService.truncated_response_message(config or self.config, phase, record or self.record)

    def test_verified_majority_reasoning_has_truthful_optional_recovery(self):
        value = self.message()
        self.assertIn('大部分输出额度用于模型思考', value)
        self.assertIn('直接提取', value)
        self.assertIn('不会自动重试', value)
        self.assertNotIn('全部用于', value)

    def test_partial_or_unverified_usage_does_not_claim_majority_exhaustion(self):
        for change in ({'finish_reason': 'stop'}, {'finish_reason': None}, {'max_output_tokens': 0}):
            self.assertNotIn('直接提取', self.message(record=dict(self.record, **change)))
        for reasoning in (None, True, '15705', 15705.0, -1, 8192, 16385):
            value = copy.deepcopy(self.record)
            value['usage']['completion_tokens_details']['reasoning_tokens'] = reasoning
            self.assertNotIn('直接提取', self.message(record=value))
        for completion in (None, True, '16384', 16383, 16385):
            value = copy.deepcopy(self.record)
            value['usage']['completion_tokens'] = completion
            self.assertNotIn('直接提取', self.message(record=value))

    def test_unrelated_phase_or_provider_never_recommends_mode_change(self):
        for phase in ('plan', 'read', 'analyze', 'supplement', None):
            self.assertNotIn('直接提取', self.message(phase=phase))
        for changes in ({'extraction_mode': 'direct'}, {'model': 'other'},
                        {'endpoint': 'https://proxy.invalid/v1'}):
            self.assertNotIn('直接提取', self.message(config=dict(self.config, **changes)))

    def test_majority_diagnostic_never_changes_request_or_spends_another_call(self):
        class Settings:
            def read(settings, private=False):
                return self.config
        task = {'batch': 1, 'ledger': budget()}
        response = json.dumps({'model': 'deepseek-flash', 'usage': self.record['usage'],
            'choices': [{'finish_reason': 'length', 'message': {
                'content': '{"evidence_specs":[', 'reasoning_content': 'PRIVATE_REASONING_NOT_STORED'}}]}).encode()
        service = ModelService(Settings(), offline=False)
        original = copy.deepcopy(self.config)
        with patch.object(service, '_request_bytes', return_value=response) as send:
            with self.assertRaisesRegex(ContractError, '大部分输出额度用于模型思考'):
                service.reason_phase(task, [], 'extract')
        self.assertEqual(send.call_count, 1)
        self.assertEqual(task['ledger']['used']['model_calls'], 1)
        self.assertEqual(task['ledger']['dispatches'][0]['failure_code'], 'content_truncated')
        self.assertEqual(self.config, original)
        self.assertNotIn('thinking', json.loads(send.call_args.args[1].data))
        self.assertNotIn('PRIVATE_REASONING_NOT_STORED', json.dumps(task))


if __name__ == '__main__':
    unittest.main()
