"""Compact citation selection against retained originals, never a live study.

The private task is new and transport is mocked with unchanged closed-study
bytes. These controls do not recover the rejected R129 model selector.
"""
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
from researchhelper.contracts import ContractError, canonical, validate_evidence
from researchhelper.data_requirements import count_satisfied, requirement_slots, satisfies
from researchhelper.evidence import candidate_evidence, evidence_binding
from researchhelper.output_schema import (ORIGINAL_SELECTION_REJECTIONS, decode_original_spans,
    original_span_output_schema, original_span_prompt)
from researchhelper.planning import input_fingerprint
from researchhelper.source_labels import POLICY, ROLE, REFERENCE, SIGNATORIES, candidates, enabled

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT/'result/acceptance/one-sentence-research-m4v02ejg'


def prepare_fixture():
    from researchhelper.archive import ResearchArchive
    from researchhelper.engine import ResearchAgent
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
    with tempfile.TemporaryDirectory(prefix='source-label-fixture-', dir=ROOT/'result/acceptance') as folder:
        agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
        try:
            task = agent.create(retained['question'], entry='宏观', research_mode='current')
            task = agent.revise_plan(task['id'], question_specs(retained),
                '离线核对留存原页的来源标注，不执行模型研究', task['input_hash'])
            collector, remapped = EvidenceCollector(offline=False), {}
            with (patch('socket.socket.connect', side_effect=AssertionError('No network in fixture')),
                  patch('researchhelper.source_transport.official_html', side_effect=transport)):
                for record in retained['source_records']:
                    parameters = copy.deepcopy(record['parameters'])
                    if 'catalog_id' in parameters: parameters['catalog_id'] = remapped[parameters['catalog_id']]
                    acquired = collector.external(task, 'official', record['operation'], parameters, {})
                    task['source_records'].append(acquired)
                    remapped[record['id']] = acquired['id']
            source = next(record for record in task['source_records'] if record['operation'] == 'release_original'
                          and record['snapshot']['official_origin']['title'] == '2026年9月中国采购经理指数运行情况')
            result = dict(task=task, views=extractable_source_views(task), source=source, protected=protected,
                mocked_transport=True, original_study_resumed=False, real_provider_calls=0, model_calls=0)
        finally:
            agent.close_runtime()
    assert protected == {name: hashlib.sha256((STUDY/name).read_bytes()).hexdigest() for name in names}
    return result


def literal(schema):
    if 'anyOf' in schema: return literal(schema['anyOf'][0])
    if 'enum' in schema: return schema['enum'][0]
    if schema['type'] == 'object': return {key: literal(value) for key, value in schema['properties'].items()}
    if schema['type'] == 'array': return []
    if schema['type'] == 'string': return 'offline_source_label'
    raise AssertionError('No invented field or selector')


class SourceLabelSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        supplied = os.environ.get('RESEARCHHELPER_SOURCE_LABEL_FIXTURE')
        cls.fixture = json.loads(gzip.decompress(Path(supplied).read_bytes())) if supplied else prepare_fixture()

    def setUp(self):
        network = patch('socket.socket.connect', side_effect=AssertionError('Offline source-label controls only'))
        network.start(); self.addCleanup(network.stop)
        if os.environ.get('RESEARCHHELPER_FROZEN_TEST_EXECUTABLE'):
            from frozen_archive_test_loader import frozen_pdf_worker
            from researchhelper.material_parser import MaterialParser
            worker = patch.object(MaterialParser, 'worker_target', staticmethod(frozen_pdf_worker))
            worker.start(); self.addCleanup(worker.stop)
        self.task, self.views, self.source = (copy.deepcopy(self.fixture[key]) for key in ('task', 'views', 'source'))
        self.original = canonical((self.task, self.views))
        self.slots = requirement_slots(self.task)
        self.slot = next(slot for slot in self.slots if '用于来源标注' in slot['text'])
        self.schema = original_span_output_schema(self.task, self.views)

    def tearDown(self):
        self.assertEqual(canonical((self.task, self.views)), self.original)

    def branches(self, slot=None, schema=None):
        field = (schema or self.schema)['properties']['requirements']['properties'][(slot or self.slot)['key']]
        found = next((b for b in field['anyOf'] if b['properties']['status']['enum'] == ['found']), None)
        return found['properties']['items']['items']['anyOf'] if found else []

    def wire(self):
        wire = dict(version=12, binding_ref=self.schema['properties']['binding_ref']['enum'][0], selections=[],
            requirements={slot['key']: dict(status='not_found', reason='离线选择控制，不认定材料无内容') for slot in self.slots})
        branch = next(branch for branch in self.branches() if REFERENCE in branch['properties'])
        item = literal(branch)
        wire['requirements'][self.slot['key']] = dict(status='found', items=[item])
        return wire

    def spec(self, wire=None):
        result = decode_original_spans(self.wire() if wire is None else wire, self.schema, (self.task, self.views))
        self.assertNotIn(ORIGINAL_SELECTION_REJECTIONS, result)
        self.assertEqual(len(result['evidence_specs']), 1)
        return result['evidence_specs'][0]

    def assess(self):
        spec = self.spec(); item = candidate_evidence(self.task, self.source, spec)
        ResearchAnalyzer().assess(self.task, item, spec['review'], self.task['source_records'])
        validate_evidence(item, self.task)
        return item

    def test_complete_literal_properties_pass_existing_source_and_applicability_gates(self):
        item = self.assess(); label = item['source_label']
        self.assertEqual(label['title'], '2026年9月中国采购经理指数运行情况')
        self.assertEqual(label['site_label'], '国家统计局')
        self.assertEqual(tuple(row['quote'] for row in label['signatories']), SIGNATORIES)
        self.assertEqual(label['declared_date']['value'], '2026-09-30')
        self.assertEqual(item['text'], label['page_header']['quote'])
        self.assertEqual(item['date_role'], ROLE)
        self.assertIsNone(item['published_at'])
        self.assertEqual(item['publication']['certainty'], '未知')
        self.assertTrue(count_satisfied(self.task['questions'][0], self.slot['text'], [item]))
        self.assertFalse(self.task['evidence'] or self.task['theses'])
        self.assertEqual(self.task['ledger']['used']['model_calls'], 0)

    def test_wire_does_not_retype_title_date_publisher_or_authority(self):
        item = self.wire()['requirements'][self.slot['key']]['items'][0]
        self.assertEqual(set(item), {'id', 'kind', 'source_ref', 'review', REFERENCE})
        self.assertEqual(item['kind'], 'text')
        self.assertNotIn('approved', item['review'])

    def test_skill_short_reference_expands_to_the_same_canonical_specification(self):
        spec = self.spec(); candidate = candidates(self.task, self.source)[0]
        short = {key: spec[key] for key in ('id', 'artifact_id', 'question_ids', 'review')}
        short[REFERENCE] = candidate['reference']
        self.assertEqual(candidate_evidence(self.task, self.source, short), candidate_evidence(self.task, self.source, spec))

    def test_each_of_five_anchors_must_be_complete_and_read_once(self):
        parts = next(view['parts'] for view in self.views if view['id'] == self.source['id'])
        label = candidates(self.task, self.source, parts)[0]['label']
        anchors = [label['title_basis'], label['page_header'], label['declared_date'], *label['signatories']]
        self.assertEqual(len({row['part'] for row in anchors}), 5)
        for anchor in anchors:
            row = next(row for row in parts if row['locator'] == anchor['part'])
            for changed in ([p for p in parts if p['locator'] != anchor['part']], parts + [row],
                            [dict(p, text=p['text'][:-1]) if p['locator'] == anchor['part'] else p for p in parts]):
                with self.subTest(anchor=anchor['part']):
                    self.assertEqual(candidates(self.task, self.source, changed), [])

    def test_declared_day_does_not_gain_a_monthly_numeric_or_calculation_role(self):
        item = self.assess()
        for slot in self.slots:
            if slot == self.slot: continue
            self.assertTrue(self.branches(slot))
            self.assertTrue(all(REFERENCE not in branch['properties'] for branch in self.branches(slot)))
            self.assertFalse(satisfies(item, slot['constraints']))
        for field in ('value', 'raw_value', 'observation_date', 'observation_period', 'calculation_context'):
            self.assertNotIn(field, item)

    def test_forged_or_extra_wire_properties_cannot_produce_a_fact(self):
        for change in (dict(source_label_ref='foreign'), dict(kind='number'), dict(published_at='2026-09-30'),
                       dict(object='国家统计局'), dict(date_role='first_publication')):
            wire = self.wire(); wire['requirements'][self.slot['key']]['items'][0].update(change)
            with self.subTest(change=change):
                try: result = decode_original_spans(wire, self.schema, (self.task, self.views))
                except ContractError: continue
                self.assertFalse(result['evidence_specs'])
                self.assertFalse(result[ORIGINAL_SELECTION_REJECTIONS]['evidence_approved'])

    def test_short_selector_rejects_redundant_fields_foreign_artifact_or_reference(self):
        spec = self.spec(); short = {key: spec[key] for key in ('id', 'artifact_id', 'question_ids', 'review')}
        short[REFERENCE] = candidates(self.task, self.source)[0]['reference']
        for change in (dict(period='2026-09-30'), dict(artifact_id='foreign'), dict(source_label_ref='foreign')):
            with self.subTest(change=change), self.assertRaises(ContractError):
                candidate_evidence(self.task, self.source, dict(short, **change))

    def test_short_references_are_bound_to_task_version_batch_cutoff_and_input(self):
        baseline = candidates(self.task, self.source)[0]['reference']
        for field, value in (('id', 'foreign'), ('version', self.task['version'] + 1),
                             ('batch', self.task['batch'] + 1), ('input_hash', 'changed'),
                             ('cutoff', '2026-10-21T23:59:59+08:00')):
            task = copy.deepcopy(self.task); task[field] = value
            with self.subTest(field=field):
                try: rows = candidates(task, self.source)
                except ContractError: continue
                self.assertFalse(any(row['reference'] == baseline for row in rows))

    def test_unknown_legacy_historical_and_document_only_modes_are_not_upgraded(self):
        for mode in ('legacy', 'historical', 'document_content'):
            task = copy.deepcopy(self.task)
            if mode == 'legacy': task.pop(POLICY)
            elif mode == 'historical': task['research_mode'] = 'historical'
            else: task['intent'] = '原件内容核对'
            self.assertFalse(enabled(task)); self.assertEqual(candidates(task, self.source), [])
        for value in (True, 0, 2, '1'):
            task = copy.deepcopy(self.task); task[POLICY] = value
            with self.subTest(value=value), self.assertRaises(ContractError): enabled(task)

    def test_other_original_formats_do_not_require_new_metadata_fields(self):
        from researchhelper.output_schema import source_label_choices
        choice = dict(kind='original', view=dict(id='legacy_original', snapshot_hash='read_snapshot'))
        legacy = dict(source_records=[dict(id='legacy_original')])
        self.assertEqual(source_label_choices(legacy, choice), [])
        for source in ('ifind', 'official', 'user_material'):
            task = {POLICY: 1, 'research_mode': 'current', 'source_records': [dict(
                id='legacy_original', source=source, operation='financial_original')]}
            self.assertEqual(source_label_choices(task, choice), [])

    def test_future_page_date_or_unregistered_layout_is_not_guessed(self):
        task = copy.deepcopy(self.task); task['cutoff'] = '2026-09-01T23:59:59+08:00'
        # The catalogue gate may reject the future original before metadata
        # filtering; neither outcome may advertise a citation candidate.
        try: rows = candidates(task, self.source)
        except ContractError: rows = []
        self.assertEqual(rows, [])
        for key, value in (('source', 'ifind'), ('operation', 'release_catalog')):
            source = copy.deepcopy(self.source); source[key] = value
            self.assertEqual(candidates(self.task, source), [])

    def test_tampered_raw_title_date_or_signature_fails_before_adoption(self):
        label = candidates(self.task, self.source)[0]['label']
        for anchor in (label['title_basis'], label['declared_date'], *label['signatories']):
            source = copy.deepcopy(self.source)
            part = next(p for p in source['snapshot']['parts'] if p['locator'] == anchor['part'])
            part['text'] += '伪造'
            task = copy.deepcopy(self.task)
            task['source_records'] = [source if p['id'] == source['id'] else p for p in task['source_records']]
            with self.subTest(anchor=anchor['part']):
                try: rows = candidates(task, source)
                except ContractError: continue
                self.assertEqual(rows, [])

    def test_label_fields_locator_and_role_cannot_be_forged(self):
        baseline = self.spec()
        for field in ('title', 'site_label', 'signatories', 'declared_date', 'title_basis', 'page_header', 'version'):
            spec = copy.deepcopy(baseline); spec['source_label'][field] = True if field == 'version' else 'forged'
            with self.subTest(field=field), self.assertRaises(ContractError): candidate_evidence(self.task, self.source, spec)
        for change in (dict(source_label=None), dict(date_role='document_declared_publication'),
                       dict(object='制造业PMI'), dict(record_locator={}), dict(text='截取标题')):
            with self.subTest(change=change), self.assertRaises(ContractError):
                candidate_evidence(self.task, self.source, dict(copy.deepcopy(baseline), **change))

    def test_numbers_observation_context_or_first_publication_fields_are_rejected(self):
        baseline = self.spec()
        for change in (dict(value=50.1), dict(raw_value=50.1), dict(unit='%'), dict(nature='研究推算'),
                       dict(observation_date='2026-09-30'), dict(observation_period={'frequency':'month','value':'2026-09'}),
                       dict(calculation_context={}), dict(reporting_period_basis={}), dict(published_at='2026-09-30')):
            with self.subTest(change=change), self.assertRaises(ContractError):
                candidate_evidence(self.task, self.source, dict(copy.deepcopy(baseline), **change))

    def test_source_label_participates_in_receipt_identity(self):
        item = self.assess(); before = evidence_binding(item)
        changed = copy.deepcopy(item); changed['source_label']['site_label'] = '伪造站点'
        self.assertNotEqual(evidence_binding(changed), before)
        with self.assertRaises(ContractError): validate_evidence(changed, self.task)
        removed = copy.deepcopy(item); removed.pop('source_label')
        with self.assertRaises(ContractError): validate_evidence(removed, self.task)

    def test_source_properties_do_not_authorize_historical_or_causal_use(self):
        spec = self.spec()
        for use in ('historical_availability', 'event_causality'):
            item = candidate_evidence(self.task, self.source, spec)
            review = dict(copy.deepcopy(spec['review']), temporal_use=use)
            with self.subTest(use=use), self.assertRaises(ContractError):
                ResearchAnalyzer().assess(self.task, item, review, self.task['source_records'])

    def test_prompt_exposes_compact_unapproved_reference_without_changing_inputs(self):
        from researchhelper.reasoning import evidence_contract
        from researchhelper.planning import question_specs
        contract = evidence_contract(self.task, self.views)
        payload = dict(question=self.task['question'], intent=self.task['intent'], objects=self.task['objects'],
            cutoff=self.task['cutoff'], questions=question_specs(self.task), calculations=self.task['calculation_specs'],
            untrusted_sources=self.views, evidence_contract=contract, field_registry={}, source_reading=None)
        before = canonical(payload)
        prompt = original_span_prompt(payload, self.task)
        choices = [row for view in prompt['untrusted_sources'] for row in view.get('source_label_choices', [])]
        reference = self.wire()['requirements'][self.slot['key']]['items'][0][REFERENCE]
        self.assertTrue(any(row['reference'] == reference for row in choices))
        self.assertTrue(all(row['source_or_financial_approval'] is False for row in choices))
        self.assertTrue(any(row.get('source_label_candidates') for row in contract['sources']))
        self.assertEqual(canonical(payload), before)

    def test_policy_is_bound_to_new_tasks_and_frozen_reports(self):
        from researchhelper.archive import ResearchArchive
        from researchhelper.engine import ResearchAgent
        from researchhelper.reports import compose
        folder = tempfile.TemporaryDirectory(prefix='label-policy-', dir=ROOT/'result/acceptance')
        self.addCleanup(folder.cleanup)
        agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True, require_argument_support=True)
        self.addCleanup(agent.close_runtime)
        task = agent.create('解释制造业PMI发布稿的来源', research_mode='current')
        self.assertEqual(task[POLICY], 1)
        agent._adopt_method_knowledge(task); task['status'] = '部分完成'
        agent._gap(task, '离线来源标注控制，不是实际研究', category='question_coverage')
        self.assertEqual(compose(task)[POLICY], 1)
        old = copy.deepcopy(task); old.pop(POLICY)
        self.assertNotEqual(input_fingerprint(old), input_fingerprint(task))


if __name__ == '__main__': unittest.main()
