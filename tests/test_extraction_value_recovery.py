"""Classify rejected JSON without keeping values; never repair or approve it."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT, schema_diagnostic_fixture, test_completed_extraction_format_recovery
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import DOCUMENT_CONTENT_INTENT
from researchhelper.engine import ResearchAgent
from researchhelper.model import ModelService, ModelSettings, ModelCompletionError
from researchhelper.output_schema import original_span_output_schema, original_span_prompt
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import evidence_contract
from researchhelper.reports import compose


POLICY = 'extraction_value_recovery_version'
STUDY = ROOT / 'result/acceptance/one-sentence-research-hpioqat1/task.json'


class SubmissionValueDiagnosticTests(unittest.TestCase):
    def test_duplicate_keys_are_diagnosed_even_if_values_are_equal(self):
        fixture = schema_diagnostic_fixture.SchemaDiagnosticTests()
        self.addCleanup(fixture.doCleanups)
        for text in ('{"private":"PRIVATE","private":"PRIVATE"}',
                     '{"version":1,"nested":{"private":1,"private":2}}'):
            with self.subTest(text=text):
                event = fixture.call(text)
                self.assertEqual(event['structured_failure_category'], 'invalid_argument_json')
                self.assertEqual(event['structured_output_diagnostic']['json_error'], 'duplicate_key')
                self.assertNotIn('private', json.dumps(event))
                self.assertNotIn('PRIVATE', json.dumps(event))

    def test_nonfinite_literals_never_become_values_or_expose_original_tokens(self):
        fixture = schema_diagnostic_fixture.SchemaDiagnosticTests()
        self.addCleanup(fixture.doCleanups)
        for constant in ('NaN', 'Infinity', '-Infinity'):
            with self.subTest(constant=constant):
                event = fixture.call('{"private":' + constant + '}')
                self.assertEqual(event['structured_output_diagnostic']['json_error'], 'nonfinite_constant')
                self.assertFalse(event['structured_output_diagnostic']['automatic_repair'])
                self.assertFalse(event['structured_output_diagnostic']['submitted_values_retained'])
                self.assertNotIn(constant, json.dumps(event))


class ExtractionValueRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_completed_extraction_format_recovery.CompletedExtractionFormatRecoveryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        self.task = self.fixture.task
        self.task[POLICY] = 1
        self.event = self.task['ledger']['dispatches'][-1]
        self.event['structured_output_diagnostic']['json_error'] = 'duplicate_key'

    def test_recognized_value_error_uses_one_existing_bounded_submission(self):
        with patch('researchhelper.engine.model_object', side_effect=self.fixture.submit) as submit:
            self.assertIsNotNone(self.fixture.recover())
            self.assertIsNone(self.fixture.recover())
        self.assertEqual(submit.call_count, 1)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 6)
        self.assertEqual(self.task['evidence'], [])
        self.assertEqual(self.task['calculations'], [])

    def test_legacy_unknown_or_invalid_policy_never_acquires_value_recovery(self):
        for version in (None, True, 0, 2):
            if version is None:
                self.task.pop(POLICY, None)
            else:
                self.task[POLICY] = version
            with self.subTest(version=version), patch('researchhelper.engine.model_object') as submit:
                self.assertIsNone(self.fixture.recover())
                submit.assert_not_called()

    def test_nonfinite_error_can_resubmit_but_not_generic_unclassified_value_error(self):
        self.event['structured_output_diagnostic']['json_error'] = 'nonfinite_constant'
        self.assertIsNotNone(ResearchAgent._completed_model_format_issue(self.task, 'extract'))
        self.event['structured_output_diagnostic']['json_error'] = 'unknown_value_error'
        self.assertIsNone(ResearchAgent._completed_model_format_issue(self.task, 'extract'))

    def test_value_policy_never_authorizes_analysis_retries_or_unknown_completion(self):
        self.event['research_phase'] = 'analyze'
        self.assertIsNone(ResearchAgent._completed_model_format_issue(self.task, 'analyze'))
        self.event['research_phase'] = 'extract'
        for change in (dict(response_body_complete=False), dict(finish_reason='length'),
                       dict(provider_completion_status='failed'), dict(status='已派发')):
            previous = copy.deepcopy(self.event)
            self.event.update(change)
            with self.subTest(change=change):
                self.assertIsNone(ResearchAgent._completed_model_format_issue(self.task, 'extract'))
            self.event.clear(); self.event.update(previous)

    def test_budget_and_cancellation_still_block_dispatch(self):
        self.task['ledger']['used']['model_calls'] = 6
        with patch('researchhelper.engine.model_object') as submit:
            self.assertIsNone(self.fixture.recover())
            submit.assert_not_called()


class ValueRecoveryTransportTests(unittest.TestCase):
    def test_actual_responses_decoder_charges_rejection_and_fresh_submission(self):
        original = STUDY.read_bytes()
        task = json.loads(original)
        task[POLICY] = 1  # Explicit unit projection, not an upgrade of the closed study.
        views = task['reasoning_state']['reading']['bundle']['views']
        schema = original_span_output_schema(task, views)
        prompt = original_span_prompt(dict(untrusted_sources=views,
            evidence_contract=evidence_contract(task, views)), task)
        wire = dict(version=schema['properties']['version']['enum'][0],
                    binding_ref=prompt['span_binding_ref'], selections=[])
        malformed = json.dumps(wire)[:-1] + ',"selections":[]}'
        def response(text):
            return json.dumps(dict(object='response', status='completed', error=None,
                incomplete_details=None, output=[dict(type='message', role='assistant',
                status='completed', content=[dict(type='output_text', text=text)])], usage={})).encode()
        before = copy.deepcopy(task['ledger'])
        with tempfile.TemporaryDirectory(prefix='value-recovery-transport-') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses',
                               model='deepseek-flash', api_key='FAKE_VALUE_RECOVERY_KEY'))
            model = ModelService(settings, offline=False)
            responses = iter((response(malformed), response(json.dumps(wire))))
            def completed_transport(task, request, started, timeout, record):
                body = next(responses)
                record.update(response_body_complete=True, received_response_bytes=len(body))
                return body
            with patch.object(model, '_request_bytes', side_effect=completed_transport) as transport:
                with self.assertRaises(ModelCompletionError):
                    model.reason_structured_original_spans(task, [], views)
                issue = ResearchAgent._completed_model_format_issue(task, 'extract')
                self.assertEqual(issue['json_error'], 'duplicate_key')
                result = json.loads(model.reason_structured_original_spans(task, [], views))
            self.assertEqual(transport.call_count, 2)
        self.assertEqual(result, dict(evidence_specs=[]))
        self.assertEqual(task['ledger']['used']['model_calls'], before['used']['model_calls'] + 2)
        self.assertEqual(task['ledger']['used']['data_requests'], before['used']['data_requests'])
        self.assertEqual(task['ledger']['limits'], before['limits'])
        self.assertEqual(task['evidence'], [])
        self.assertEqual(task['calculations'], [])
        self.assertEqual(compose(task)[POLICY], 1)
        self.assertEqual(STUDY.read_bytes(), original)

    def test_new_task_policy_is_input_bound_and_does_not_upgrade_fixtures(self):
        with tempfile.TemporaryDirectory(prefix='value-recovery-policy-') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            try:
                task = agent.create('研究510180.SH的当前净值')
                self.assertEqual(task[POLICY], 1)
                self.assertEqual(task['input_hash'], input_fingerprint(task))
                changed = copy.deepcopy(task); changed.pop(POLICY)
                self.assertNotEqual(task['input_hash'], input_fingerprint(changed))
                self.assertNotIn(POLICY, agent.create_case('mechanism'))
                self.assertNotIn(POLICY, agent.create('核对文件', intent=DOCUMENT_CONTENT_INTENT))
            finally:
                agent.close_runtime()


if __name__ == '__main__':
    unittest.main()
