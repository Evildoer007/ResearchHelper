"""Read-scoped feedback controls on closed R133 bytes; no live research."""
import base64
import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, canonical, charge, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.extraction_feedback import FIELD, POLICY, completion_scope, enabled, validate
from researchhelper.output_schema import decode_original_spans, original_span_output_schema, fee_input_completion_binding
from researchhelper.data_requirements import pending, requirement_slots

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT/'result/acceptance/one-sentence-research-6nm7u50s'


def prepare_fixture():
    from researchhelper.evidence import EvidenceCollector
    from researchhelper.official_sources import catalogue_url
    from researchhelper.planning import question_specs
    from researchhelper.reasoning import extractable_source_views
    names = ('receipt.json', 'task.json', 'report.json', 'report.html')
    protected = {name: hashlib.sha256((STUDY/name).read_bytes()).hexdigest() for name in names}
    retained = json.loads((STUDY/'task.json').read_text())
    pages = {}
    for record in retained['source_records']:
        url = (catalogue_url(record['parameters']['page']) if record['operation'] == 'release_catalog'
               else record['snapshot']['official_origin']['url'])
        pages[url] = base64.b64decode(record['snapshot']['original_base64'], validate=True)
    def transport(task, url, started, timeout, event, **kwargs):
        event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True)
        return pages[url]
    with tempfile.TemporaryDirectory(prefix='extraction-feedback-fixture-', dir=ROOT/'result/acceptance') as folder:
        agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
        try:
            task = agent.create(retained['question'], entry='宏观', research_mode='current')
            task = agent.revise_plan(task['id'], question_specs(retained),
                '离线核对提取反馈，不重开原研究或调用模型', task['input_hash'])
            collector, remapped = EvidenceCollector(offline=False), {}
            with (patch('socket.socket.connect', side_effect=AssertionError('No external calls')),
                  patch('researchhelper.source_transport.official_html', side_effect=transport)):
                for source in retained['source_records']:
                    parameters = copy.deepcopy(source['parameters'])
                    if 'catalog_id' in parameters: parameters['catalog_id'] = remapped[parameters['catalog_id']]
                    current = collector.external(task, 'official', source['operation'], parameters, {})
                    task['source_records'].append(current); remapped[source['id']] = current['id']
            fixture = dict(task=task, views=extractable_source_views(task), protected=protected,
                mocked_transport=True, old_research_reopened=False, actual_model_quality_accepted=False)
        finally: agent.close_runtime()
    assert protected == {name: hashlib.sha256((STUDY/name).read_bytes()).hexdigest() for name in names}
    return fixture


def literal(schema):
    if 'anyOf' in schema: return literal(schema['anyOf'][0])
    if 'enum' in schema: return schema['enum'][0]
    if schema['type'] == 'object': return {key: literal(value) for key, value in schema['properties'].items()}
    if schema['type'] == 'array': return []
    if schema['type'] == 'string': return 'offline_feedback_control'
    raise AssertionError('No invented selector')


class ExtractionFeedbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        supplied = os.environ.get('RESEARCHHELPER_EXTRACTION_FEEDBACK_FIXTURE')
        cls.fixture = json.loads(gzip.decompress(Path(supplied).read_bytes())) if supplied else prepare_fixture()

    def setUp(self):
        guard = patch('socket.socket.connect', side_effect=AssertionError('No provider or model calls'))
        guard.start(); self.addCleanup(guard.stop)
        if os.environ.get('RESEARCHHELPER_FROZEN_TEST_EXECUTABLE'):
            from frozen_archive_test_loader import frozen_pdf_worker
            from researchhelper.material_parser import MaterialParser
            worker = patch.object(MaterialParser, 'worker_target', staticmethod(frozen_pdf_worker))
            worker.start(); self.addCleanup(worker.stop)
        self.task, self.views = (copy.deepcopy(self.fixture[key]) for key in ('task', 'views'))
        self.task.update(status='运行中', run_mode='model', delivery=None, pending_content=None)
        self.task['ledger']['used'].update(model_calls=3, seconds=50)
        self.slots = requirement_slots(self.task)
        self.numeric = [slot for slot in self.slots if slot['constraints']['value_kind'] == 'number']
        self.schema = original_span_output_schema(self.task, self.views)

    def wire(self):
        return dict(version=12, binding_ref=self.schema['properties']['binding_ref']['enum'][0], selections=[],
            requirements={slot['key']: dict(status='not_found', reason='同次读取尚未定位，不推断原件没有披露')
                          for slot in self.slots})

    def selected(self, slot):
        field = self.schema['properties']['requirements']['properties'][slot['key']]
        branch = next(b for b in field['anyOf'] if b['properties']['status']['enum'] == ['found'])
        return literal(branch['properties']['items']['items'])

    def rejected(self, wire, slot):
        item = self.selected(slot)
        item['temporal_reference'] = dict(observation_ref='deliberately_invalid_control')
        wire['requirements'][slot['key']] = dict(status='found', items=[item])

    def decode(self, wire=None):
        return decode_original_spans(self.wire() if wire is None else wire, self.schema, (self.task, self.views))

    def bind(self, result):
        specs = copy.deepcopy(result['evidence_specs'])
        self.task['host_result'] = dict(evidence_specs=specs)
        self.task.setdefault('reasoning_state', {})['extraction'] = dict(input_digest='local_reading_control',
            evidence_specs_hash=fingerprint(specs), receipt=dict(phase='extract', output_protocol='strict_original_spans_v1',
                task_id=self.task['id'], research_version=self.task['version'], batch=self.task['batch'],
                input_hash=self.task['input_hash'], output_schema_hash=fingerprint(self.schema),
                original_requirement_feedback=copy.deepcopy(result[FIELD])))
        return specs

    def agent(self):
        agent = object.__new__(ResearchAgent)
        agent.model = SimpleNamespace(supports_structured_analysis=lambda: True, reason_structured_original_spans=lambda: None)
        agent._active = Mock(return_value=self.task); agent._active_supplement = Mock(return_value=None)
        agent._save_model_checkpoint = Mock(); agent._gap = Mock()
        return agent

    def test_explicit_not_found_is_retained_without_evidence_or_absence_approval(self):
        before = canonical((self.task, self.views)); result = self.decode(); feedback = result[FIELD]
        self.assertFalse(result['evidence_specs'])
        self.assertEqual({row['status'] for row in feedback['requirements']}, {'not_found'})
        self.assertTrue(all(row['reason'] == '同次读取尚未定位，不推断原件没有披露' for row in feedback['requirements']))
        self.assertTrue(all(feedback[key] is False for key in ('evidence_approved', 'coverage_approved', 'source_absence_proved')))
        self.assertEqual(canonical((self.task, self.views)), before)

    def test_not_found_does_not_retry_same_reading_or_close_missing_obligations(self):
        specs = self.bind(self.decode()); scope = completion_scope(self.task, specs, self.views)
        self.assertFalse(scope['eligible']); self.assertEqual(len(scope['deferred']), len(self.numeric))
        before = canonical(self.task); agent = self.agent()
        with patch('researchhelper.engine.model_object') as model:
            agent._complete_initial_numeric_inputs(self.task, dict(untrusted_sources=self.views), 'local_reading_control', None)
        model.assert_not_called(); self.assertEqual(canonical(self.task), before)
        self.assertTrue(pending(self.task)); self.assertFalse(self.task['evidence'])

    def test_format_rejected_is_distinct_and_does_not_use_numeric_omission_retry(self):
        wire = self.wire(); self.rejected(wire, self.numeric[0])
        result = self.decode(wire); feedback = result[FIELD]
        row = next(row for row in feedback['requirements'] if row['key'] == self.numeric[0]['key'])
        self.assertEqual((row['status'], row['selected_items'], row['rejected_items'], row['reason']),
                         ('format_rejected', 0, 1, None))
        specs = self.bind(result)
        self.assertFalse(ResearchAgent._required_numeric_completion_available(self.task, self.task['host_result'], views=self.views))
        self.assertTrue(any(row['feedback_status'] == 'format_rejected' for row in completion_scope(self.task, specs, self.views)['deferred']))

    def test_missing_requirement_is_not_rewritten_as_not_found(self):
        wire = self.wire(); wire['requirements'].pop(self.numeric[0]['key'])
        result = self.decode(wire); specs = self.bind(result)
        row = next(row for row in result[FIELD]['requirements'] if row['key'] == self.numeric[0]['key'])
        self.assertEqual(row['status'], 'not_submitted'); self.assertIsNone(row['reason'])
        self.assertEqual([n['text'] for n in completion_scope(self.task, specs, self.views)['eligible']], [self.numeric[0]['text']])

    def test_mixed_feedback_only_reopens_the_unsubmitted_numeric_need(self):
        wire = self.wire(); wire['requirements'].pop(self.numeric[0]['key']); self.rejected(wire, self.numeric[1])
        specs = self.bind(self.decode(wire)); scope = completion_scope(self.task, specs, self.views)
        self.assertEqual(len(scope['eligible']), 1); self.assertEqual(len(scope['deferred']), len(self.numeric) - 1)
        original_questions = canonical(self.task['questions']); agent = self.agent()
        def submit(model, task, phase, payload, fields):
            self.assertEqual(payload['input_completion']['missing_numeric_needs'], scope['eligible'])
            selected_schema = original_span_output_schema(task, self.views)
            binding = fee_input_completion_binding(task, self.views)
            self.assertEqual(binding['all_missing_numeric_needs'], scope['all_missing'])
            self.assertEqual(binding['extraction_feedback_hash'], scope['feedback_hash'])
            found = {key for key, field in selected_schema['properties']['requirements']['properties'].items()
                     if any(branch['properties']['status']['enum'] == ['found'] for branch in field['anyOf'])}
            self.assertEqual(found, {self.numeric[0]['key']})
            charge(task['ledger'], 'model_calls')
            return dict(evidence_specs=[]), dict(phase='extract', output_hash=fingerprint([]))
        with patch('researchhelper.engine.model_object', side_effect=submit) as model:
            agent._complete_initial_numeric_inputs(self.task, dict(untrusted_sources=self.views), 'local_reading_control', None)
        self.assertEqual(model.call_count, 1); self.assertEqual(canonical(self.task['questions']), original_questions)
        with patch('researchhelper.engine.model_object') as again:
            agent._complete_initial_numeric_inputs(self.task, dict(untrusted_sources=self.views), 'local_reading_control', None)
        again.assert_not_called(); self.assertEqual(self.task['ledger']['used']['model_calls'], 4)

    def test_changed_reading_does_not_inherit_a_previous_negative(self):
        specs = self.bind(self.decode()); changed = copy.deepcopy(self.views)
        changed[0]['parts'] = changed[0]['parts'][:-1]
        scope = completion_scope(self.task, specs, changed)
        self.assertEqual(scope['eligible'], scope['all_missing']); self.assertFalse(scope['deferred'])
        self.assertEqual(self.task['ledger']['used']['model_calls'], 3)

    def test_stale_receipt_context_does_not_suppress_current_reading(self):
        specs = self.bind(self.decode())
        self.task['reasoning_state']['extraction']['receipt']['batch'] += 1
        self.assertEqual(completion_scope(self.task, specs, self.views)['eligible'], completion_scope(self.task, specs, self.views)['all_missing'])

    def test_foreign_feedback_cannot_override_a_current_receipt(self):
        specs = self.bind(self.decode())
        self.task['reasoning_state']['extraction']['receipt']['original_requirement_feedback']['context']['id'] = 'foreign'
        with self.assertRaises(ContractError): completion_scope(self.task, specs, self.views)

    def test_feedback_requires_exact_schema_views_specs_and_false_approval_fields(self):
        result = self.decode(); baseline = result[FIELD]
        changes = [dict(version=True), dict(schema_hash='foreign'), dict(views_hash='foreign'),
                   dict(evidence_specs_hash='foreign'), dict(evidence_approved=True), dict(coverage_approved=True),
                   dict(source_absence_proved=True), dict(extra='not_allowed')]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ContractError):
                validate(dict(copy.deepcopy(baseline), **change), self.task, self.views, [],
                         schema_hash=fingerprint(self.schema), schema=self.schema)

    def test_unknown_duplicate_need_and_invalid_status_counts_are_rejected(self):
        baseline = self.decode()[FIELD]
        for field, value in (('key', []), ('key', 'foreign'), ('question_id', 'foreign'), ('need_ref', 'foreign'),
                             ('status', []), ('status', 'approved'), ('selected_items', True), ('reason', None)):
            feedback = copy.deepcopy(baseline); feedback['requirements'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                validate(feedback, self.task, self.views, [], schema_hash=fingerprint(self.schema), schema=self.schema)
        for rows in ([], baseline['requirements'][:-1], baseline['requirements'] + baseline['requirements'][:1]):
            with self.assertRaises(ContractError):
                validate(dict(baseline, requirements=rows), self.task, self.views, [],
                         schema_hash=fingerprint(self.schema), schema=self.schema)

    def test_selected_feedback_does_not_approve_the_returned_proposal(self):
        slot = next(slot for slot in self.slots if slot['constraints']['value_kind'] == 'text')
        wire = self.wire(); wire['requirements'][slot['key']] = dict(status='found', items=[self.selected(slot)])
        result = self.decode(wire)
        row = next(row for row in result[FIELD]['requirements'] if row['key'] == slot['key'])
        self.assertEqual(row['status'], 'selected'); self.assertEqual(row['selected_items'], 1)
        self.assertFalse(self.task['evidence']); self.assertTrue(pending(self.task))

    def test_legacy_decoder_and_completion_behavior_are_not_upgraded(self):
        task = copy.deepcopy(self.task); task.pop(POLICY)
        schema = original_span_output_schema(task, self.views)
        wire = self.wire(); wire['binding_ref'] = schema['properties']['binding_ref']['enum'][0]
        result = decode_original_spans(wire, schema, (task, self.views))
        self.assertNotIn(FIELD, result); self.assertFalse(enabled(task))
        self.bind(self.decode()); self.task.pop(POLICY)
        self.assertTrue(ResearchAgent._required_numeric_completion_available(self.task, self.task['host_result'], views=self.views))

    def test_unregistered_policy_values_do_not_enable_feedback(self):
        for version in (True, 0, 2, '1'):
            with self.subTest(version=version), self.assertRaises(ContractError): enabled({POLICY: version})

    def test_unknown_wire_fields_still_fail_without_creating_feedback(self):
        wire = self.wire(); wire['source_approval'] = True
        with self.assertRaises(ContractError): self.decode(wire)

    def test_model_boundary_keeps_feedback_in_receipt_not_business_evidence(self):
        from researchhelper.model import ModelService
        from researchhelper.reasoning import evidence_contract, model_object
        from researchhelper.planning import question_specs
        wire = self.wire()
        class Selector:
            supports_structured_analysis = staticmethod(lambda: True)
            input_budget = staticmethod(lambda: ModelService.documented_input_budget(dict(
                endpoint='https://api.deepseek.com/responses', model='deepseek-flash')))
            def reason_structured_original_spans(model, task, messages, views):
                return json.dumps(decode_original_spans(wire, original_span_output_schema(task, views), (task, views)))
        payload = dict(question=self.task['question'], intent=self.task['intent'], objects=self.task['objects'],
            cutoff=self.task['cutoff'], questions=question_specs(self.task), calculations=[],
            evidence_contract=evidence_contract(self.task, self.views), field_registry={}, untrusted_sources=self.views,
            source_reading=None)
        value, receipt = model_object(Selector(), self.task, 'extract', payload, {'evidence_specs'})
        self.assertEqual(value, dict(evidence_specs=[])); self.assertIn('original_requirement_feedback', receipt)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 3)

    def test_feedback_survives_archive_roundtrip_without_rebinding(self):
        specs = self.bind(self.decode()); before = completion_scope(self.task, specs, self.views)
        with tempfile.TemporaryDirectory(prefix='feedback-archive-', dir=ROOT/'result/acceptance') as folder:
            archive = ResearchArchive(folder)
            inserted = copy.deepcopy(self.task); inserted['revision'] = 0  # New private store insertion only.
            archive.save(inserted); restored = archive.get(inserted['id'])
        self.assertEqual(completion_scope(restored, restored['host_result']['evidence_specs'], self.views), before)

    def test_new_policy_is_bound_and_frozen_without_reopening_old_studies(self):
        from researchhelper.planning import input_fingerprint
        from researchhelper.reports import compose
        resources = Path(os.environ.get('RESEARCHHELPER_EXTRACTION_FEEDBACK_RESOURCES', str(ROOT)))
        with tempfile.TemporaryDirectory(prefix='feedback-policy-', dir=ROOT/'result/acceptance') as folder:
            agent = ResearchAgent(ResearchArchive(folder), resources, offline=True, require_argument_support=True)
            try:
                task = agent.create('解释已读材料和缺失数据的区别', research_mode='current')
                self.assertEqual(task[POLICY], 1)
                old = copy.deepcopy(task); old.pop(POLICY)
                self.assertNotEqual(input_fingerprint(old), input_fingerprint(task))
                agent._adopt_method_knowledge(task); task['status'] = '部分完成'
                agent._gap(task, '本地反馈控制，不是实际研究', category='question_coverage')
                self.assertEqual(compose(task)[POLICY], 1)
            finally: agent.close_runtime()


if __name__ == '__main__': unittest.main()
