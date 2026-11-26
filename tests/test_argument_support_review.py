"""Evidence-bound review mechanics; mocked verdicts are not semantic proof."""
import copy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from tests.argument_review_fixture import financial_review_fixture
from researchhelper.argument_support import (PROTOCOL, VERSION, review_request, review_schema,
    review_messages, validate_request, decode_review)
from researchhelper.contracts import ContractError
from researchhelper.model import ModelService, ModelSettings, ModelCompletionError
from researchhelper.output_schema import structured_json_completion

ROOT = Path(__file__).resolve().parents[1]


class ArgumentSupportReviewTests(unittest.TestCase):
    def setUp(self):
        self.task, _ = financial_review_fixture()
        self.proposal = copy.deepcopy(self.task['host_result'])
        self.request = review_request(self.task, self.proposal)
        self.schema = review_schema(self.request)
        related = sorted({ident for item in self.request['targets'] for ident in item['question_ids']})
        self.wire = dict(version=VERSION, review_hash=self.request['review_hash'], questions=[
            dict(question_id=ident,status='answered',reason='隔离协议夹具。',missing_evidence=[])
            for ident in related], reviews=[
            dict(target_id=item['id'], segments=[dict(text=item['text'], verdict='supported',
                basis_refs=['scope:task'], explanation='合成复核结果，仅验证协议，不证明语义正确。')])
            for item in self.request['targets']])
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No networking'))
        guard.start(); self.addCleanup(guard.stop)

    def decode(self, wire=None):
        return decode_review(self.wire if wire is None else wire, self.schema, self.request)

    def test_actual_numeric_text_is_rendered_without_mutating_proposal(self):
        before = copy.deepcopy((self.task, self.proposal))
        self.assertEqual(len(self.request['targets']), 4)
        self.assertTrue(any('50.75' in item['text'] for item in self.request['targets']))
        self.assertEqual((self.task, self.proposal), before)

    def test_hash_binds_task_version_batch_cutoff_questions_and_inputs(self):
        for field, value in (('id', 'foreign'), ('version', 99), ('batch', 99),
                              ('cutoff', '2026-01-01T00:00:00+08:00'), ('input_hash', 'changed')):
            task = copy.deepcopy(self.task); task[field] = value
            with self.assertRaises(ContractError):
                validate_request(task, self.proposal, self.request)

    def test_changed_wording_and_evidence_cannot_reuse_review(self):
        proposal = copy.deepcopy(self.proposal)
        proposal['theses'][0]['numeric_segments']['title'] = [{'text': '不同的判断'}]
        with self.assertRaises(ContractError): validate_request(self.task, proposal, self.request)
        self.task['evidence'][0]['text'] += 'changed'
        with self.assertRaises(ContractError): validate_request(self.task, self.proposal, self.request)

    def test_changed_calculation_or_knowledge_invalidates_review(self):
        for field in ('calculations', 'knowledge'):
            task = copy.deepcopy(self.task)
            if field == 'calculations': task[field][0]['limitations'] = 'changed'
            else: task[field]['entries'][0]['content'] = 'changed'
            with self.assertRaises(ContractError): validate_request(task, self.proposal, self.request)

    def test_full_valid_envelope_is_not_source_or_causal_approval(self):
        result = self.decode()
        self.assertTrue(result['supported'])
        self.assertFalse(result['source_approval'])
        self.assertFalse(result['independent_causal_proof'])
        self.assertEqual(result['assessments'][0]['segments'][-1]['end'],
                         len(self.request['targets'][0]['text']))

    def test_one_insufficient_span_blocks_entire_target(self):
        self.wire['reviews'][0]['segments'][0]['verdict'] = 'insufficient'
        result = self.decode()
        self.assertFalse(result['supported'])
        self.assertFalse(result['assessments'][0]['supported'])
        self.assertTrue(result['assessments'][1]['supported'])

    def test_contradicted_span_is_not_silently_downgraded_to_supported(self):
        self.wire['reviews'][0]['segments'][0]['verdict'] = 'contradicted'
        self.assertFalse(self.decode()['supported'])

    def test_missing_duplicate_and_unknown_targets_rejected(self):
        for kind in ('missing', 'duplicate', 'unknown'):
            wire = copy.deepcopy(self.wire)
            if kind == 'missing': wire['reviews'].pop()
            elif kind == 'duplicate': wire['reviews'][-1] = copy.deepcopy(wire['reviews'][0])
            else: wire['reviews'][0]['target_id'] = 'unknown'
            with self.assertRaises(ContractError): self.decode(wire)

    def test_partial_reordered_and_rewritten_text_rejected(self):
        for replacement in ('', 'changed', self.request['targets'][0]['text'][:-1]):
            wire = copy.deepcopy(self.wire)
            wire['reviews'][0]['segments'][0]['text'] = replacement
            with self.assertRaises(ContractError): self.decode(wire)

    def test_split_claims_must_cover_exact_unicode_text(self):
        segment = self.wire['reviews'][0]['segments'][0]
        text = segment['text']; middle = len(text) // 2
        self.wire['reviews'][0]['segments'] = [dict(segment, text=text[:middle]),
                                             dict(segment, text=text[middle:], verdict='insufficient')]
        result = self.decode()
        segments = result['assessments'][0]['segments']
        self.assertEqual(segments[0]['end'], segments[1]['start'])
        self.assertFalse(result['supported'])

    def test_bare_approval_unknown_basis_and_repeated_basis_rejected(self):
        for refs in ([], ['evidence:foreign'], ['scope:task', 'scope:task']):
            wire = copy.deepcopy(self.wire); wire['reviews'][0]['segments'][0]['basis_refs'] = refs
            with self.assertRaises(ContractError): self.decode(wire)

    def test_empty_or_excessive_explanation_rejected(self):
        for text in (' ', '长' * 2001):
            wire = copy.deepcopy(self.wire); wire['reviews'][0]['segments'][0]['explanation'] = text
            with self.assertRaises(ContractError): self.decode(wire)

    def test_json_channel_requires_actual_context_and_rejects_duplicate_keys(self):
        text = json.dumps(self.wire, ensure_ascii=False)
        result = json.loads(structured_json_completion(text, self.schema, protocol=PROTOCOL,
                                                       context=(self.task, self.proposal)))
        self.assertTrue(result['supported'])
        with self.assertRaises(ContractError):
            structured_json_completion(text, self.schema, protocol=PROTOCOL)
        with self.assertRaises(ContractError):
            structured_json_completion('{"version":1,' + text[1:], self.schema, protocol=PROTOCOL,
                                       context=(self.task, self.proposal))

    def test_review_instruction_is_separate_from_untrusted_material(self):
        messages = review_messages(self.request)
        self.assertEqual([item['role'] for item in messages], ['system', 'user'])
        self.assertEqual(json.loads(messages[1]['content']), self.request)
        self.assertNotIn(self.request['targets'][0]['text'], messages[0]['content'])

    def test_real_model_transport_consumes_last_call_but_not_a_ninth(self):
        with tempfile.TemporaryDirectory(prefix='argument-model-', dir=ROOT / 'result/acceptance') as folder:
            settings = ModelSettings(Path(folder))
            settings.save(dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash',
                               api_key='FAKE_REVIEW_ONLY_KEY', reasoning_effort='low', extraction_mode='direct'))
            service = ModelService(settings, offline=False)
            response = dict(object='response', status='completed', error=None, incomplete_details=None,
                output=[dict(type='message', role='assistant', status='completed',
                    content=[dict(type='output_text', text=json.dumps(self.wire))])],
                usage=dict(input_tokens=10, output_tokens=20))
            self.task['ledger']['used']['model_calls'] = 7
            with patch.object(service, '_request_bytes', return_value=json.dumps(response).encode()) as transport:
                output = json.loads(service.reason_argument_support(self.task, self.proposal, delivery=True))
                self.assertTrue(output['supported'])
                self.assertEqual(self.task['ledger']['used']['model_calls'], 8)
                with self.assertRaises(ContractError):
                    service.reason_argument_support(self.task, self.proposal, delivery=True)
                transport.assert_called_once()
            event = self.task['ledger']['dispatches'][-1]
            self.assertEqual(event['status'], '成功')


if __name__ == '__main__':
    unittest.main()
