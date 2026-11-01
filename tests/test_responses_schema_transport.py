"""Explicit Responses schema transport shares the original research validators."""
import copy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.model import ModelService, ModelSettings
from researchhelper.output_schema import (READING_PROTOCOL, READING_FUNCTION_NAME,
    reading_output_schema, structured_completion, structured_json_completion)
from researchhelper.source_reader import catalog

ROOT = Path(__file__).resolve().parents[1]


class ResponsesSchemaTransportTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='responses-schema-')
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(Path(folder.name)/'store'), ROOT)
        self.task = self.agent.create('核对合成材料的收入口径', objects=['合成材料'])
        self.task = self.agent.add_material(self.task['id'], 'scope.txt',
            '发布日期：2026年7月19日。营业收入单位为元。'.encode())
        self.description = catalog(self.task)
        item = self.description['items'][0]
        self.request = dict(item['request_binding'], queries=['营业收入'],
            parts=[copy.deepcopy(item['reading_spans'][0])], reason='核对原件', question_ids=['q1'])
        self.wire = {'version': 1, 'requests': [self.request]}
        self.schema = reading_output_schema(self.task, self.description)
        self.settings = ModelSettings(Path(folder.name)/'model')
        self.config = dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash',
            api_key='FAKE_RESPONSES_KEY', reasoning_effort='low', extraction_mode='direct')
        self.settings.save(self.config)
        self.service = ModelService(self.settings, offline=False)
        network = patch.object(socket.socket, 'connect', side_effect=AssertionError('No external calls'))
        network.start(); self.addCleanup(network.stop)

    def payload(self):
        return dict(object='response', status='completed', model='deepseek-flash', error=None,
            incomplete_details=None, output=[dict(type='reasoning', content='PRIVATE_REASONING'),
                dict(type='message', role='assistant', status='completed',
                    content=[dict(type='output_text', text=json.dumps(self.wire))])],
            usage=dict(input_tokens=10, output_tokens=30, output_tokens_details=dict(reasoning_tokens=20)))

    def call(self, value, failure=None):
        with patch.object(self.service, '_request_bytes', return_value=json.dumps(value).encode()) as transport:
            if failure:
                with self.assertRaises(ContractError):
                    self.service.reason_structured_reading(self.task, [], self.description)
            else:
                result = json.loads(self.service.reason_structured_reading(self.task, [], self.description))
                self.assertEqual(result, {'requests': [self.request]})
        transport.assert_called_once()
        event = self.task['ledger']['dispatches'][-1]
        if failure:
            self.assertEqual(event['failure_code'], failure)
        self.assertNotIn('PRIVATE_', json.dumps(self.task))
        return event, transport.call_args.args[1]

    def test_explicit_endpoint_only_never_rewrites_chat_or_proxy(self):
        self.assertTrue(ModelSettings.uses_responses(self.config))
        for endpoint in ('https://api.deepseek.com/chat/completions',
                         'https://api.deepseek.com/beta/chat/completions',
                         'https://proxy.invalid/responses'):
            self.assertFalse(ModelSettings.uses_responses(dict(self.config, endpoint=endpoint)))
            self.assertEqual(ModelSettings.completion_endpoint(endpoint), endpoint)

    def test_low_reasoning_schema_request_has_no_optional_tools_or_stream(self):
        event, request = self.call(self.payload())
        body = json.loads(request.data)
        self.assertEqual(request.full_url, self.config['endpoint'])
        self.assertEqual(body['reasoning'], {'effort':'low'})
        self.assertEqual(body['text']['format']['type'], 'json_schema')
        self.assertEqual(body['text']['format']['name'], READING_FUNCTION_NAME)
        self.assertTrue(body['text']['format']['strict'])
        self.assertFalse(body['stream']); self.assertFalse(body['store'])
        self.assertNotIn('tools', body); self.assertNotIn('tool_choice', body)
        self.assertEqual(event['submission_transport'], 'responses_json_schema_v1')
        self.assertEqual(event['effective_reasoning_effort'], 'low')

    def test_common_phase_decoder_is_identical_for_declared_transports(self):
        text = json.dumps(self.wire)
        tool = dict(role='assistant', tool_calls=[dict(id='a', type='function',
            function=dict(name=READING_FUNCTION_NAME, arguments=text))])
        self.assertEqual(structured_json_completion(text, self.schema, protocol=READING_PROTOCOL),
            structured_completion(tool, 'tool_calls', self.schema, protocol=READING_PROTOCOL))

    def test_source_binding_is_not_relaxed_by_schema_transport(self):
        value = self.payload()
        bad = copy.deepcopy(self.wire); bad['requests'][0]['artifact_id'] = 'foreign-source'
        value['output'][1]['content'][0]['text'] = json.dumps(bad)
        self.call(value, 'structured_output_rejected')

    def test_plain_prose_cannot_replace_json(self):
        value = self.payload(); value['output'][1]['content'][0]['text'] = '读取材料即可'
        self.call(value, 'structured_output_rejected')

    def test_duplicate_keys_and_nonfinite_numbers_rejected_without_repair(self):
        for text in ('{"version":1,"version":1,"requests":[]}', '{"version":NaN,"requests":[]}'):
            with self.assertRaises(ContractError):
                structured_json_completion(text, self.schema, protocol=READING_PROTOCOL)

    def test_incomplete_even_with_valid_body_never_accepted(self):
        value=self.payload(); value['status']='incomplete'
        event,_=self.call(value, 'content_truncated')
        self.assertEqual(event['provider_completion_status'], 'incomplete')

    def test_failed_response_never_accepted(self):
        value=self.payload(); value['status']='failed'
        self.call(value, 'generation_interrupted')

    def test_missing_unknown_or_nonterminal_status_rejected(self):
        for status in (None, 'in_progress', 'PRIVATE_STATUS', True):
            value=self.payload(); value['status']=status
            self.call(value, 'completion_not_confirmed')

    def test_multiple_message_outputs_are_not_arbitrarily_selected(self):
        value=self.payload(); value['output'].append(copy.deepcopy(value['output'][1]))
        self.call(value, 'invalid_response_shape')

    def test_tool_or_unknown_output_is_not_silently_ignored(self):
        for kind in ('function_call', 'web_search_call', 'unknown'):
            value=self.payload(); value['output'].append(dict(type=kind))
            self.call(value, 'invalid_response_shape')

    def test_refusal_or_mixed_content_is_not_research(self):
        for content in ([dict(type='refusal', refusal='PRIVATE_REFUSAL')], [],
                        [dict(type='output_text', text='{}'), dict(type='output_text', text='{}')]):
            value=self.payload(); value['output'][1]['content']=content
            self.call(value, 'invalid_completion_message')

    def test_assistant_and_item_completion_required(self):
        for field,value in (('role','user'),('status','in_progress')):
            payload=self.payload(); payload['output'][1][field]=value
            self.call(payload, 'invalid_completion_message')

    def test_reasoning_not_retained_but_real_usage_is_retained(self):
        value=self.payload(); event,_=self.call(value)
        self.assertEqual(event['usage'],value['usage'])
        self.assertEqual(event['completion_metadata'],'responses_status')

    def test_error_and_incomplete_metadata_cannot_claim_completed(self):
        for field in ('error', 'incomplete_details'):
            value=self.payload(); value[field]={'message':'PRIVATE_DETAILS'}
            self.call(value, 'invalid_response_shape')

    def test_budget_and_journal_unchanged_on_failed_dispatch(self):
        snapshots=[]; self.service.journal=lambda task:snapshots.append(copy.deepcopy(task))
        self.call(dict(status='in_progress'), 'completion_not_confirmed')
        self.assertEqual(self.task['ledger']['used']['model_calls'],1)
        self.assertEqual(len(snapshots),2)
        self.assertEqual(snapshots[0]['ledger']['dispatches'][-1]['status'],'已派发')
        self.assertEqual(snapshots[-1]['ledger']['dispatches'][-1]['status'],'失败')

    def test_beta_tools_do_not_gain_json_fallback(self):
        self.settings.save(dict(self.config,endpoint='https://api.deepseek.com/beta/chat/completions'))
        value={'choices':[dict(index=0,finish_reason='stop',message=dict(role='assistant',content=json.dumps(self.wire)))]}
        event,request=self.call(value,'structured_output_rejected')
        self.assertNotIn('submission_transport',event)
        self.assertEqual(json.loads(request.data)['tool_choice'],'auto')


if __name__ == '__main__':
    unittest.main()
