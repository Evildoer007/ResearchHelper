"""Diagnose retained real failure without rerunning models or changing modes."""
import copy
import json
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, budget
from researchhelper.model import ModelService


class ExtractionExhaustionDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.config = {'endpoint': 'https://api.deepseek.com/chat/completions',
                       'model': 'deepseek-flash', 'api_key': 'OFFLINE_EXHAUSTION_FIXTURE'}
        self.record = {'max_output_tokens': 16384, 'usage': {
            'completion_tokens': 16384,
            'completion_tokens_details': {'reasoning_tokens': 16384}}}

    def message(self, config=None, phase='extract', record=None):
        return ModelService.truncated_response_message(
            self.config if config is None else config, phase,
            self.record if record is None else record)

    def test_verified_reasoning_only_exhaustion_has_actionable_opt_in_hint(self):
        self.assertIn('全部用于模型思考', self.message())
        self.assertIn('直接提取', self.message())
        self.assertIn('不会自动重试', self.message())

    def test_other_phases_cannot_recommend_disabling_reasoning(self):
        for phase in (None, 'plan', 'read', 'analyze', 'supplement'):
            self.assertNotIn('直接提取', self.message(phase=phase))

    def test_already_direct_and_unsupported_provider_keep_original_diagnostic(self):
        for changes in ({'extraction_mode': 'direct'}, {'model': 'other-model'},
                        {'endpoint': 'https://proxy.example.invalid/chat/completions'}):
            self.assertNotIn('直接提取', self.message(self.config | changes))

    def test_malformed_or_partial_usage_does_not_infer_reasoning_exhaustion(self):
        for value in (None, True, '16384', 16384.0, 16383, -1):
            record = copy.deepcopy(self.record)
            record['usage']['completion_tokens_details']['reasoning_tokens'] = value
            self.assertNotIn('直接提取', self.message(record=record))
        for record in ({}, {'usage': []}, {'usage': {'completion_tokens_details': []}}):
            self.assertNotIn('直接提取', self.message(record=record))

    def test_request_ledger_rejection_and_no_retry_are_preserved(self):
        class Settings:
            def read(settings, private=False):
                return self.config
        task = {'batch': 1, 'ledger': budget()}
        response = json.dumps({'model': 'deepseek-flash', 'usage': self.record['usage'],
            'choices': [{'finish_reason': 'length', 'message': {
                'content': '', 'reasoning_content': 'PRIVATE_REASONING_NOT_STORED'}}]}).encode()
        service = ModelService(Settings(), offline=False)
        before = copy.deepcopy(self.config)
        with patch.object(service, '_request_bytes', return_value=response) as send:
            with self.assertRaisesRegex(ContractError, '全部用于模型思考'):
                service.reason_phase(task, [], 'extract')
        self.assertEqual(send.call_count, 1)
        self.assertEqual(task['ledger']['used']['model_calls'], 1)
        self.assertEqual(task['ledger']['dispatches'][0]['failure_code'], 'content_truncated')
        self.assertEqual(self.config, before)
        self.assertNotIn('PRIVATE_REASONING_NOT_STORED', json.dumps(task))
        self.assertNotIn('thinking', json.loads(send.call_args.args[1].data))
