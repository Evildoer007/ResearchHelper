"""Offline date-role controls; never a recovered model response or live study."""
import base64
import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.contracts import ContractError, canonical, fingerprint, validate_evidence
from researchhelper.data_requirements import count_satisfied, requirement_slots, satisfies
from researchhelper.document_dates import POLICY, ROLE, _declared_day, anchors, enabled
from researchhelper.evidence import candidate_evidence
from researchhelper.output_schema import (ORIGINAL_SELECTION_REJECTIONS, decode_original_spans,
                                         original_span_output_schema, original_span_prompt)
from researchhelper.planning import input_fingerprint

ROOT = Path(__file__).resolve().parents[1]


def prepare_fixture():
    """Development-only assembly from unchanged retained bytes and normal gates."""
    from tests.test_macro_metadata_adoption import MacroMetadataAdoptionTests
    from researchhelper.official_sources import catalogue_url
    from researchhelper.reasoning import extractable_source_views
    folder = ROOT/'result/acceptance/one-sentence-research-sd6z544y'
    protected = {name: hashlib.sha256((folder/name).read_bytes()).hexdigest()
                 for name in ('receipt.json', 'task.json', 'report.json', 'report.html')}
    actual = json.loads((folder/'task.json').read_text())
    MacroMetadataAdoptionTests.setUpClass()
    control = MacroMetadataAdoptionTests()
    control.retained, control.pages = actual, {}
    for source in actual['source_records']:
        url = (catalogue_url(source['parameters']['page']) if source['operation'] == 'release_catalog'
               else source['snapshot']['official_origin']['url'])
        control.pages[url] = base64.b64decode(source['snapshot']['original_base64'], validate=True)
    try:
        control.setUp()
        result = dict(task=control.task, views=extractable_source_views(control.task), source=control.source,
                      retained_original_hashes=protected)
    finally:
        control.doCleanups()
    assert protected == {name: hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in protected}
    return result


def literal(schema):
    if 'anyOf' in schema: return literal(schema['anyOf'][0])
    if 'enum' in schema: return schema['enum'][0]
    if schema['type'] == 'object': return {key: literal(value) for key, value in schema['properties'].items()}
    if schema['type'] == 'array': return []
    if schema['type'] == 'string': return 'offline_date_control'
    raise AssertionError('No invented field or reference')


class DocumentDateRoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        supplied = os.environ.get('RESEARCHHELPER_DOCUMENT_DATE_FIXTURE')
        cls.fixture = json.loads(gzip.decompress(Path(supplied).read_bytes())) if supplied else prepare_fixture()

    def setUp(self):
        network = patch('socket.socket.connect', side_effect=AssertionError('Offline date control only'))
        network.start(); self.addCleanup(network.stop)
        if os.environ.get('RESEARCHHELPER_FROZEN_TEST_EXECUTABLE'):
            from frozen_archive_test_loader import frozen_pdf_worker
            from researchhelper.material_parser import MaterialParser
            worker = patch.object(MaterialParser, 'worker_target', staticmethod(frozen_pdf_worker))
            worker.start(); self.addCleanup(worker.stop)
        self.task, self.views, self.source = (copy.deepcopy(self.fixture[key]) for key in ('task', 'views', 'source'))
        self.schema = original_span_output_schema(self.task, self.views)
        self.slots = requirement_slots(self.task)
        self.slot = self.slots[0]

    def branches(self, key='requirement_1', schema=None):
        field = (schema or self.schema)['properties']['requirements']['properties'][key]
        found = next((branch for branch in field['anyOf'] if branch['properties']['status']['enum'] == ['found']), None)
        return found['properties']['items']['items']['anyOf'] if found else []

    def wire(self, item=None):
        wire = dict(version=12, binding_ref=self.schema['properties']['binding_ref']['enum'][0], selections=[],
                    requirements={slot['key']: dict(status='not_found', reason='离线控制，不认定原件无内容') for slot in self.slots})
        branch = next(branch for branch in self.branches() if 'date_role' in branch['properties'])
        wire['requirements'][self.slot['key']] = dict(status='found', items=[literal(branch) if item is None else item])
        return wire

    def decode(self, wire=None):
        return decode_original_spans(self.wire() if wire is None else wire, self.schema, (self.task, self.views))

    def spec(self):
        result = self.decode()
        self.assertNotIn(ORIGINAL_SELECTION_REJECTIONS, result)
        self.assertEqual(len(result['evidence_specs']), 1)
        return result['evidence_specs'][0]

    def test_actual_meta_date_can_pass_source_and_applicability_without_first_publication_claim(self):
        before = canonical((self.task, self.views))
        spec = self.spec()
        evidence = candidate_evidence(self.task, self.source, spec)
        ResearchAnalyzer().assess(self.task, evidence, spec['review'], self.task['source_records'])
        validate_evidence(evidence, self.task)
        self.assertEqual(evidence['period'], '2026-09-30')
        self.assertEqual(evidence['date_role'], ROLE)
        self.assertIn('2026/09/30 09:30', evidence['text'])
        self.assertEqual(evidence['unit'], '不适用')
        self.assertIsNone(evidence['published_at'])
        self.assertEqual(evidence['publication']['certainty'], '未知')
        self.assertNotIn('observation_date', evidence)
        self.assertNotIn('observation_period', evidence)
        self.assertTrue(count_satisfied(self.task['questions'][0], self.slot['text'], [evidence]))
        self.assertFalse(satisfies(evidence, self.slots[1]['constraints']))
        self.assertEqual(canonical((self.task, self.views)), before)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 0)
        self.assertFalse(self.task['evidence'] or self.task['theses'])

    def test_all_other_eight_needs_keep_their_original_monthly_branches(self):
        self.assertEqual(len(self.slots), 9)
        for slot in self.slots[1:]:
            branches = self.branches(slot['key'])
            self.assertTrue(branches)
            self.assertTrue(all('date_role' not in branch['properties'] for branch in branches))
            self.assertEqual(slot['constraints']['period_kind'], 'month')

    def test_legacy_historical_and_document_only_tasks_do_not_gain_page_date_role(self):
        for mode in ('legacy', 'historical', 'document_content'):
            task = copy.deepcopy(self.task)
            if mode == 'legacy': task.pop(POLICY)
            elif mode == 'historical': task['research_mode'] = 'historical'
            else: task['intent'] = '原件内容核对'
            self.assertFalse(enabled(task))
            self.assertEqual(anchors(task, self.source), [])
        legacy = copy.deepcopy(self.task); legacy.pop(POLICY); legacy['input_hash'] = input_fingerprint(legacy)
        schema = original_span_output_schema(legacy, self.views)
        self.assertEqual(self.branches(schema=schema), [])
        self.assertNotEqual(input_fingerprint(legacy), input_fingerprint(self.task))

    def test_bad_policy_types_are_not_enabled(self):
        for value in (True, 0, 2, '1'):
            task = copy.deepcopy(self.task); task[POLICY] = value
            with self.subTest(value=value), self.assertRaises(ContractError): enabled(task)

    def test_forged_observation_numeric_calculation_and_publication_fields_are_rejected(self):
        baseline = self.spec()
        changes = [dict(date_role='observation'), dict(period='2026-10-01'),
                   dict(observation_date='2026-09-30'), dict(observation_period={'frequency': 'month', 'value': '2026-09'}),
                   dict(value=2026), dict(raw_value=2026), dict(raw_unit='%'), dict(calculation_context={}),
                   dict(reporting_period_basis={}), dict(nature='研究推算'), dict(unit='%'),
                   dict(published_at='2026-09-30'), dict(availability=None)]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ContractError):
                candidate_evidence(self.task, self.source, dict(copy.deepcopy(baseline), **change))

    def test_forged_text_locator_foreign_or_clipped_tag_cannot_pass_source_check(self):
        for change in ('text', 'part', 'period_part', 'period_quote', 'foreign'):
            spec = self.spec()
            if change == 'text': spec['text'] = '2026/09/30'
            elif change == 'foreign': spec['availability']['artifact_id'] = 'foreign'
            else: spec['record_locator'][change] = 'forged'
            with self.subTest(change=change), self.assertRaises(ContractError):
                candidate_evidence(self.task, self.source, spec)

    def test_wrong_temporal_reference_or_authority_cannot_produce_a_fact(self):
        for change in ('reference', 'role', 'number', 'text', 'part', 'authority'):
            wire = self.wire(); item = wire['requirements'][self.slot['key']]['items'][0]
            if change == 'reference': item['temporal_reference']['document_date_ref'] = 'foreign'
            elif change == 'role': item['date_role'] = 'first_publication'
            elif change == 'number': item['kind'] = 'number'
            elif change == 'text': item['text_reference'] = {'line': 0}
            elif change == 'part': item['text_reference']['part'] = '标题1'
            else: item['published_at'] = '2026-09-30'
            with self.subTest(change=change):
                try:
                    result = self.decode(wire)
                except ContractError:
                    continue  # Authority/field-shape violations may reject the envelope.
                self.assertFalse(result['evidence_specs'])
                self.assertFalse(result[ORIGINAL_SELECTION_REJECTIONS]['evidence_approved'])

    def test_only_complete_once_read_metadata_is_offered(self):
        view = next(view for view in self.views if view['id'] == self.source['id'])
        actual = anchors(self.task, self.source, view['parts'])
        self.assertEqual(len(actual), 1)
        part = next(part for part in view['parts'] if part['locator'] == actual[0]['part'])
        self.assertEqual(anchors(self.task, self.source, []), [])
        self.assertEqual(anchors(self.task, self.source, [dict(part, text='2026/09/30')]), [])
        self.assertEqual(anchors(self.task, self.source, [part, part]), [])
        task = copy.deepcopy(self.task); task['cutoff'] = '2026-09-01T23:59:59+08:00'
        self.assertEqual(anchors(task, self.source, view['parts']), [])

    def test_only_real_unambiguous_publication_meta_tags_are_date_candidates(self):
        for text in ('<meta name="PubDate" content="2026/09/30 09:30">',
                     '<meta name="PubDate" content="2026-09-30T09:30:00+08:00"/>'):
            self.assertEqual(_declared_day(dict(metadata_field='PubDate', text=text)), '2026-09-30')
        for text in ('2026/09/30', '<meta name="update" content="2026/09/30">',
                     '<meta name="PubDate" content="2026/02/30">',
                     '<meta name="PubDate" content="2026/09/30 99:99">',
                     '<meta name="PubDate" content="2026/09/30 09:30+25:00">',
                     '<meta name="PubDate" name="PubDate" content="2026/09/30">',
                     '<meta name="PubDate" content="2026/09/30"><!--extra-->',
                     '<meta name="PubDate" content="2026/09/30"></meta>',
                     '<div name="PubDate" content="2026/09/30">'):
            with self.subTest(text=text): self.assertIsNone(_declared_day(dict(metadata_field='PubDate', text=text)))

    def test_duplicate_meta_tags_do_not_count_as_multiple_observations(self):
        spec = self.spec(); item = candidate_evidence(self.task, self.source, spec)
        ResearchAnalyzer().assess(self.task, item, spec['review'], self.task['source_records'])
        question = copy.deepcopy(self.task['questions'][0])
        question['evidence_need_constraints'][self.slot['text']]['minimum_count'] = 2
        duplicate = copy.deepcopy(item); duplicate['id'] += '_duplicate'
        duplicate['record_locator']['part'] += '_duplicate'
        self.assertFalse(count_satisfied(question, self.slot['text'], [item, duplicate]))

    def test_prompt_exposes_explicit_date_references_without_relabeling_them_as_observations(self):
        from researchhelper.reasoning import evidence_contract
        from researchhelper.planning import question_specs
        contract = evidence_contract(self.task, self.views)
        payload = dict(question=self.task['question'], intent=self.task['intent'], objects=self.task['objects'],
            cutoff=self.task['cutoff'], questions=question_specs(self.task), calculations=self.task['calculation_specs'],
            untrusted_sources=self.views, evidence_contract=contract, field_registry={}, source_reading=None)
        before = fingerprint(payload)
        prompt = original_span_prompt(payload, self.task)
        references = [row for view in prompt['untrusted_sources'] for row in view.get('document_date_choices', [])]
        self.assertEqual(len(references), 1)
        self.assertEqual(references[0]['period'], '2026-09-30')
        actual_ref = self.wire()['requirements'][self.slot['key']]['items'][0]['temporal_reference']['document_date_ref']
        self.assertEqual(references[0]['reference'], actual_ref)
        observations = [row for view in prompt['untrusted_sources'] for row in view.get('observation_choices', [])]
        self.assertFalse(any(row['period'] == references[0]['period'] for row in observations))
        self.assertTrue(any(source.get('document_date_candidates') for source in contract['sources']))
        self.assertEqual(fingerprint(payload), before)

    def test_tampered_retained_metadata_never_becomes_a_date_anchor(self):
        source = copy.deepcopy(self.source)
        part = next(part for part in source['snapshot']['parts'] if part.get('metadata_field') == 'PubDate')
        part['text'] = part['text'].replace('2026/09/30', '2026/09/29')
        task = copy.deepcopy(self.task)
        task['source_records'] = [source if row['id'] == source['id'] else row for row in task['source_records']]
        try:
            result = anchors(task, source)
        except ContractError:
            return
        self.assertEqual(result, [])

    def test_conflicting_declared_calendar_dates_have_no_single_candidate(self):
        # Unit control for ambiguity only. This is not an acquired original.
        parts = [dict(locator='synthetic_' + str(index), metadata_field='PubDate',
                      text='<meta name="PubDate" content="' + day + '">')
                 for index, day in enumerate(('2026/09/29', '2026/09/30'))]
        with patch('researchhelper.material_parser.verified_material', return_value={'parts': parts}):
            self.assertEqual(anchors(self.task, self.source, parts), [])

    def test_new_policy_is_in_frozen_content_and_does_not_change_old_task_identity(self):
        from researchhelper.archive import ResearchArchive
        from researchhelper.engine import ResearchAgent
        from researchhelper.reports import compose
        folder = tempfile.TemporaryDirectory(prefix='date-role-policy-', dir=ROOT/'result/acceptance')
        self.addCleanup(folder.cleanup)
        agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True, require_argument_support=True)
        self.addCleanup(agent.close_runtime)
        task = agent.create('解释原页日期与统计所属月的区别', research_mode='current')
        self.assertEqual(task[POLICY], 1)
        agent._adopt_method_knowledge(task)
        task['status'] = '部分完成'
        agent._gap(task, '离线日期规则核验，不是一次研究', category='question_coverage')
        self.assertEqual(compose(task)[POLICY], 1)
        previous = copy.deepcopy(task); previous.pop(POLICY)
        self.assertNotEqual(input_fingerprint(previous), input_fingerprint(task))


if __name__ == '__main__': unittest.main()
