"""Only documented official models may replace the legacy character gate."""
import copy
import json
import unittest
import tempfile
from unittest.mock import patch

from tests import ROOT
from researchhelper.contracts import ContractError, canonical, budget
from researchhelper.model import ModelService, ModelSettings
from researchhelper.reasoning import validate_model_context_capacity, ModelContextCapacityError
from researchhelper.output_schema import original_span_output_schema, original_span_prompt, ORIGINAL_SPAN_PROTOCOL
from tests.probe_operating_extraction_capacity import payload


class ProviderContextBudgetTests(unittest.TestCase):
    def config(self, **changes):
        return dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash', **changes)

    def test_official_profile_reserves_output_and_safety_inside_documented_context(self):
        profile = ModelService.documented_input_budget(self.config())
        self.assertEqual(profile['context_tokens'], 1000000)
        self.assertEqual(profile['input_byte_limit'] + profile['output_token_reserve']
                         + profile['safety_token_reserve'], profile['context_tokens'])
        self.assertGreaterEqual(profile['output_token_reserve'], 65536)

    def test_proxy_unknown_model_http_and_unregistered_route_do_not_inherit_capacity(self):
        for config in (dict(endpoint='https://proxy.example/responses', model='deepseek-flash'),
                       dict(endpoint='http://api.deepseek.com/responses', model='deepseek-flash'),
                       dict(endpoint='https://api.deepseek.com.evil.example/responses', model='deepseek-flash'),
                       dict(endpoint='https://api.deepseek.com/responses', model='unknown'),
                       dict(endpoint='https://api.deepseek.com:444/responses', model='deepseek-flash'),
                       dict(endpoint='https://api.deepseek.com/custom', model='deepseek-flash')):
            with self.subTest(config=config):
                self.assertIsNone(ModelService.documented_input_budget(config))

    def test_real_failed_wire_can_fit_without_changing_data_schema_or_call_allowance(self):
        source = ROOT / 'result/acceptance/autonomous-data-phase-hc9zpqn3/task.json'
        task = json.loads(source.read_text())
        before = copy.deepcopy(task)
        inputs = payload(task)
        shown = original_span_prompt(inputs, task)
        schema = original_span_output_schema(task, inputs['untrusted_sources'])
        with self.assertRaises(ModelContextCapacityError):
            validate_model_context_capacity(task, canonical(shown), schema, ORIGINAL_SPAN_PROTOCOL)
        profile = ModelService.documented_input_budget(self.config())
        validate_model_context_capacity(task, canonical(shown), schema, ORIGINAL_SPAN_PROTOCOL,
                                        input_budget=profile, system='研究规则保持不变。')
        self.assertEqual(task, before)

    def test_full_system_and_utf8_bytes_count_not_only_character_length(self):
        profile = ModelService.documented_input_budget(self.config())
        with self.assertRaises(ModelContextCapacityError):
            validate_model_context_capacity({}, '中' * 300000, input_budget=profile)
        with self.assertRaises(ModelContextCapacityError):
            validate_model_context_capacity({}, '正文', input_budget=profile,
                                            system='A' * profile['input_byte_limit'])

    def test_unknown_budget_shape_cannot_silently_raise_the_limit(self):
        profile = ModelService.documented_input_budget(self.config())
        for key, value in (('input_byte_limit', 1000000000), ('context_tokens', True), ('version', 99)):
            changed = dict(profile, **{key: value})
            with self.subTest(key=key), self.assertRaises(ContractError):
                validate_model_context_capacity({}, '正文', input_budget=changed)


class FullRequestContextBudgetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='rh-context-profile-')
        self.addCleanup(temporary.cleanup)
        self.settings = ModelSettings(temporary.name)
        self.settings.save(dict(endpoint='https://api.deepseek.com/chat/completions',
                                model='deepseek-flash', api_key='SYNTHETIC_CONTEXT_KEY'))
        self.service = ModelService(self.settings, offline=False)
        self.task = dict(id='context_fixture', version=1, batch=1, ledger=budget(), model_context_budget_version=1)
        guard = patch('socket.socket.connect', side_effect=AssertionError('Offline only'))
        guard.start();self.addCleanup(guard.stop)

    def test_exact_serialized_request_is_counted_without_changing_the_message(self):
        captured = []
        def response(task, request, started, timeout, record):
            captured.append(request.data)
            return json.dumps(dict(choices=[dict(finish_reason='stop', message=dict(content='{}'))])).encode()
        text = 'A' * 200000
        with patch.object(self.service, '_request_bytes', side_effect=response):
            self.assertEqual(self.service.reason(self.task, [dict(role='user', content=text)], phase='extract'), '{}')
        self.assertEqual(json.loads(captured[0])['messages'][0]['content'], text)
        event = self.task['ledger']['dispatches'][-1]
        self.assertEqual(event['input_budget']['request_bytes'], len(captured[0]))
        self.assertEqual(self.task['ledger']['used']['model_calls'], 1)
        self.assertNotIn('SYNTHETIC_CONTEXT_KEY', json.dumps(self.task))

    def test_final_wire_escape_overhead_is_checked_before_charging_or_dispatch(self):
        before = copy.deepcopy(self.task['ledger'])
        with patch.object(self.service, '_request_bytes') as request:
            with self.assertRaisesRegex(ContractError, '完整模型请求'):
                self.service.reason(self.task, [dict(role='user', content='中' * 150000)], phase='extract')
        request.assert_not_called()
        self.assertEqual(self.task['ledger'], before)

    def test_unknown_service_does_not_gain_large_request_authority(self):
        self.settings.save(dict(endpoint='https://proxy.example/chat/completions',
                                model='deepseek-flash', api_key='SYNTHETIC_CONTEXT_KEY'))
        before = copy.deepcopy(self.task['ledger'])
        with patch.object(self.service, '_request_bytes') as request:
            with self.assertRaisesRegex(ContractError, '保守上限'):
                self.service.reason(self.task, [dict(role='user', content='A' * 200000)], phase='extract')
        request.assert_not_called()
        self.assertEqual(self.task['ledger'], before)


if __name__ == '__main__':
    unittest.main()
