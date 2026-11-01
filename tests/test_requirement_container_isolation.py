"""Synthetic per-requirement errors against retained originals, never paid retries."""
import copy
import gzip
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from researchhelper.contracts import ContractError, canonical, fingerprint
from researchhelper.data_requirements import pending
from researchhelper.engine import ResearchAgent
from researchhelper.model import ModelService, ModelSettings
from researchhelper.output_schema import (ORIGINAL_SELECTION_REJECTIONS, decode_original_spans,
    original_span_output_schema, validated_original_selection_rejections)
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import PHASE_FIELDS, evidence_contract, model_object

ROOT = Path(__file__).resolve().parents[1]
POLICY = 'original_requirement_isolation_version'


def prepare_fixture():
    from tests.test_independent_original_selections import IndependentOriginalSelectionsTests
    IndependentOriginalSelectionsTests.setUpClass()
    fixture = IndependentOriginalSelectionsTests(); fixture.setUp()
    try:
        fixture.task[POLICY] = 1
        fixture.task['input_hash'] = input_fingerprint(fixture.task)
        fixture.schema = original_span_output_schema(fixture.task, fixture.views)
        wire = fixture.wire()
        good = fixture.item(fixture.slot); good['id'] = 'valid_requirement_control'
        wire['requirements'][fixture.slot['key']] = dict(status='found', items=[good])
        other = next(key for key in wire['requirements'] if key != fixture.slot['key'])
        return copy.deepcopy(dict(task=fixture.task, views=fixture.views, wire=wire,
                                  good=fixture.slot['key'], bad=other))
    finally:
        fixture.doCleanups()


class RequirementContainerIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        supplied = os.environ.get('RESEARCHHELPER_REQUIREMENT_ISOLATION_FIXTURE')
        cls.fixture = json.loads(gzip.decompress(Path(supplied).read_bytes())) if supplied else prepare_fixture()

    def setUp(self):
        guard = patch('socket.socket.connect', side_effect=AssertionError('Offline test control only'))
        guard.start(); self.addCleanup(guard.stop)
        if os.environ.get('RESEARCHHELPER_FROZEN_TEST_EXECUTABLE'):
            from frozen_archive_test_loader import frozen_pdf_worker
            from researchhelper.material_parser import MaterialParser
            worker = patch.object(MaterialParser, 'worker_target', staticmethod(frozen_pdf_worker))
            worker.start(); self.addCleanup(worker.stop)
        self.task, self.views = copy.deepcopy(self.fixture['task']), copy.deepcopy(self.fixture['views'])
        self.schema = original_span_output_schema(self.task, self.views)
        self.wire = copy.deepcopy(self.fixture['wire'])
        self.good, self.bad = self.fixture['good'], self.fixture['bad']

    def malformed(self):
        wire = copy.deepcopy(self.wire)
        # Only the observed error categories are reproduced. The real rejected
        # values were not retained; this is not a recovered model response.
        wire['requirements'][self.bad] = dict(status='unregistered_status', text='synthetic bad wrapper')
        return wire

    def decode(self, wire=None, task=None):
        task = self.task if task is None else task
        return decode_original_spans(self.malformed() if wire is None else wire,
                                     original_span_output_schema(task, self.views), (task, self.views))

    def test_bad_known_requirement_preserves_valid_selection_without_mutation_or_approval(self):
        before = fingerprint((self.task, self.views, self.wire))
        result = self.decode()
        self.assertEqual([s['id'] for s in result['evidence_specs']], ['valid_requirement_control'])
        diagnostic = result[ORIGINAL_SELECTION_REJECTIONS]
        self.assertEqual(diagnostic['version'], 2)
        self.assertEqual(diagnostic['submitted_items'], diagnostic['accepted_items'])
        self.assertEqual(diagnostic['rejected_requirements'], [dict(
            location='$.requirements.' + self.bad, category='requirement_schema_mismatch')])
        self.assertEqual(validated_original_selection_rejections(diagnostic, self.schema), diagnostic)
        self.assertNotIn('synthetic bad wrapper', canonical(result))
        self.assertFalse(diagnostic['automatic_repair'] or diagnostic['evidence_approved'] or diagnostic['coverage_approved'])
        self.assertEqual(fingerprint((self.task, self.views, self.wire)), before)
        self.assertTrue(pending(self.task))

    def test_missing_reason_wrong_status_nonobject_empty_found_and_extra_fields_are_local(self):
        for response in (dict(status='not_found'), dict(status='not_found', reason=' '),
                         dict(status='unknown'), 'not_an_object', dict(status='found', items=[]),
                         dict(status='not_found', reason='no selection', extra='not allowed')):
            with self.subTest(response=response):
                wire = copy.deepcopy(self.wire); wire['requirements'][self.bad] = response
                result = self.decode(wire)
                self.assertEqual(len(result['evidence_specs']), 1)
                self.assertEqual(len(result[ORIGINAL_SELECTION_REJECTIONS]['rejected_requirements']), 1)

    def test_unsubmitted_requirement_remains_a_format_gap_not_data_absence(self):
        wire = copy.deepcopy(self.wire); wire['requirements'].pop(self.bad)
        result = self.decode(wire)
        self.assertEqual(len(result['evidence_specs']), 1)
        self.assertEqual(result[ORIGINAL_SELECTION_REJECTIONS]['rejected_requirements'][0]['category'],
                         'requirement_not_submitted')
        self.assertEqual(self.task['questions'], self.fixture['task']['questions'])

    def test_all_malformed_requirements_return_no_facts_or_completion(self):
        wire = copy.deepcopy(self.wire)
        for key in wire['requirements']: wire['requirements'][key] = None
        result = self.decode(wire)
        self.assertEqual(result['evidence_specs'], [])
        diagnostic = result[ORIGINAL_SELECTION_REJECTIONS]
        self.assertEqual(len(diagnostic['rejected_requirements']), len(wire['requirements']))
        self.assertEqual(diagnostic['accepted_items'], 0)
        self.assertEqual(validated_original_selection_rejections(diagnostic, self.schema), diagnostic)

    def test_valid_batch_matches_legacy_and_old_tasks_do_not_gain_new_handling(self):
        current = self.decode(self.wire)
        self.assertNotIn(ORIGINAL_SELECTION_REJECTIONS, current)
        old = copy.deepcopy(self.task); old.pop(POLICY); old['input_hash'] = input_fingerprint(old)
        wire = copy.deepcopy(self.wire)
        wire['binding_ref'] = original_span_output_schema(old, self.views)['properties']['binding_ref']['enum'][0]
        self.assertEqual(current, self.decode(wire, old))
        wire['requirements'][self.bad] = dict(status='unregistered_status')
        with self.assertRaises(ContractError): self.decode(wire, old)

    def test_wrong_binding_unknown_root_or_requirement_and_nonobject_map_still_fail(self):
        for change in ('binding', 'root', 'unknown_requirement', 'requirements_type'):
            wire = self.malformed()
            if change == 'binding': wire['binding_ref'] = 'foreign_task'
            elif change == 'root': wire['evidence_approved'] = True
            elif change == 'unknown_requirement': wire['requirements']['foreign_need'] = dict(status='not_found', reason='x')
            else: wire['requirements'] = []
            with self.subTest(change=change), self.assertRaises(ContractError): self.decode(wire)

    def test_duplicate_ids_and_capacity_do_not_get_repaired_by_quarantine(self):
        wire = copy.deepcopy(self.wire)
        wire['requirements'][self.good]['items'] *= 2
        with self.assertRaises(ContractError): self.decode(wire)
        wire = self.malformed()
        wire['requirements'][self.bad]['items'] = self.wire['requirements'][self.good]['items'] * 1001
        with self.assertRaises(ContractError): self.decode(wire)

    def test_diagnostic_hash_counts_locations_overlap_and_flags_cannot_be_forged(self):
        original = self.decode()[ORIGINAL_SELECTION_REJECTIONS]
        for change in ('hash', 'count', 'bool', 'location', 'duplicate', 'approval', 'raw', 'overlap'):
            diagnostic = copy.deepcopy(original)
            if change == 'hash': diagnostic['schema_hash'] = 'foreign'
            elif change == 'count': diagnostic['expected_requirements'] += 1
            elif change == 'bool': diagnostic['expected_requirements'] = True
            elif change == 'location': diagnostic['rejected_requirements'][0]['location'] = '$.requirements.foreign'
            elif change == 'duplicate': diagnostic['rejected_requirements'] *= 2
            elif change == 'approval': diagnostic['coverage_approved'] = True
            elif change == 'raw': diagnostic['raw_output'] = 'not retained'
            else:
                diagnostic['rejected_items'] = [dict(location='$.requirements.' + self.bad + '.items[0]', category='selector_schema_mismatch')]
                diagnostic['submitted_items'] += 1
            with self.subTest(change=change), self.assertRaises(ContractError):
                validated_original_selection_rejections(diagnostic, self.schema)

    def test_unknown_policy_values_do_not_enable_isolation(self):
        for value in (True, 0, 2, '1'):
            task = copy.deepcopy(self.task); task[POLICY] = value
            with self.subTest(value=value), self.assertRaises(ContractError): self.decode(task=task)

    def test_policy_is_bound_to_new_input_and_frozen_content_not_old_task(self):
        from researchhelper.archive import ResearchArchive
        from researchhelper.reports import compose
        folder = tempfile.TemporaryDirectory(prefix='requirement-policy-', dir=ROOT/'result/acceptance')
        self.addCleanup(folder.cleanup)
        agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True, require_argument_support=True)
        self.addCleanup(agent.close_runtime)
        task = agent.create('解释原文证据缺失与提取格式错误的区别', research_mode='current')
        self.assertEqual(task[POLICY], 1)
        agent._adopt_method_knowledge(task)
        task['status'] = '部分完成'
        agent._gap(task, '仅为离线契约检查，未执行研究', category='question_coverage')
        self.assertEqual(compose(task)[POLICY], 1)
        previous = copy.deepcopy(task); previous.pop(POLICY)
        self.assertNotEqual(input_fingerprint(previous), input_fingerprint(task))

    def test_receipt_is_idempotent_and_cannot_upgrade_legacy_task(self):
        result = self.decode(); diagnostic = result[ORIGINAL_SELECTION_REJECTIONS]
        receipt = dict(phase='extract', output_protocol='strict_original_spans_v1', task_id=self.task['id'],
            research_version=self.task['version'], batch=self.task['batch'], input_hash=self.task['input_hash'],
            output_hash=fingerprint(result), output_schema_hash=fingerprint(self.schema),
            original_selection_rejections=diagnostic)
        agent = object.__new__(ResearchAgent); agent._save_model_checkpoint = Mock()
        preserved = {k: copy.deepcopy(self.task[k]) for k in ('ledger', 'questions', 'evidence')}
        agent._retain_original_selection_diagnostics(self.task, receipt, 'offline_input')
        agent._retain_original_selection_diagnostics(self.task, receipt, 'offline_input')
        agent._save_model_checkpoint.assert_called_once()
        self.assertEqual(len(self.task['reasoning_state']['original_selection_rejections']), 1)
        self.assertEqual({k: self.task[k] for k in preserved}, preserved)
        old = copy.deepcopy(self.task); old.pop(POLICY)
        with self.assertRaises(ContractError): agent._retain_original_selection_diagnostics(old, receipt, 'offline_input')

    def test_real_response_decoder_retains_diagnostic_with_one_fake_dispatch_no_extra_call(self):
        before = copy.deepcopy(self.task['ledger'])
        response = dict(object='response', status='completed', error=None, incomplete_details=None,
            output=[dict(type='message', role='assistant', status='completed',
                         content=[dict(type='output_text', text=json.dumps(self.malformed()))])], usage={})
        folder = tempfile.TemporaryDirectory(prefix='requirement-response-', dir=ROOT/'result/acceptance')
        self.addCleanup(folder.cleanup)
        settings = ModelSettings(folder.name)
        settings.save(dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash', api_key='OFFLINE_CONTROL'))
        service = ModelService(settings, offline=False)
        payload = dict(questions=self.task['questions'], knowledge=self.task['knowledge'],
            untrusted_sources=self.views, evidence_contract=evidence_contract(self.task, self.views),
            calculations=self.task['calculation_specs'], field_registry={}, source_reading=None)
        with patch.object(service, '_request_bytes', return_value=json.dumps(response).encode()) as transport:
            value, receipt = model_object(service, self.task, 'extract', payload, PHASE_FIELDS['extract'])
        transport.assert_called_once()
        self.assertEqual(len(value['evidence_specs']), 1)
        self.assertEqual(receipt['original_selection_rejections']['version'], 2)
        self.assertEqual(self.task['ledger']['used']['model_calls'], before['used']['model_calls'] + 1)
        self.assertEqual(self.task['ledger']['used']['data_requests'], before['used']['data_requests'])
        self.assertEqual(self.task['ledger']['limits'], before['limits'])


if __name__ == '__main__': unittest.main()
