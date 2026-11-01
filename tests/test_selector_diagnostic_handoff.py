"""Producer, actual model wrapper, gateway and source adoption in one offline handoff."""
import copy
import importlib
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import Mock, patch

fixture = importlib.import_module((__package__+'.' if __package__ else '')+'selector_handoff_fixture')
from researchhelper.contracts import ContractError, canonical, fingerprint, validate_evidence
from researchhelper.output_schema import (decode_original_batch, ORIGINAL_SELECTION_REJECTIONS,
    validated_original_selection_rejections, DETAILED_SELECTOR_DIAGNOSTIC_VERSION)
from researchhelper.reasoning import model_object
from researchhelper.model import ModelSettings, ModelService
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import candidate_evidence
from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.source_reader import validate_read_specs
from researchhelper.model_presentation import restore


class SelectorDiagnosticHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = fixture.SOURCE.read_bytes()
        cls.guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No real external I/O'))
        cls.guard.start(); cls.addClassCleanup(cls.guard.stop)
        if os.environ.get('RESEARCHHELPER_FROZEN_TEST_EXECUTABLE'):
            from frozen_archive_test_loader import frozen_pdf_worker
            from researchhelper.material_parser import MaterialParser
            worker = patch.object(MaterialParser, 'worker_target', staticmethod(frozen_pdf_worker))
            worker.start(); cls.addClassCleanup(worker.stop)
        cls.task, cls.views, (cls.payload, cls.schema, cls.wire, cls.prompt) = fixture.retained_case()
        cls.decoded = decode_original_batch(cls.wire, cls.schema, (cls.task, cls.views))
        response = dict(object='response', status='completed', error=None, incomplete_details=None,
            output=[dict(type='message', role='assistant', status='completed',
                content=[dict(type='output_text', text=canonical(cls.wire))])], usage={})
        cls.before = copy.deepcopy(cls.task['ledger']['used'])
        with tempfile.TemporaryDirectory(prefix='selector-handoff-model-') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash',
                               api_key='SYNTHETIC_SELECTOR_HANDOFF_KEY'))
            service = ModelService(settings, offline=False)
            with patch.object(service, '_request_bytes', return_value=json.dumps(response).encode()) as transport:
                cls.result, cls.receipt = model_object(service, cls.task, 'extract', cls.payload, {'evidence_specs'})
            transport.assert_called_once()
            cls.sent = json.loads(transport.call_args.args[1].data)
        cls.task['ledger']['dispatches'][-1]['synthetic_test_transport'] = True

    @classmethod
    def tearDownClass(cls):
        assert fixture.SOURCE.read_bytes() == cls.original

    def diagnostic(self):
        return copy.deepcopy(self.receipt['original_selection_rejections'])

    def test_decoder_and_gateway_accept_the_same_versioned_sidecar(self):
        value = self.decoded[ORIGINAL_SELECTION_REJECTIONS]
        self.assertEqual(value['version'], DETAILED_SELECTOR_DIAGNOSTIC_VERSION)
        self.assertEqual(value['version'], 3)
        self.assertEqual(validated_original_selection_rejections(value, self.schema), value)
        self.assertEqual(self.receipt['original_selection_rejections'], value)
        self.assertEqual((value['submitted_items'], value['accepted_items']), (2, 1))

    def test_actual_model_wrapper_keeps_the_valid_original_with_one_mocked_dispatch(self):
        self.assertEqual(len(self.result['evidence_specs']), 1)
        spec = self.result['evidence_specs'][0]
        self.assertEqual((spec['id'], spec['raw_value'], spec['raw_unit']), ('handoff_revenue_2026', 344815421, '千元'))
        self.assertFalse('source_check' in spec or 'applicability_check' in spec)
        self.assertEqual(self.task['ledger']['used']['model_calls'], self.before['model_calls']+1)
        self.assertEqual(self.task['ledger']['used']['data_requests'], self.before['data_requests'])

    def test_serialized_request_keeps_the_actual_batch_instead_of_an_old_route(self):
        body = json.loads(self.sent['input'][-1]['content'])
        if body.get('format') == 'researchhelper.table_input':
            body = restore(body)
        self.assertEqual(fingerprint(body), fingerprint(self.prompt))
        self.assertEqual(self.sent['text']['format']['name'], 'rh_select_original_batch_v1')
        self.assertFalse(self.sent['store'])

    def test_valid_record_passes_real_reading_source_and_financial_adoption(self):
        specs = self.result['evidence_specs']
        bundle = self.task['reasoning_state']['reading']['bundle']
        validate_read_specs(self.task, specs, bundle)
        spec = specs[0]
        source = next(source for source in self.task['source_records'] if source['id'] == spec['artifact_id'])
        item = candidate_evidence(self.task, source, spec)
        adopted = ResearchAnalyzer().assess(self.task, item, spec['review'], self.task['source_records'])
        validate_evidence(adopted, self.task)
        self.assertEqual((adopted['source_check'], adopted['applicability_check']), ('通过', '通过'))
        self.assertEqual((adopted['raw_value'], adopted['raw_unit']), (344815421, '千元'))
        self.assertFalse(self.task['evidence'] or self.task['calculations'])

    def test_archive_consumer_keeps_diagnostics_without_approving_rejected_items(self):
        task = copy.deepcopy(self.task)
        agent = ResearchAgent.__new__(ResearchAgent)
        agent._save_model_checkpoint = Mock()
        agent._retain_original_selection_diagnostics(task, self.receipt, 'synthetic_handoff_digest')
        agent._save_model_checkpoint.assert_called_once()
        value = task['reasoning_state']['original_selection_rejections'][-1]['diagnostic']
        self.assertEqual(value, self.diagnostic())
        self.assertFalse(value['evidence_approved'] or value['coverage_approved'] or value['automatic_repair'])

    def test_private_selector_values_never_enter_diagnostics(self):
        value = self.diagnostic()
        self.assertNotIn('private_optional_address_marker', canonical(value))
        detail = value['rejected_items'][0]['diagnostic']
        self.assertEqual(detail['schema_issues'], [dict(path='$.amount_reference', code='enum_mismatch')])
        self.assertFalse(detail['submitted_values_retained'] or detail['automatic_repair'])

    def test_unknown_paths_categories_values_or_approval_remain_rejected(self):
        for change in ('path', 'code', 'value', 'approval', 'count', 'schema', 'version'):
            value = self.diagnostic()
            detail = value['rejected_items'][0]['diagnostic']
            if change == 'path': detail['schema_issues'][0]['path'] = '$.private_unregistered_marker'
            elif change == 'code': detail['schema_issues'][0]['code'] = 'private_unregistered_marker'
            elif change == 'value': detail['submitted_value'] = 'private_unregistered_marker'
            elif change == 'approval': value['evidence_approved'] = True
            elif change == 'count': value['accepted_items'] = 2
            elif change == 'schema': value['schema_hash'] = 'foreign'
            else: value['version'] = True
            with self.subTest(change=change), self.assertRaises(ContractError):
                validated_original_selection_rejections(value, self.schema)

    def test_legacy_version_one_keeps_its_original_two_field_records(self):
        task = copy.deepcopy(self.task)
        task.pop('financial_address_isolation_version')
        payload, schema, wire, prompt = fixture.wire_for(task, self.views)
        value = decode_original_batch(wire, schema, (task, self.views))[ORIGINAL_SELECTION_REJECTIONS]
        self.assertEqual(value['version'], 1)
        self.assertTrue(all(set(item) == {'location', 'category'} for item in value['rejected_items']))
        self.assertEqual(validated_original_selection_rejections(value, schema), value)

    def test_requirement_version_two_is_still_separate_and_bound(self):
        schema = dict(properties=dict(requirements=dict(properties=dict(r1=dict(type='object')))))
        value = dict(version=2, schema_hash=fingerprint(schema), submitted_items=0, accepted_items=0,
            rejected_items=[], expected_requirements=1,
            rejected_requirements=[dict(location='$.requirements.r1', category='requirement_not_submitted')],
            automatic_repair=False, evidence_approved=False, coverage_approved=False)
        self.assertEqual(validated_original_selection_rejections(value, schema), value)
        value['schema_hash'] = 'foreign'
        with self.assertRaises(ContractError):
            validated_original_selection_rejections(value, schema)


if __name__ == '__main__':
    unittest.main()
