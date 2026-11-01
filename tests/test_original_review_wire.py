"""Review-wire inspection of closed R136 inputs, without model requests.

The rejected response was not retained. This reconstructs the selector schema
and prompt, not that response, and never resumes or edits the real study.
"""
import copy
import hashlib
import base64
import gzip
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from decimal import Decimal
from unittest.mock import patch

from researchhelper.contracts import ContractError, canonical, fingerprint, validate_evidence
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import EvidenceCollector, candidate_evidence
from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.model import ModelService
from researchhelper.model_presentation import restore
from researchhelper.output_schema import (original_span_output_schema, provider_request_schema, ORIGINAL_SPAN_PROTOCOL,
    ORIGINAL_SPAN_FUNCTION_NAME, _original_span_choices, _named_quantity_index, original_observation_choices,
    decode_original_spans, structured_json_completion, structured_completion, _matches)
from researchhelper.original_scope_wire import POLICY, project, restore as restore_scope, enabled
from researchhelper.planning import question_specs, input_fingerprint
from researchhelper.reasoning import evidence_contract, extractable_source_views, model_object

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT/'result/acceptance/one-sentence-research-ln4q0sob'
EXPECTED = {
    '国家统计局最新一期制造业PMI原值、原始单位及所属月份': ('2026-09', '制造业采购经理指数（PMI）为50.1%', 50.1),
    '国家统计局最新一期制造业生产指数原值、原始单位及所属月份': ('2026-09', '生产指数为51.7%', 51.7),
    '国家统计局最新一期制造业新订单指数原值、原始单位及所属月份': ('2026-09', '新订单指数为50.5%', 50.5),
    '国家统计局上一期制造业PMI原值、原始单位及所属月份': ('2026-08', '制造业采购经理指数（PMI）为49.8%', 49.8),
    '国家统计局上一期制造业生产指数原值、原始单位及所属月份': ('2026-08', '生产指数为50.4%', 50.4),
    '国家统计局上一期制造业新订单指数原值、原始单位及所属月份': ('2026-08', '新订单指数为50.6%', 50.6),
}


class Captured(Exception): pass


class Observer:
    def supports_structured_analysis(self): return True

    def input_budget(self):
        return ModelService.documented_input_budget(dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash'))

    def reason_structured_original_spans(self, task, messages, views):
        self.messages = messages
        self.schema = original_span_output_schema(task, views)
        self.provider_schema = provider_request_schema(task, self.schema, ORIGINAL_SPAN_PROTOCOL)
        raise Captured()


def observe(task=None, views=None):
    task = json.loads((STUDY/'task.json').read_text()) if task is None else task
    views = extractable_source_views(task) if views is None else views
    original = canonical((task, views))
    payload = dict(question=task['question'], intent=task['intent'], objects=task['objects'], cutoff=task['cutoff'],
        questions=question_specs(task), calculations=task['calculation_specs'],
        evidence_contract=evidence_contract(task, views), field_registry={}, untrusted_sources=views, source_reading=None)
    observer = Observer()
    with patch('socket.socket.connect', side_effect=AssertionError('No paid or public requests')):
        try: model_object(observer, task, 'extract', payload, {'evidence_specs'})
        except Captured: pass
        else: raise AssertionError('Did not observe the actual extraction boundary')
    assert canonical((task, views)) == original
    body = json.loads(observer.messages[1]['content'])
    if body.get('format') == 'researchhelper.table_input': body = restore(body)
    return task, observer, body


def prepare_fixture():
    protected = {name: hashlib.sha256((STUDY/name).read_bytes()).hexdigest()
                 for name in ('receipt.json', 'task.json', 'report.json', 'report.html')}
    old = json.loads((STUDY/'task.json').read_text())
    from researchhelper.official_sources import catalogue_url, selection_references
    pages = {(catalogue_url(record['parameters']['page']) if record['operation'] == 'release_catalog'
              else record['snapshot']['official_origin']['url']): base64.b64decode(record['snapshot']['original_base64'], validate=True)
             for record in old['source_records']}
    def transport(task, url, started, timeout, event, **kwargs):
        event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True)
        return pages[url]
    with tempfile.TemporaryDirectory(prefix='scope-wire-fixture-', dir=ROOT/'result/acceptance') as folder:
        agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
        try:
            task = agent.create(old['question'], entry=old['entry'], objects=old['objects'], research_mode='current')
            task = agent.revise_plan(task['id'], question_specs(old), '离线固定用途传输控制，不重开旧研究', task['input_hash'])
            collector = EvidenceCollector(offline=False)
            with (patch('socket.socket.connect', side_effect=AssertionError('No external requests')),
                  patch('researchhelper.source_transport.official_html', side_effect=transport)):
                for prior in old['source_records']:
                    parameters = copy.deepcopy(prior['parameters'])
                    if prior['operation'] == 'release_original':
                        choice = next(row for row in selection_references(task)
                                      if row['title'] == prior['snapshot']['official_origin']['title'])
                        parameters = dict(selection_ref=choice['selection_ref'])
                    current = collector.external(task, 'official', prior['operation'], parameters, {})
                    current['question_ids'] = copy.deepcopy(prior.get('question_ids', ['q1']))
                    task['source_records'].append(current)
            agent._adopt_method_knowledge(task)
        finally: agent.close_runtime()
    assert protected == {name: hashlib.sha256((STUDY/name).read_bytes()).hexdigest() for name in protected}
    return dict(old=old, task=task, views=extractable_source_views(task), old_views=extractable_source_views(old),
        protected=protected, mocked_transport=True, authored_selectors=True, real_model_calls=0, real_source_calls=0,
        original_study_reopened=False, rejected_response_recovered=False, real_model_quality_accepted=False)


def literal(schema):
    if 'anyOf' in schema: return literal(schema['anyOf'][0])
    if 'enum' in schema: return schema['enum'][0]
    if schema['type'] == 'object': return {key: literal(value) for key, value in schema['properties'].items()}
    if schema['type'] == 'array': return []
    if schema['type'] == 'string': return 'scope_wire_control'
    raise AssertionError('No invented selector field')


def canonical_wire(task, views, schema):
    from researchhelper.data_requirements import requirement_slots
    choices = _original_span_choices(task, views)
    records = {record['id']: record for record in task['source_records']}
    result = dict(version=12, binding_ref=schema['properties']['binding_ref']['enum'][0], selections=[], requirements={})
    used_ids = set()
    for index, slot in enumerate(requirement_slots(task)):
        period, needle, value = EXPECTED[slot['text']]
        title = period[:4] + '年' + str(int(period[5:])) + '月中国采购经理指数运行情况'
        choice = next(choice for choice in choices if records[choice['view']['id']]['snapshot']['official_origin']['title'] == title)
        found = next(option for option in schema['properties']['requirements']['properties'][slot['key']]['anyOf']
                     if option['properties']['status']['enum'] == ['found'])
        branch = next(branch for branch in found['properties']['items']['items']['anyOf']
                      if branch['properties']['source_ref']['enum'] == [choice['source_ref']])
        selected = literal(branch); fields = branch['properties']; id_schema = fields['id']
        selected['id'] = (next(ident for ident in id_schema['enum'] if ident not in used_ids) if 'enum' in id_schema
                          else 'wire_control_' + str(index))
        used_ids.add(selected['id']); selected['object'] = '中国制造业'; selected['nature'] = '已实现事实'
        tokens = {token['ref']: token for token in choice['number_tokens']}
        quantity = next(reference for reference, address in _named_quantity_index(choice).items()
            if address['unit'] == '%' and Decimal(tokens[address['number_ref']]['token']) == Decimal(str(value))
            and needle in re.sub(r'\s+', '', choice['lines'][address['line']]['text']))
        selected['number_reference'] = dict(quantity_ref=quantity)
        observations = original_observation_choices(choice)
        allowed = {reference for option in fields['temporal_reference']['anyOf']
                   for reference in option['properties'].get('observation_ref', {}).get('enum', [])}
        selected['temporal_reference'] = dict(observation_ref=next(row['reference'] for row in observations
            if row['period'] == period and row['reference'] in allowed))
        assert _matches(selected, branch), 'Authored control must match the actual source schema'
        result['requirements'][slot['key']] = dict(status='found', items=[selected])
    return result


class OriginalReviewWireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        supplied = os.environ.get('RESEARCHHELPER_SCOPE_WIRE_FIXTURE')
        cls.fixture = json.loads(gzip.decompress(Path(supplied).read_bytes())) if supplied else prepare_fixture()

    def setUp(self):
        guard = patch('socket.socket.connect', side_effect=AssertionError('No model or provider requests'))
        guard.start(); self.addCleanup(guard.stop)
        if os.environ.get('RESEARCHHELPER_FROZEN_TEST_EXECUTABLE'):
            from frozen_archive_test_loader import frozen_pdf_worker
            from researchhelper.material_parser import MaterialParser
            worker = patch.object(MaterialParser, 'worker_target', staticmethod(frozen_pdf_worker))
            worker.start(); self.addCleanup(worker.stop)
        self.task, self.views = copy.deepcopy(self.fixture['task']), copy.deepcopy(self.fixture['views'])
        self.schema = original_span_output_schema(self.task, self.views)
        self.canonical = canonical_wire(self.task, self.views, self.schema)
        self.wire = copy.deepcopy(self.canonical); self.wire['version'] = 13; self.wire.pop('selections')
        for requirement in self.wire['requirements'].values():
            for item in requirement['items']: item.pop('review')

    def decode(self, wire=None):
        return json.loads(structured_json_completion(json.dumps(self.wire if wire is None else wire), self.schema,
            protocol=ORIGINAL_SPAN_PROTOCOL, context=(self.task, self.views)))

    def test_closed_dispatch_schemas_are_preserved_without_upgrading_old_task(self):
        old = copy.deepcopy(self.fixture['old']); schema = original_span_output_schema(old, self.fixture['old_views'])
        event = next(event for event in reversed(old['ledger']['dispatches']) if event['kind'] == 'model_calls')
        self.assertEqual(fingerprint(schema), event['output_schema_hash'])
        self.assertEqual(fingerprint(provider_request_schema(old, schema, ORIGINAL_SPAN_PROTOCOL)), event['provider_output_schema_hash'])
        self.assertEqual(project(old, schema), schema); self.assertNotIn(POLICY, old)

    def test_projection_removes_only_the_two_declared_constants(self):
        before = canonical(self.schema); encoded = project(self.task, self.schema)
        self.assertEqual(set(encoded['properties']), {'version', 'binding_ref', 'requirements'})
        self.assertEqual(encoded['properties']['version']['enum'], [13])
        self.assertEqual(restore_scope(self.task, self.schema, self.wire), self.canonical)
        self.assertEqual(canonical(self.schema), before)

    def test_public_app_skill_contract_stays_version_twelve(self):
        self.assertEqual(self.schema['properties']['version']['enum'], [12])
        self.assertEqual(decode_original_spans(self.canonical, self.schema, (self.task, self.views)), self.decode())

    def test_json_and_function_transports_produce_the_same_canonical_proposals(self):
        message = dict(role='assistant', content=None, tool_calls=[dict(id='offline_control', type='function',
            function=dict(name=ORIGINAL_SPAN_FUNCTION_NAME, arguments=json.dumps(self.wire)))])
        result = structured_completion(message, 'tool_calls', self.schema, protocol=ORIGINAL_SPAN_PROTOCOL,
                                       context=(self.task, self.views))
        self.assertEqual(json.loads(result), self.decode())
        self.assertFalse(self.task['evidence']); self.assertEqual(self.task['ledger']['used']['model_calls'], 0)

    def test_six_authored_literal_selections_still_require_both_checks(self):
        from researchhelper.data_requirements import requirement_slots
        specs = self.decode()['evidence_specs']; self.assertEqual(len(specs), 6)
        by_id = {record['id']: record for record in self.task['source_records']}
        expected = [EXPECTED[slot['text']] for slot in requirement_slots(self.task)]
        for spec, (period, _, value) in zip(specs, expected):
            item = candidate_evidence(self.task, by_id[spec['artifact_id']], spec)
            self.assertEqual(item['applicability_check'], '待核验')
            ResearchAnalyzer().assess(self.task, item, spec['review'], self.task['source_records'])
            validate_evidence(item, self.task)
            self.assertEqual((item['value'], item['unit'], item['period']), (value, '%', period))
            self.assertIsNone(item['published_at'])
            self.assertFalse(item['availability']['historical_availability_approved'])

    def test_extra_review_is_rejected_even_if_empty_or_redundant(self):
        for review in ({}, dict(scope='current_original_descriptive_only'), dict(reason='合成多余字段')):
            wire = copy.deepcopy(self.wire)
            next(iter(wire['requirements'].values()))['items'][0]['review'] = review
            before = canonical(wire)
            with self.assertRaises(ContractError): self.decode(wire)
            self.assertEqual(canonical(wire), before); self.assertFalse(self.task['evidence'])

    def test_legacy_envelope_is_not_silently_converted_in_new_model_transport(self):
        with self.assertRaises(ContractError): self.decode(self.canonical)
        wire = dict(self.wire, selections=[])
        with self.assertRaises(ContractError): self.decode(wire)

    def test_changed_binding_and_permission_fields_are_not_repaired(self):
        wire = copy.deepcopy(self.wire); wire['binding_ref'] = 'another_task'
        with self.assertRaises(ContractError): self.decode(wire)
        wire = copy.deepcopy(self.wire)
        next(iter(wire['requirements'].values()))['items'][0]['temporal_use'] = 'historical_event'
        with self.assertRaises(ContractError): self.decode(wire)

    def test_bad_local_selector_keeps_other_five_and_feedback(self):
        wire = copy.deepcopy(self.wire)
        next(iter(wire['requirements'].values()))['items'][0]['temporal_reference'] = dict(observation_ref='bad_control')
        result = self.decode(wire)
        self.assertEqual(len(result['evidence_specs']), 5)
        self.assertTrue(any(row['status'] == 'format_rejected' for row in result['_original_requirement_feedback']['requirements']))

    def test_unsubmitted_requirement_is_not_fabricated_or_globally_rejected(self):
        wire = copy.deepcopy(self.wire); wire['requirements'].pop(next(iter(wire['requirements'])))
        result = self.decode(wire)
        self.assertEqual(len(result['evidence_specs']), 5)
        self.assertTrue(any(row['status'] == 'not_submitted' for row in result['_original_requirement_feedback']['requirements']))

    def test_bad_wrapper_keeps_independent_valid_selections(self):
        wire = copy.deepcopy(self.wire)
        wire['requirements'][next(iter(wire['requirements']))] = dict(status='not_found', reason='')
        self.assertEqual(len(self.decode(wire)['evidence_specs']), 5)

    def test_all_not_found_does_not_approve_facts_or_source_absence(self):
        wire = copy.deepcopy(self.wire)
        wire['requirements'] = {key: dict(status='not_found', reason='控制样例未定位，不是未披露') for key in wire['requirements']}
        result = self.decode(wire); self.assertFalse(result['evidence_specs'])
        self.assertFalse(result['_original_requirement_feedback']['source_absence_proved'])

    def test_different_metric_value_remains_a_financial_applicability_failure(self):
        wire = copy.deepcopy(self.wire); values = list(wire['requirements'].values())
        values[1]['items'][0]['number_reference'] = copy.deepcopy(values[0]['items'][0]['number_reference'])
        specs = self.decode(wire)['evidence_specs']
        spec = specs[1]; source = next(record for record in self.task['source_records'] if record['id'] == spec['artifact_id'])
        item = candidate_evidence(self.task, source, spec)
        self.assertEqual(item['source_check'], '通过')
        with self.assertRaises(ContractError): ResearchAnalyzer().assess(self.task, item, spec['review'], self.task['source_records'])

    def test_nonconstant_mixed_or_historical_contracts_stay_explicit(self):
        for mutation in ('historical', 'live_array', 'scope_choice'):
            task, schema = copy.deepcopy(self.task), copy.deepcopy(self.schema)
            if mutation == 'historical': task['research_mode'] = 'historical'
            elif mutation == 'live_array': schema['properties']['selections']['maxItems'] = 1
            else:
                found = next(option for option in next(iter(schema['properties']['requirements']['properties'].values()))['anyOf']
                             if option['properties']['status']['enum'] == ['found'])
                found['properties']['items']['items']['anyOf'][0]['properties']['review']['properties']['scope']['enum'].append('other_scope')
            self.assertEqual(project(task, schema), schema)

    def test_unknown_policy_cannot_enable_scope_restoration(self):
        for value in (True, 0, 2, '1', 1.0):
            with self.assertRaises(ContractError): enabled({POLICY: value})
        task = copy.deepcopy(self.task); task.pop(POLICY)
        self.assertEqual(project(task, self.schema), self.schema)
        with self.assertRaises(ContractError):
            structured_json_completion(json.dumps(self.wire), self.schema, protocol=ORIGINAL_SPAN_PROTOCOL, context=(task, self.views))

    def test_common_selection_limit_is_checked_before_constant_expansion(self):
        wire = copy.deepcopy(self.wire); first = next(iter(wire['requirements'].values()))
        first['items'] = first['items'] * 1001
        with self.assertRaises(ContractError): self.decode(wire)

    def test_prompt_and_provider_expose_only_the_active_transport_fields(self):
        _, observer, _ = observe(self.task, self.views)
        system = observer.messages[0]['content']
        self.assertIn('version=13', system)
        self.assertNotIn('items的review只按Schema填写scope', system)
        self.assertNotIn('保留空selections', system)
        self.assertNotIn('selections', observer.provider_schema['properties'])
        old = copy.deepcopy(self.task); old.pop(POLICY)
        baseline = provider_request_schema(old, self.schema, ORIGINAL_SPAN_PROTOCOL)
        self.assertLess(len(canonical(observer.provider_schema)), len(canonical(baseline)))

    def test_json_duplicate_keys_are_not_a_scope_codec_recovery_path(self):
        raw = json.dumps(self.wire).replace('"version": 13', '"version": 13, "version": 13', 1)
        with self.assertRaises(ContractError):
            structured_json_completion(raw, self.schema, protocol=ORIGINAL_SPAN_PROTOCOL, context=(self.task, self.views))

    def test_fresh_actual_resource_policy_is_bound_and_frozen(self):
        from researchhelper.reports import compose
        resources = Path(os.environ.get('RESEARCHHELPER_SCOPE_WIRE_RESOURCES', str(ROOT)))
        with tempfile.TemporaryDirectory(prefix='scope-wire-policy-') as folder:
            agent = ResearchAgent(ResearchArchive(folder), resources, offline=True, require_argument_support=True)
            try:
                task = agent.create('核对本次原件选择的用途', research_mode='current')
                self.assertEqual(task[POLICY], 1)
                old = copy.deepcopy(task); old.pop(POLICY)
                self.assertNotEqual(input_fingerprint(old), input_fingerprint(task))
                agent._adopt_method_knowledge(task); task['status'] = '部分完成'
                agent._gap(task, '固定用途控制，不是实际研究', category='question_coverage')
                self.assertEqual(compose(task)[POLICY], 1)
            finally: agent.close_runtime()

    def test_archive_roundtrip_keeps_policy_and_original_budget(self):
        before = copy.deepcopy(self.task['ledger'])
        with tempfile.TemporaryDirectory(prefix='scope-wire-archive-') as folder:
            archive = ResearchArchive(folder); private = copy.deepcopy(self.task); private['revision'] = 0
            archive.save(private); restored = archive.get(private['id'])
            self.assertEqual(restored[POLICY], 1); self.assertEqual(restored['ledger'], before)
            self.assertEqual(original_span_output_schema(restored, self.views), self.schema)


def probe():
    task, observer, body = observe()
    grammars = []
    def walk(value, path='$'):
        if isinstance(value, dict):
            for key, item in value.items():
                location = path + '.' + key
                if key in ('review', 'review_fields', 'review_types', 'review_templates'):
                    grammars.append(dict(path=location, fields=list(item) if isinstance(item, dict) else item))
                walk(item, location)
        elif isinstance(value, list):
            for index, item in enumerate(value): walk(item, path + '[' + str(index) + ']')
    walk(body)
    allowed = []
    for shape in observer.schema['properties']['requirements']['properties'].values():
        for found in shape['anyOf']:
            if found['properties']['status']['enum'] != ['found']: continue
            for branch in found['properties']['items']['items']['anyOf']:
                fields = list(branch['properties']['review']['properties'])
                if fields not in allowed: allowed.append(fields)
    event = next(event for event in reversed(task['ledger']['dispatches']) if event['kind'] == 'model_calls')
    result = dict(schema_matches_real_dispatch=fingerprint(observer.schema) == event['output_schema_hash'],
        provider_schema_matches_real_dispatch=fingerprint(observer.provider_schema) == event['provider_output_schema_hash'],
        active_review_fields=allowed, prompt_review_grammars=grammars,
        system_review_clauses=[part for part in re.split('[。；\n]', observer.messages[0]['content']) if 'review' in part or 'need_links' in part],
        real_model_calls=0, real_source_calls=0, original_study_reopened=False, rejected_response_recovered=False)
    print(json.dumps(result, ensure_ascii=False))
    return result
