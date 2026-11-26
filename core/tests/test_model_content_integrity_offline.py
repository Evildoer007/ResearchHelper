"""Pristine model text, privacy rejection and non-content parser diagnostics."""

import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, budget
from researchhelper.engine import ResearchAgent
from researchhelper.model import IsolatedModelService, ModelCompletionError, ModelService, ModelSettings
from researchhelper.planning import question_specs
from tests import test_autonomous_reasoning_offline as fixtures

ROOT = Path(__file__).resolve().parents[2]


class ModelContentIntegrityTests(unittest.TestCase):
    def setUp(self):
        out = ROOT / 'result/acceptance/model-content-integrity'
        out.mkdir(parents=True, exist_ok=True)
        folder = tempfile.TemporaryDirectory(prefix='isolated-', dir=out)
        self.addCleanup(folder.cleanup)
        self.settings = ModelSettings(folder.name)
        self.secret = 'FABRICATED_CONTENT_INTEGRITY_KEY'
        self.settings.save({'endpoint': 'https://example.invalid/model', 'model': 'offline-fixture', 'api_key': self.secret})
        self.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=False)
        self.agent.model = ModelService(self.settings, offline=False, journal=self.agent._journal)
        network = patch('socket.socket.connect', side_effect=AssertionError('External network forbidden'))
        network.start()
        self.addCleanup(network.stop)

    @staticmethod
    def response(content):
        return json.dumps({'model': 'offline-fixture', 'choices': [{'finish_reason': 'stop',
            'message': {'content': content, 'reasoning_content': 'PRIVATE_REASONING_NOT_RETAINED'}}],
            'usage': {'total_tokens': 13}}).encode()

    def raw_call(self, content, isolated=False):
        task = {'id': 'content-fixture', 'batch': 1, 'ledger': budget()}
        service = (IsolatedModelService if isolated else ModelService)(self.settings, offline=False)
        with patch.object(service, '_request_bytes', return_value=self.response(content)) as request:
            result = service.reason_phase(task, [], 'extract')
        request.assert_called_once()
        return result, task

    def start(self, content):
        task = self.agent.create('核对原件条款', questions=[{'id': 'q1', 'question': '核对原件条款', 'kind': 'fact'}])
        with patch('researchhelper.model.urllib.request.urlopen', return_value=io.BytesIO(self.response(content))) as request:
            done = self.agent.start(task['id'])
        request.assert_called_once()
        return done, done['ledger']['dispatches'][0]

    def test_public_json_is_preserved_byte_for_byte_not_reserialized(self):
        content = '{\n  "answer": "利率为3.0%，引用\\\"公开条款\\\"，网址https://example.invalid/a?date=2025-05-20"\n}\n'
        json.loads(content)
        for isolated in (False, True):
            with self.subTest(isolated=isolated):
                answer, task = self.raw_call(content, isolated)
                self.assertEqual(answer, content)
                self.assertEqual(task['ledger']['used']['model_calls'], 1)
                self.assertEqual(task['ledger']['dispatches'][0]['status'], '成功')

    def test_private_url_with_escaped_quote_is_rejected_not_redacted(self):
        content = json.dumps({'answer': 'https://example.invalid/a?token=fabricated"PRIVATE_TAIL'}, ensure_ascii=False)
        json.loads(content)
        for isolated in (False, True):
            task = {'id': 'privacy-fixture', 'batch': 1, 'ledger': budget()}
            service = (IsolatedModelService if isolated else ModelService)(self.settings, offline=False)
            with patch.object(service, '_request_bytes', return_value=self.response(content)) as request:
                with self.assertRaises(ContractError) as error:
                    service.reason_phase(task, [], 'extract')
            request.assert_called_once()
            self.assertEqual(task['ledger']['dispatches'][0]['failure_code'], 'private_content')
            self.assertEqual(task['ledger']['dispatches'][0]['status'], '失败')
            self.assertEqual(task['ledger']['used']['model_calls'], 1)
            self.assertNotIn('PRIVATE_TAIL', json.dumps(task) + str(error.exception))

    def test_configured_secret_is_never_forwarded_or_archived(self):
        done, event = self.start(json.dumps({'questions': [], 'data_requests': [], 'calculations': [], 'echo': self.secret}))
        self.assertEqual(done['status'], '失败')
        self.assertEqual(event['failure_code'], 'private_content')
        self.assertNotIn(self.secret, json.dumps(done))
        self.assertNotIn('output_contract', event)
        self.assertEqual(event['usage']['total_tokens'], 13)
        self.assertNotIn('PRIVATE_REASONING_NOT_RETAINED', json.dumps(done))

    def test_nested_private_fields_remain_rejected_not_silently_removed(self):
        task = self.agent.create('核对条款')
        plan = {'questions': question_specs(task), 'data_requests': [], 'calculations': []}
        plan['questions'][0]['email'] = 'PRIVATE_EMAIL@example.invalid'
        with patch('researchhelper.model.urllib.request.urlopen', return_value=io.BytesIO(self.response(json.dumps(plan)))):
            done = self.agent.start(task['id'])
        event = done['ledger']['dispatches'][0]
        self.assertEqual(event['status'], '成功')
        self.assertEqual(event['output_contract']['failure_code'], 'private_content')
        self.assertNotIn('PRIVATE_EMAIL', json.dumps(done))

    def test_private_provider_echo_rejects_without_changing_original_needs(self):
        task = self.agent.create('核对原件条款')
        original = copy.deepcopy(task['questions'])
        plan = {'questions': question_specs(task), 'data_requests': [], 'calculations': []}
        plan['questions'][0]['evidence_needs'] = [self.secret]
        with patch('researchhelper.model.urllib.request.urlopen', return_value=io.BytesIO(self.response(json.dumps(plan)))) as request:
            done = self.agent.start(task['id'])
        request.assert_called_once()
        self.assertEqual(done['status'], '失败')
        self.assertEqual(done['questions'], original)
        self.assertEqual(done['ledger']['used']['model_calls'], 1)
        self.assertEqual(done['ledger']['dispatches'][0]['failure_code'], 'private_content')
        self.assertFalse(done['data_need_specs'] or done['evidence'] or done['theses'])
        self.assertNotIn(self.secret, json.dumps(done))
        self.assertNotIn('已移除敏感值', json.dumps(done))

    def test_private_fields_and_escaped_credentials_are_blocked_before_draft_archiving(self):
        encoded = ''.join(r'\u%04x' % ord(char) for char in self.secret)
        for body in ('{"conclusion":"' + encoded + '"}',
                     '~~~\n{"conclusion":"https://example.invalid/r\\u003ftoken=CONTROLLED_PRIVATE"}\n~~~',
                     '{"conclusion":"public","email":"CONTROLLED_PRIVATE@example.invalid"}'):
            task = {'id': 'escaped-private', 'batch': 1, 'ledger': budget(), 'draft_first_version': 1}
            service = ModelService(self.settings, offline=False)
            with self.subTest(size=len(body)), patch.object(service, '_request_bytes', return_value=self.response(body)) as dispatch:
                with self.assertRaises(ModelCompletionError):
                    service.reason(task, [], delivery=True, phase='draft_report')
            dispatch.assert_called_once()
            event = task['ledger']['dispatches'][0]
            self.assertEqual((event['status'], event['failure_code']), ('失败', 'private_content'))
            self.assertNotIn(self.secret, json.dumps(task))
            self.assertNotIn('CONTROLLED_PRIVATE', json.dumps(task))

    def test_duplicate_draft_fields_reject_before_archiving(self):
        service = ModelService(self.settings, offline=False)
        task = {'id': 'duplicate-draft', 'batch': 1, 'ledger': budget(), 'draft_first_version': 1}
        body = '```json\n{"conclusion":"PRIVATE_FIRST","conclusion":"PRIVATE_LAST"}\n```'
        with patch.object(service, '_request_bytes', return_value=self.response(body)) as dispatch:
            with self.assertRaises(ModelCompletionError):
                service.reason(task, [], delivery=True, phase='draft_report')
        dispatch.assert_called_once()
        event = task['ledger']['dispatches'][-1]
        self.assertEqual((event['status'], event['failure_code']), ('失败', 'duplicate_response_field'))
        self.assertNotIn('PRIVATE_', json.dumps(task))

    def test_rejected_analysis_keeps_the_verified_excerpt_without_reusing_the_reply(self):
        quote = '政策自2026年7月21日起生效。'
        expected_field = {
            'raw_private': 'analysis.private_content',
            'nested_private': 'analysis.private_content',
            'ambiguous_output': 'analysis.completion',
            'invalid_top_level': 'analysis.completion',
        }
        replies = {
            'raw_private': '{"answers":{},"theses":[],"discard":"https://example.invalid/r?token=PRIVATE_RAW_REPLY"}',
            'nested_private': '{"answers":{"q1":{"email":"PRIVATE_NESTED_REPLY@example.invalid"}},"theses":[]}',
            'ambiguous_output': '{"answers":{"q1":{"answer":"PRIVATE_FIRST_VALUE"}},"answers":{},"theses":[]}',
            'invalid_top_level': '["PRIVATE_LIST_VALUE"]',
        }
        for name, response in replies.items():
            with self.subTest(case=name):
                task = self.agent.create('这份政策什么时候生效？', objects=['材料示例政策'],
                    cutoff=fixtures.CUTOFF, limits={'model_calls': 3})
                task = self.agent.add_material(task['id'], 'policy.txt',
                    ('材料示例政策\n\n发布日期：2026年7月19日。\n\n' + quote).encode())
                model = fixtures.FabricatedModel([fixtures.AutonomousReasoningTests.plan(task),
                    {'evidence_specs': [fixtures.AutonomousReasoningTests.policy_spec(task)]}, response])
                with patch('researchhelper.model.urllib.request.urlopen', side_effect=model.respond) as dispatch:
                    result = self.agent.start(task['id'])
                saved = self.agent.archive.get(task['id'])
                self.assertEqual(saved['status'], '部分完成', result.get('error'))
                self.assertEqual(saved['ledger']['used']['model_calls'], dispatch.call_count)
                self.assertEqual(dispatch.call_count, 3)
                self.assertEqual(saved['ledger']['used']['data_requests'], 0)
                self.assertEqual(saved['reasoning_state']['final_delivery']['status'], '未采用')
                self.assertEqual(saved['reasoning_state']['analysis']['numeric_review']['invalid_fields'], [expected_field[name]])
                self.assertEqual(saved['questions'][0]['status'], '未回答')
                self.assertEqual(len(saved['evidence']), 1)
                self.assertFalse(saved['theses'] or saved['tracking'])
                report = self.agent.export(task['id'])
                self.assertIn(quote, report['html'])
                self.assertEqual(report['content']['evidence'], saved['evidence'])
                self.assertNotIn('PRIVATE_', json.dumps(saved) + report['html'])

    def test_json_syntax_categories_use_only_fixed_names_and_positions(self):
        cases = {
            'extra_data': '{"questions":[],"data_requests":[],"calculations":[]} PRIVATE_SUFFIX',
            'unterminated_string': '{"x":"PRIVATE_UNCLOSED}',
            'invalid_escape': '{"x":"\\q"}',
            'invalid_unicode_escape': '{"x":"\\uZZZZ"}',
            'expected_property_name': "{'PRIVATE_NAME':1}",
            'expected_comma': '{"x":1 "PRIVATE_NAME":2}',
            'expected_colon': '{"PRIVATE_NAME" 1}',
            'expected_value': '{"x":}',
            'invalid_control_character': '{"x":"\n"}',
            'unexpected_bom': '\ufeff{}',
        }
        for expected, content in cases.items():
            with self.subTest(category=expected):
                done, event = self.start(content)
                diagnostic = event['output_contract']
                self.assertEqual(diagnostic['failure_code'], 'invalid_json')
                self.assertEqual(diagnostic['json_error'], expected)
                self.assertGreaterEqual(diagnostic['json_error_line'], 1)
                self.assertGreaterEqual(diagnostic['json_error_column'], 1)
                self.assertNotIn('PRIVATE_', json.dumps(done))
                self.assertEqual(done['ledger']['used']['model_calls'], 1)
                self.assertFalse(done['evidence'] or done['theses'])

    def test_duplicate_keys_and_nonfinite_numbers_still_fail_as_ambiguous(self):
        for content in ('{"questions":[],"questions":[],"data_requests":[],"calculations":[]}',
            '{"questions":[],"data_requests":[],"calculations":[NaN]}'):
            done, event = self.start(content)
            self.assertEqual(event['output_contract']['failure_code'], 'ambiguous_json')
            self.assertNotIn('json_error', event['output_contract'])

    def test_schema_success_is_not_research_approval(self):
        task = self.agent.create('核对原件条款')
        plan = {'questions': question_specs(task), 'data_requests': [], 'calculations': []}
        with patch('researchhelper.model.urllib.request.urlopen', return_value=io.BytesIO(self.response(json.dumps(plan)))):
            done = self.agent.start(task['id'])
        self.assertTrue(done['ledger']['dispatches'][0]['output_contract']['shape_accepted'])
        self.assertEqual(done['status'], '部分完成')
        self.assertEqual(done['evidence'], [])

    def test_bad_extract_preserves_original_and_saves_restricted_delivery(self):
        task = self.agent.create('政策何时生效？', objects=['材料示例政策'], cutoff='2026-07-20T18:00:00+08:00')
        task = self.agent.add_material(task['id'], 'policy.txt',
            '发布日期：2026年7月19日。\n\n政策自2026年7月21日起生效。'.encode())
        original = copy.deepcopy(task['materials'])
        plan = {'questions': question_specs(task), 'data_requests': [], 'calculations': []}
        model = fixtures.FabricatedModel([plan, {'evidence_specs': [{'text': 'https://example.invalid/?token=fabricated"tail'}]}])
        with patch('researchhelper.model.urllib.request.urlopen', side_effect=model.respond) as request:
            done = self.agent.start(task['id'])
        self.assertEqual(request.call_count, 2)
        self.assertEqual(done['status'], '部分完成')
        self.assertEqual(done['ledger']['used']['model_calls'], 2)
        self.assertEqual(done['ledger']['used']['data_requests'], 0)
        self.assertEqual(done['materials'], original)
        self.assertFalse(done['evidence'] or done['theses'] or done['tracking'])
        self.assertEqual(done['ledger']['dispatches'][-1]['failure_code'], 'private_content')
        self.assertTrue(done['delivery'])
        self.assertIn('受限材料报告', self.agent.export(done['id'])['html'])


if __name__ == '__main__':
    unittest.main()
