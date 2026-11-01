"""Declared period goals against retained originals, with no real API calls.

These are fresh private controls, not another attempt at the closed study.
The plan annotations and navigation decisions are authored test inputs, not
claims about a model's ability to plan or produce an accepted client report.
"""
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
from researchhelper.contracts import ContractError, canonical, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.period_targets import FIELD, POLICY, FAMILY, enabled, normalized, resolve, validate_plan
from researchhelper.planning import input_fingerprint, normalize_questions, question_specs, validate_question_revision
from researchhelper.acquisition import initial_acquisition_coverage, complete_initial_acquisition
from researchhelper.output_schema import original_span_output_schema, _original_span_choices, original_observation_choices
from researchhelper.reasoning import extractable_source_views
from researchhelper.data_requirements import validate_links, requirement_slots

ROOT = Path(__file__).resolve().parents[1]
STUDIES = ('one-sentence-research-6nm7u50s', 'one-sentence-research-m4v02ejg')


def target(relation, period=None):
    return dict(family=FAMILY, relation=relation, period=period)


def prepare_fixture():
    from researchhelper.evidence import EvidenceCollector
    from researchhelper.official_sources import catalogue_url, selection_references
    protected, retained, pages = {}, [], {}
    for name in STUDIES:
        location = ROOT/'result/acceptance'/name
        protected[name] = {part: hashlib.sha256((location/part).read_bytes()).hexdigest()
                           for part in ('receipt.json', 'task.json', 'report.json', 'report.html')}
        retained.append(json.loads((location/'task.json').read_text()))
    for old in reversed(retained):
        for record in old['source_records']:
            url = (catalogue_url(record['parameters']['page']) if record['operation'] == 'release_catalog'
                   else record['snapshot']['official_origin']['url'])
            pages[url] = base64.b64decode(record['snapshot']['original_base64'], validate=True)
    def transport(task, url, started, timeout, event, **kwargs):
        event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True)
        return pages[url]
    with tempfile.TemporaryDirectory(prefix='period-goal-fixture-', dir=ROOT/'result/acceptance') as folder:
        agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
        try:
            task = agent.create(retained[0]['question'], entry='宏观', research_mode='current')
            questions = question_specs(retained[0])
            numeric = [text for text, spec in questions[0]['evidence_need_constraints'].items() if spec['value_kind'] == 'number']
            assert len(numeric) == 6
            questions[0][FIELD] = {text: target('latest' if index < 3 else 'previous') for index, text in enumerate(numeric)}
            task = agent.revise_plan(task['id'], questions, '离线期间目标控制，不重开旧研究', task['input_hash'])
            collector = EvidenceCollector(offline=False)
            agent._adopt_method_knowledge(task)
            def collect(operation, parameters):
                record = collector.external(task, 'official', operation, parameters, {})
                record['question_ids'] = ['q1']
                task['source_records'].append(record)
            with (patch('socket.socket.connect', side_effect=AssertionError('No real calls')),
                  patch('researchhelper.source_transport.official_html', side_effect=transport)):
                for page in ('0', '1'):
                    collect('release_catalog', dict(channel='nbs_release', page=page))
                titles = [record['snapshot']['official_origin']['title'] for record in retained[0]['source_records']
                          if record['operation'] == 'release_original']
                for title in titles:
                    choice = next(row for row in selection_references(task) if row['title'] == title)
                    collect('release_original', dict(selection_ref=choice['selection_ref']))
                initial = copy.deepcopy(task)
                for page in ('2', '3'):
                    collect('release_catalog', dict(channel='nbs_release', page=page))
                august = next(row for row in selection_references(task) if row['title'] == '2026年8月中国采购经理指数运行情况')
                collect('release_original', dict(selection_ref=august['selection_ref']))
                full = copy.deepcopy(task)
        finally: agent.close_runtime()
    for name, hashes in protected.items():
        assert hashes == {part: hashlib.sha256((ROOT/'result/acceptance'/name/part).read_bytes()).hexdigest() for part in hashes}
    return dict(initial=initial, full=full, protected=protected,
        pages={url: base64.b64encode(payload).decode() for url, payload in pages.items()},
        real_model_calls=0, real_source_calls=0, old_studies_reopened=False, model_quality_accepted=False)


class SourcePeriodTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        supplied = os.environ.get('RESEARCHHELPER_PERIOD_TARGET_FIXTURE')
        cls.fixture = json.loads(gzip.decompress(Path(supplied).read_bytes())) if supplied else prepare_fixture()

    def setUp(self):
        guard = patch('socket.socket.connect', side_effect=AssertionError('No model, provider or public network'))
        guard.start(); self.addCleanup(guard.stop)
        if os.environ.get('RESEARCHHELPER_FROZEN_TEST_EXECUTABLE'):
            from frozen_archive_test_loader import frozen_pdf_worker
            from researchhelper.material_parser import MaterialParser
            worker = patch.object(MaterialParser, 'worker_target', staticmethod(frozen_pdf_worker))
            worker.start(); self.addCleanup(worker.stop)
        self.task = copy.deepcopy(self.fixture['initial'])
        self.texts = list(self.task['questions'][0][FIELD])

    def test_current_and_same_month_commentary_do_not_complete_previous_month(self):
        coverage = initial_acquisition_coverage(self.task)
        self.assertFalse(coverage['ready'])
        rows = coverage['source_period_targets']
        self.assertEqual([row['resolved_period'] for row in rows], ['2026-09'] * 3 + ['2026-08'] * 3)
        self.assertTrue(all(row['source_path_ready'] for row in rows[:3]))
        self.assertTrue(all(not row['source_path_ready'] for row in rows[3:]))
        self.assertEqual(coverage['pending_original_question_ids'], ['q1'])
        self.assertFalse(self.task['evidence'])

    def test_actual_previous_primary_completes_source_path_but_approves_no_values(self):
        task = copy.deepcopy(self.fixture['full']); before = canonical(task)
        coverage = initial_acquisition_coverage(task)
        self.assertTrue(coverage['ready'])
        self.assertTrue(all(row['source_path_ready'] for row in coverage['source_period_targets']))
        self.assertTrue(all(row['financial_approval'] is False and row['numeric_values_approved'] is False
                            for row in coverage['source_period_targets']))
        self.assertFalse(task['evidence']); self.assertEqual(canonical(task), before)

    def test_unannotated_and_legacy_tasks_keep_ordinary_source_behavior(self):
        self.task['questions'][0].pop(FIELD)
        before = canonical(self.task)
        self.assertTrue(initial_acquisition_coverage(self.task)['ready'])
        self.assertEqual(canonical(self.task), before)
        self.task.pop(POLICY)
        self.assertFalse(enabled(self.task)); self.assertFalse(resolve(self.task))

    def test_missing_optional_period_does_not_block_independent_required_goals(self):
        question = copy.deepcopy(self.task['questions'][0]); question.update(id='optional', required=False)
        self.task['questions'][0][FIELD] = {text: target('latest') for text in self.texts[:3]}
        question[FIELD] = {text: target('previous') for text in self.texts[3:]}
        self.task['questions'].append(question)
        coverage = initial_acquisition_coverage(self.task)
        self.assertTrue(coverage['ready']); self.assertFalse(coverage['pending_original_question_ids'])

    def test_exact_month_is_explicit_not_inferred_from_need_words(self):
        self.task['questions'][0][FIELD] = {self.texts[0]: target('exact', '2026-08')}
        row = resolve(self.task)[0]
        self.assertEqual(row['resolved_period'], '2026-08'); self.assertIsNone(row['anchor_period'])

    def test_future_invalid_and_text_period_goals_are_rejected(self):
        for value in (target('exact', '2026-13'), target('previous', '2026-08'),
                      dict(family='invented', relation='latest', period=None)):
            question = copy.deepcopy(self.task['questions'][0]); question[FIELD] = {self.texts[0]: value}
            with self.assertRaises(ContractError): normalized(question)
        proposed = question_specs(self.task); proposed[0][FIELD][self.texts[0]] = target('exact', self.task['cutoff'][:7])
        with self.assertRaises(ContractError): validate_plan(self.task, proposed)
        text_need = next(text for text, spec in proposed[0]['evidence_need_constraints'].items() if spec['value_kind'] == 'text')
        proposed[0][FIELD] = {text_need: target('latest')}
        with self.assertRaises(ContractError): normalized(proposed[0])

    def test_target_cannot_be_removed_or_changed_to_suit_obtained_data(self):
        for action in ('remove', 'change'):
            proposed = question_specs(self.task)
            if action == 'remove': proposed[0][FIELD].pop(self.texts[3])
            else: proposed[0][FIELD][self.texts[3]] = target('latest')
            with self.assertRaises(ContractError): validate_question_revision(self.task, proposed)

    def test_normalization_and_input_hash_preserve_period_declarations(self):
        questions = normalize_questions(question_specs(self.task), self.task['question'], self.task['intent'])
        self.assertEqual(questions[0][FIELD], self.task['questions'][0][FIELD])
        before = input_fingerprint(self.task)
        self.task['questions'][0][FIELD][self.texts[3]] = target('latest')
        self.assertNotEqual(input_fingerprint(self.task), before)

    def test_changed_or_cross_task_catalog_does_not_resolve_month(self):
        for mutation in ('hash', 'task'):
            task = copy.deepcopy(self.task)
            for record in task['source_records']:
                if record.get('operation') == 'release_catalog':
                    if mutation == 'hash': record['hash'] = 'changed'
                    else: record['task_id'] = 'unrelated_task'
            self.assertTrue(all(row['resolved_period'] is None for row in resolve(task)))
            self.assertFalse(initial_acquisition_coverage(task)['ready'])

    def test_calendar_previous_is_not_previous_available_release_and_handles_january(self):
        from researchhelper.period_targets import _previous
        self.assertEqual(_previous('2026-01'), '2025-12')
        self.task['questions'][0][FIELD] = {self.texts[0]: target('previous')}
        with patch('researchhelper.period_targets._previous', wraps=_previous) as previous:
            self.assertEqual(resolve(self.task)[0]['resolved_period'], '2026-08')
            previous.assert_called_once_with('2026-09')

    def test_association_rejects_current_month_for_previous_need(self):
        links = {'q1': [fingerprint(self.texts[3])]}
        item = dict(value=49.4, unit='%', period='2026-09')
        with self.assertRaises(ContractError): validate_links(self.task, item, links)
        item['period'] = '2026-08'; validate_links(self.task, item, links)

    def test_unknown_policy_and_unproven_original_do_not_gain_readiness(self):
        for version in (True, 0, 2, '1'):
            with self.assertRaises(ContractError): enabled({POLICY: version})
        task = copy.deepcopy(self.fixture['full'])
        for record in task['source_records']:
            if record.get('operation') == 'release_original': record.pop('acquisition', None)
        self.assertFalse(initial_acquisition_coverage(task)['ready'])
        self.assertTrue(all(not row['source_path_ready'] for row in initial_acquisition_coverage(task)['source_period_targets']))

    def test_selector_offers_only_target_observation_month(self):
        task = copy.deepcopy(self.fixture['full']); views = extractable_source_views(task)
        schema = original_span_output_schema(task, views)
        choices = {choice['source_ref']: choice for choice in _original_span_choices(task, views)}
        expected = {(row['question_id'], row['text']): row['resolved_period'] for row in resolve(task)}
        for slot in requirement_slots(task):
            if (slot['question_id'], slot['text']) not in expected: continue
            periods = set()
            row = schema['properties']['requirements']['properties'][slot['key']]
            for found in row['anyOf']:
                if found['properties']['status']['enum'] != ['found']: continue
                for branch in found['properties']['items']['items']['anyOf']:
                    props = branch['properties']; choice = choices[props['source_ref']['enum'][0]]
                    observed = {item['reference']: item['period'] for item in original_observation_choices(choice)}
                    for temporal in props['temporal_reference']['anyOf']:
                        if 'observation_ref' in temporal['properties']:
                            periods.update(observed[ref] for ref in temporal['properties']['observation_ref']['enum'])
            self.assertEqual(periods, {expected[(slot['question_id'], slot['text'])]})

    def test_no_previous_candidate_leaves_not_found_without_inventing_reference(self):
        schema = original_span_output_schema(self.task, extractable_source_views(self.task))
        # A source may contain another month's literal anchors, but the schema
        # must never offer an arbitrary current-month anchor for a prior goal.
        row = next(row for row in resolve(self.task) if row['target']['relation'] == 'previous')
        self.assertEqual(row['candidates'], [])
        self.assertIn('requirements', schema['properties'])

    def test_new_policy_is_frozen_in_task_report_and_read_only_archive(self):
        from researchhelper.reports import compose
        self.assertEqual(self.task[POLICY], 1)
        self.task['status'] = '部分完成'
        ResearchAgent._gap(self.task, '离线期间目标控制，不是实际研究', category='question_coverage')
        content = compose(self.task)
        self.assertEqual(content[POLICY], 1)
        self.assertEqual(content['questions'][0][FIELD], self.task['questions'][0][FIELD])
        with tempfile.TemporaryDirectory(prefix='period-archive-') as folder:
            archive = ResearchArchive(folder); fresh = copy.deepcopy(self.task); fresh['revision'] = 0
            archive.save(fresh)
            restored = archive.get(fresh['id'])
            self.assertEqual(restored['questions'][0][FIELD], fresh['questions'][0][FIELD])

    def test_navigation_acquires_missing_catalogue_and_primary_within_existing_budget(self):
        from researchhelper import acquisition
        from researchhelper.contracts import charge
        from researchhelper.evidence import EvidenceCollector
        task = self.task; task.update(run_mode='model', status='运行中', delivery=None)
        task['ledger']['used'].update(model_calls=3, seconds=50)
        task.pop('reasoning_state', None)
        payloads = []; collector = EvidenceCollector(offline=False)
        def model(model, active, phase, payload, fields):
            payloads.append(copy.deepcopy(payload)); charge(active['ledger'], 'model_calls')
            goals = payload['source_readiness']['source_period_targets']
            missing = next(row for row in goals if not row['source_path_ready'])
            if not missing['candidates']:
                needs = [dict(id='catalog_'+page, source='official', operation='release_catalog',
                    parameters=dict(channel='nbs_release', page=page), question_ids=['q1'],
                    selection_reason='当前及上期统计口径须核对统计局已登记原件') for page in ('2', '3')]
            else:
                selected = missing['candidates'][0]
                needs = [dict(id='previous_original', source='official', operation='release_original',
                    parameters=dict(selection_ref=selected['selection_ref']), question_ids=['q1'],
                    selection_reason='上期数值须读取对应观测月原件，不能用同月解读替代')]
            return dict(data_requests=needs, stop_reason=''), dict(acquisition_rules_version=3, mocked_model=True)
        def acquire(active, needs, **kwargs):
            for need in needs:
                record = collector.external(active, need['source'], need['operation'], need['parameters'], {})
                record['question_ids'] = need['question_ids']
                active['source_records'].append(record)
        def transport(active_task, url, started, timeout, event, **kwargs):
            event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True)
            return base64.b64decode(self.fixture['pages'][url], validate=True)
        engine = SimpleNamespace(offline=False, model=None, source_credentials={},
            _active_supplement=lambda active: None, _active=lambda *args: task,
            _save_model_checkpoint=Mock(), _reasoning_available=lambda active: True,
            _validated_data_needs=Mock(), _acquire_sources=Mock(side_effect=acquire), _gap=Mock())
        with (patch.object(acquisition, 'model_object', side_effect=model),
              patch('researchhelper.source_transport.official_html', side_effect=transport)):
            complete_initial_acquisition(engine, task)
        self.assertEqual(task['reasoning_state']['acquisition']['status'], 'complete',
                         task['reasoning_state']['acquisition'].get('stop_reason'))
        self.assertEqual(len(payloads), 2)
        self.assertEqual(task['ledger']['used']['model_calls'], 5)
        self.assertEqual(task['ledger']['used']['data_requests'], 7)
        self.assertEqual(task['ledger']['limits']['model_calls'], 8)
        self.assertFalse(task['evidence']); self.assertEqual(task['ledger']['supplements'], 0)
        self.assertFalse(engine._gap.called)

    def test_insufficient_budget_keeps_missing_period_without_dispatch(self):
        task = self.task; task.update(run_mode='model', status='运行中')
        task['ledger']['used']['model_calls'] = 6
        engine = SimpleNamespace(offline=False, model=None, source_credentials={},
            _active_supplement=lambda active: None, _save_model_checkpoint=Mock(), _gap=Mock(),
            _acquire_sources=Mock(), _reasoning_available=lambda active: False)
        with patch('researchhelper.acquisition.model_object') as model:
            complete_initial_acquisition(engine, task)
        model.assert_not_called(); engine._acquire_sources.assert_not_called()
        self.assertFalse(initial_acquisition_coverage(task)['ready'])
        self.assertEqual(task['ledger']['used']['model_calls'], 6)

    def test_strict_planning_preserves_target_and_keeps_ordinary_needs_compact(self):
        from researchhelper.output_schema import planning_output_schema, decode_planning_output, _matches
        def sample(schema):
            if 'anyOf' in schema: return sample(schema['anyOf'][0])
            if 'enum' in schema: return schema['enum'][0]
            kind = schema['type']
            if kind == 'object': return {key: sample(value) for key, value in schema['properties'].items()}
            if kind == 'array': return []
            if kind == 'string': return 'period_control'
            if kind == 'boolean': return True
            if kind in ('integer', 'number'): return schema.get('minimum', 1)
            if kind == 'null': return None
            raise AssertionError('No invented planning shape')
        schema = planning_output_schema(self.task); wire = sample(schema)
        ordinary = dict(text=self.texts[3], **self.task['questions'][0]['evidence_need_constraints'][self.texts[3]])
        annotated = dict(ordinary, period_target=target('previous'))
        question_schema = (schema['properties']['main_question'] if 'main_question' in schema['properties']
                           else schema['properties']['questions']['items'])
        need_schema = question_schema['anyOf'][0]['properties']['evidence_needs']['items']
        self.assertTrue(_matches(ordinary, need_schema)); self.assertTrue(_matches(annotated, need_schema))
        question = sample(question_schema); question['evidence_needs'] = [annotated]
        if 'main_question' in wire: wire['main_question'] = question
        else: wire['questions'] = [question]
        result = decode_planning_output(wire, schema)
        question = result['questions'][0]
        self.assertEqual(question[FIELD], {self.texts[3]: target('previous')})
        self.assertEqual(question['evidence_need_constraints'][self.texts[3]],
                         self.task['questions'][0]['evidence_need_constraints'][self.texts[3]])

    def test_fresh_actual_resource_policy_and_host_guidance_do_not_require_second_model(self):
        from researchhelper.planning import question_contract
        resources = Path(os.environ.get('RESEARCHHELPER_PERIOD_TARGET_RESOURCES', str(ROOT)))
        with tempfile.TemporaryDirectory(prefix='period-policy-') as folder:
            agent = ResearchAgent(ResearchArchive(folder), resources, offline=True, require_argument_support=True)
            try:
                task = agent.create('核对当前和上期统计观测', entry='宏观', research_mode='current')
                self.assertEqual(task[POLICY], 1)
                self.assertIn('source_period_targets', question_contract(task))
                before = input_fingerprint(task); old = copy.deepcopy(task); old.pop(POLICY)
                self.assertNotEqual(before, input_fingerprint(old))
                self.assertIsNone(agent.model)
                self.assertEqual(task['ledger']['used']['model_calls'], 0)
                question = dict(id='q1', question='核对明确月份', kind='fact', required=True,
                    evidence_needs=['明确月度指标'], method_needs=[], dependencies=[],
                    evidence_need_constraints={'明确月度指标': dict(value_kind='number', unit_dimension='percentage', minimum_count=1, period_kind='month')},
                    evidence_period_targets={'明确月度指标': target('exact', task['cutoff'][:7])})
                with self.assertRaises(ContractError):
                    agent.create('核对明确月份', entry='宏观', research_mode='current', questions=[question])
            finally: agent.close_runtime()


if __name__ == '__main__':
    unittest.main()
