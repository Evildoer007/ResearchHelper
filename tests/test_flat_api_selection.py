"""Flat transport is lossless; actual financial validators remain in charge."""
import copy
import json
import unittest

try:
    from . import test_sse_etf_price_context as fixtures
except ImportError:
    import test_sse_etf_price_context as fixtures
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.flat_api_selection import POLICY, enabled, project, restore
from researchhelper.api_context_wire import restore as restore_contexts
from researchhelper.output_schema import (ORIGINAL_SPAN_PROTOCOL, _matches, object_schema,
    decode_original_spans, structured_json_completion, provider_request_schema)
from researchhelper.planning import input_fingerprint


class FlatAPISelectionTests(unittest.TestCase):
    def setUp(self):
        self.control = fixtures.SSEETFPcfPriceContextTests()
        self.control.setUp()
        self.addCleanup(self.control.doCleanups)
        self.task = self.control.task
        self.task[POLICY] = 1
        self.task['api_need_compatibility_version'] = 2
        self.task['questions'][0]['evidence_needs'] = ['同一估值日收盘价', '官方单位净值及所属日']
        self.task['questions'][0]['evidence_need_constraints'] = {text: dict(value_kind='number',
            unit_dimension='price', minimum_count=1, period_kind='point')
            for text in self.task['questions'][0]['evidence_needs']}
        self.task['input_hash'] = input_fingerprint(self.task)
        from researchhelper.reasoning import extractable_source_views
        from researchhelper.output_schema import original_span_output_schema
        self.views = extractable_source_views(self.task)
        self.schema = original_span_output_schema(self.task, self.views)
        variant = next(branch for branch in self.schema['properties']['selections']['items']['anyOf']
            if branch['properties'].get('additional_contexts', {}).get('items', {}).get('anyOf'))
        item = fixtures.literal(variant)
        shape = variant['properties']['selection']['properties']['evidence_specs']['items']['anyOf'][0]
        spec = fixtures.literal(shape)
        spec['api_cell_selections'] = [shape['properties']['api_cell_selections']['items']['enum'][0]]
        link_shape = shape['properties']['review']['properties']['need_links']['items']['anyOf'][0]
        link = fixtures.literal(link_shape)
        link['need_ids'] = [link_shape['properties']['need_ids']['items']['enum'][0]]
        spec['review']['need_links'] = [link]
        spec['review']['allowed_natures'] = ['已实现事实']
        spec['review']['conditions'] = ['仅本次原值比较，不证明套利、历史可得或收益。']
        item['selection']['evidence_specs'] = [spec]
        item['additional_contexts'] = [fixtures.literal(
            variant['properties']['additional_contexts']['items']['anyOf'][0])]
        self.canonical = fixtures.literal(self.schema)
        self.canonical['selections'] = [item]
        self.wire = copy.deepcopy(self.canonical)
        self.wire['version'] = 15
        item = self.wire['selections'][0]
        spec = item.pop('selection')['evidence_specs'][0]
        item.update(spec)

    def decode(self, wire):
        return json.loads(structured_json_completion(json.dumps(wire), self.schema,
            protocol=ORIGINAL_SPAN_PROTOCOL, context=(self.task, self.views)))

    def test_projected_api_is_flat_and_retains_actual_review_and_context_choices(self):
        selected = project(self.task, self.schema)
        self.assertEqual(selected['properties']['version']['enum'], [15])
        self.assertTrue(_matches(self.wire, selected))
        item = self.wire['selections'][0]
        self.assertNotIn('selection', item)
        self.assertNotIn('evidence_specs', item)
        self.assertIn('review', item)
        self.assertIn('additional_contexts', item)

    def test_roundtrip_matches_full_canonical_selection_not_a_new_approval(self):
        before = fingerprint((self.task, self.views, self.wire, self.schema))
        restored = restore_contexts(self.task, self.schema, restore(self.task, self.schema, self.wire))
        self.assertEqual(restored, self.canonical)
        self.assertEqual(self.decode(self.wire), decode_original_spans(
            self.canonical, self.schema, (self.task, self.views)))
        self.assertEqual(before, fingerprint((self.task, self.views, self.wire, self.schema)))
        self.assertEqual(self.task['evidence'], [])

    def test_canonical_host_submission_is_unchanged(self):
        self.assertEqual(restore(self.task, self.schema, self.canonical), self.canonical)
        self.assertEqual(self.decode(self.canonical), self.decode(self.wire))

    def test_omission_does_not_select_existing_share_class_reference(self):
        wire = copy.deepcopy(self.wire)
        wire['selections'][0].pop('additional_contexts')
        result = self.decode(wire)['evidence_specs'][0]
        self.assertNotIn('share_class', result.get('calculation_context', {}))
        self.assertNotIn('share_class', result.get('calculation_context_basis', {}))

    def test_unknown_fields_authority_old_nesting_and_binding_stay_rejected(self):
        for change in ('value', 'authority', 'nested', 'binding', 'version'):
            wire = copy.deepcopy(self.wire)
            item = wire['selections'][0]
            if change == 'value': item['value'] = 123.45
            elif change == 'authority': item['source_check'] = '通过'
            elif change == 'nested': item['selection'] = {'version': 3, 'evidence_specs': []}
            elif change == 'binding': wire['binding_ref'] = 'foreign'
            else: wire['version'] = True
            with self.subTest(change=change), self.assertRaises(ContractError):
                self.decode(wire)

    def test_foreign_cells_sources_context_and_need_links_are_not_repaired(self):
        for change in ('cell', 'source', 'context', 'need'):
            wire = copy.deepcopy(self.wire)
            item = wire['selections'][0]
            if change == 'cell': item['api_cell_selections'] = ['foreign-cell']
            elif change == 'source': item['source_ref'] = 'foreign-source'
            elif change == 'context': item['additional_contexts'] = [{'context_ref': 'foreign'}]
            else: item['review']['need_links'] = [{'question_id': 'q1', 'need_ids': ['foreign']}]
            with self.subTest(change=change), self.assertRaises(ContractError):
                self.decode(wire)

    def test_old_historical_document_and_unknown_versions_do_not_gain_format(self):
        old = copy.deepcopy(self.task)
        old.pop(POLICY)
        self.assertNotEqual(project(old, self.schema)['properties']['version']['enum'], [15])
        for field, value in [('research_mode', 'historical'), ('intent', '原件内容核对')]:
            task = dict(self.task, **{field: value})
            self.assertFalse(enabled(task))
            self.assertNotEqual(project(task, self.schema)['properties']['version']['enum'], [15])
        for value in (True, 2):
            with self.subTest(value=value), self.assertRaises(ContractError):
                project(dict(self.task, **{POLICY: value}), self.schema)

    def test_original_items_and_their_partial_validation_are_not_rewritten(self):
        schema = copy.deepcopy(self.schema)
        original = object_schema(dict(source_ref=dict(type='string', enum=['pdf_source']),
            kind=dict(type='string', enum=['text']), text_span=object_schema(dict(part_ref=dict(type='integer')))))
        schema['properties']['selections']['items']['anyOf'].append(original)
        wire = copy.deepcopy(self.wire)
        malformed = dict(source_ref='pdf_source', kind='text', text_span=dict(part_ref='invalid'))
        wire['selections'].append(malformed)
        restored = restore(self.task, schema, wire)
        self.assertEqual(restored['selections'][-1], malformed)
        self.assertFalse(_matches(restored['selections'][-1], original))

    def test_other_api_formats_are_preserved_not_forced_into_cells(self):
        schema = copy.deepcopy(self.schema)
        old = copy.deepcopy(schema['properties']['selections']['items']['anyOf'][0])
        old['properties']['source_ref']['enum'] = ['legacy_api']
        old['properties']['selection']['properties']['version']['enum'] = [1]
        schema['properties']['selections']['items']['anyOf'].append(old)
        item = fixtures.literal(old)
        wire = copy.deepcopy(self.wire)
        wire['selections'].append(item)
        self.assertEqual(restore(self.task, schema, wire)['selections'][-1], item)

    def test_provider_and_system_instructions_use_same_flat_schema(self):
        from researchhelper.reasoning import _api_selector_instructions
        projected = project(self.task, self.schema)
        self.assertIn('没有selection、evidence_specs', _api_selector_instructions(projected))
        offered = provider_request_schema(self.task, self.schema, ORIGINAL_SPAN_PROTOCOL)
        self.assertEqual(offered['properties']['version']['enum'], [15])

    def test_readonly_decoding_still_passes_registered_same_day_context_validation(self):
        from researchhelper.evidence import candidate_evidence
        from researchhelper.contracts import calculation_context_contract, validate_calculation_context
        spec = self.decode(self.wire)['evidence_specs'][0]
        fact = candidate_evidence(self.task, self.control.price, spec)
        self.assertEqual(fact['source_check'], '通过')
        self.assertEqual(fact['applicability_check'], '待核验')
        validate_calculation_context(self.control.method, dict(price=fact, nav=self.control.bindings['nav']),
            self.control.parameters, context_contract=calculation_context_contract(self.task, self.control.method))
        self.assertEqual(self.task['evidence'], [])

    def test_definition_question_does_not_break_typed_api_schema_or_own_observations(self):
        from researchhelper.output_schema import original_span_output_schema
        self.task['questions'].append(dict(id='definition', question='什么是净值', kind='fact',
            required=True, dependencies=[], evidence_needs=[], method_needs=[], answer_scope='general_definition'))
        self.task['input_hash'] = input_fingerprint(self.task)
        schema = original_span_output_schema(self.task, self.views)
        projected = project(self.task, schema)
        self.assertEqual(projected['properties']['version']['enum'], [15])
        api = [b for b in projected['properties']['selections']['items']['anyOf']
               if b['properties']['kind']['enum'] == ['api']]
        self.assertTrue(api)
        for branch in api:
            links = branch['properties']['review']['properties']['need_links']['items']['anyOf']
            self.assertTrue(all('definition' not in link['properties']['question_id']['enum'] for link in links))

    def test_model_entry_describes_the_flat_wire_without_old_wrapper_instruction(self):
        from researchhelper.model import ModelService
        from researchhelper.planning import question_specs
        from researchhelper.reasoning import evidence_contract, model_object
        class Captured(Exception):
            pass
        class Observer:
            def supports_structured_analysis(self): return True
            def input_budget(self):
                return ModelService.documented_input_budget(dict(
                    endpoint='https://api.deepseek.com/responses', model='deepseek-flash'))
            def reason_structured_original_spans(self, task, messages, views):
                self.messages = messages
                raise Captured()
        observer = Observer()
        payload = dict(question=self.task['question'], intent=self.task['intent'], objects=self.task['objects'],
            cutoff=self.task['cutoff'], questions=question_specs(self.task), calculations=self.task['calculation_specs'],
            untrusted_sources=self.views, evidence_contract=evidence_contract(self.task, self.views),
            field_registry={}, source_reading=None)
        with self.assertRaises(Captured):
            model_object(observer, self.task, 'extract', payload, {'evidence_specs'})
        system = observer.messages[0]['content']
        self.assertIn('version=15', system)
        self.assertIn('没有selection、evidence_specs', system)
        self.assertNotIn('也不把API的selection字段摊到外层', system)

    def test_policy_fingerprint_and_report_freeze_without_budget_change(self):
        import os
        from pathlib import Path
        import tempfile
        from researchhelper.archive import ResearchArchive
        from researchhelper.engine import ResearchAgent
        from researchhelper.reports import compose
        original = copy.deepcopy(self.task)
        original.pop(POLICY)
        self.assertNotEqual(input_fingerprint(original), input_fingerprint(self.task))
        with tempfile.TemporaryDirectory(prefix='rh-flat-policy-') as folder:
            resources = Path(os.environ.get('RESEARCHHELPER_SSE_ETF_RESOURCES', str(fixtures.fixtures.ROOT)))
            agent = ResearchAgent(ResearchArchive(folder), resources, offline=True, require_argument_support=True)
            try:
                task = agent.create('核对当前原值，不作投资判断。', objects=['510300.SH'])
                agent._adopt_method_knowledge(task)
                task['status'] = '部分完成'
                agent._gap(task, '离线格式控制，未开展真实研究。', category='question_coverage')
                before = copy.deepcopy(task['ledger'])
                self.assertEqual(compose(task)[POLICY], 1)
                self.assertEqual(task['ledger'], before)
            finally:
                agent.close_runtime()


if __name__ == '__main__':
    unittest.main()
