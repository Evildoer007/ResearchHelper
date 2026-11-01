"""Four real originals fit through reversible encoding, not material pruning."""
import copy
import json
import re
import socket
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from tests.probe_etf_extraction_context import payload_for
from researchhelper.contracts import canonical, fingerprint, ContractError
from researchhelper.evidence import _publication_date_value
from researchhelper.model import ModelService, ModelSettings
from researchhelper.output_schema import (original_span_prompt, original_span_output_schema,
    _original_span_choices, _columnar_records, _mark_reference_line, decode_original_spans, StructuredOutputError)
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import model_object, MAX_CONTEXT_CHARACTERS

MARK = re.compile(r'(?<!⟦)⟦(?:n[0-9]+_[0-9]+|v[0-9]+)⟧')
RETAINED = ROOT/'result/acceptance/autonomous-data-phase-gxbltz31/task.json'


def records(value):
    if isinstance(value, dict) and set(value) == {'columns','rows'}:
        return [dict(zip(value['columns'], row)) for row in value['rows']]
    return value


def parts(view):
    return [dict(zip(view['part_columns'], row)) if isinstance(row, list) else row for row in view['parts']]


class MultiOriginalProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = RETAINED.read_bytes()
        cls.old = json.loads(cls.raw)
        cls.task = copy.deepcopy(cls.old)
        cls.task['original_prompt_projection_version'] = 2
        cls.task['input_hash'] = input_fingerprint(cls.task)
        cls.payload = payload_for(cls.task)
        cls.views = cls.payload['untrusted_sources']
        cls.prompt = original_span_prompt(cls.payload, cls.task)
        cls.schema = original_span_output_schema(cls.task, cls.views)

    @classmethod
    def tearDownClass(cls):
        if RETAINED.read_bytes() != cls.raw:
            raise AssertionError('Closed study changed')

    def setUp(self):
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline multi-original test'))
        guard.start()
        self.addCleanup(guard.stop)

    def wire(self):
        choice = _original_span_choices(self.task, self.views)[0]
        variant = next(v for v in self.schema['properties']['selections']['items']['anyOf']
            if v['properties']['source_ref']['enum'] == [choice['source_ref']]
            and v['properties']['kind']['enum'] == ['text'])
        props = variant['properties']
        link = props['review']['properties']['need_links']['items']['anyOf'][0]['properties']
        period = next(p['ref'] for p in choice['period_anchors'] if p['period'].startswith('2026'))
        item = dict(id='ev_source', source_ref=choice['source_ref'], kind='text', object=props['object']['enum'][0],
            nature='合同政策条款', text_reference=dict(line=0), temporal_reference=dict(period_ref=period),
            review=dict(scope='current_original_descriptive_only', need_links=[dict(
                question_id=link['question_id']['enum'][0], need_ids=[link['need_ids']['items']['enum'][0]])]))
        return dict(version=12, binding_ref=self.schema['properties']['binding_ref']['enum'][0], selections=[item])

    def test_all_four_originals_fit_unchanged_context_limit(self):
        legacy = original_span_prompt(payload_for(self.old), self.old)
        legacy_schema = original_span_output_schema(self.old, self.views)
        self.assertEqual(len(self.views), 4)
        self.assertGreater(len(canonical(legacy)) + len(canonical(legacy_schema)), MAX_CONTEXT_CHARACTERS)
        self.assertLessEqual(len(canonical(self.prompt)) + len(canonical(self.schema)), MAX_CONTEXT_CHARACTERS)
        self.assertEqual(self.prompt['questions'], self.payload['questions'])
        self.assertEqual(self.prompt['calculations'], self.payload['calculations'])

    def test_every_original_character_and_part_metadata_restores(self):
        for view, choice in zip(self.prompt['untrusted_sources'], _original_span_choices(self.task, self.views)):
            expected = {line['index']:line['text'] for line in choice['lines']}
            restored = {index:MARK.sub('', text).replace('⟦⟦','⟦') for part in parts(view)
                        for index,text in part['indexed_lines']}
            self.assertEqual(restored, expected)
            for projected, original in zip(parts(view), choice['view']['parts']):
                for key,value in original.items():
                    if key != 'text':
                        self.assertEqual(projected[key], value)
            self.assertEqual(view['directory_archive_ref']['content_hash'], fingerprint(choice['view']['directory']))

    def test_literal_dates_and_disclosure_locations_reconstruct_exactly(self):
        for view, choice in zip(self.prompt['untrusted_sources'], _original_span_choices(self.task, self.views)):
            dates = []
            for row in view['observation_anchors'] + view.get('non_observation_dates', []):
                date = dict(zip(view['observation_anchor_columns'], row))
                date['date'] = _publication_date_value(date['quote'])
                dates.append(date)
            actual = [{key:value for key,value in date.items() if key != 'index'}
                      for date in sorted(dates, key=lambda date:date['index'])]
            self.assertEqual(actual, choice['description']['dates'])
            self.assertEqual(view['observation_date_encoding'], 'iso_date_from_same_literal_full_date_quote')

    def test_coverage_and_publication_facts_survive_table_encoding(self):
        coverage = records(copy.deepcopy(self.prompt['source_reading']['coverage']))
        for record in coverage:
            record['query_coverage'] = records(record['query_coverage'])
        self.assertEqual(coverage, self.payload['source_reading']['coverage'])
        self.assertEqual(self.prompt['source_reading']['limitations'], self.payload['source_reading']['limitations'])
        for old,new in zip(self.payload['evidence_contract']['sources'], self.prompt['evidence_contract']['sources']):
            self.assertEqual(records(new['publication_candidates']), old['publication_candidates'])
            self.assertEqual(new['current_availability_reference'], old['current_availability_reference'])

    def test_table_physical_page_overrides_restore_passage_context(self):
        saw_override = False
        for old,new,view in zip(self.payload['evidence_contract']['sources'], self.prompt['evidence_contract']['sources'], self.prompt['untrusted_sources']):
            original, projected = old['passage_context_index'], new['passage_context_index']
            locators = [part['locator'] for part in parts(view)]
            overrides = projected.get('physical_part_overrides', {})
            saw_override |= bool(overrides)
            restored = [dict(part=locator, physical_part=overrides.get(str(index), locator), section_ref=section)
                        for index,(locator,section) in enumerate(zip(locators, projected['section_refs']))]
            self.assertEqual(restored, original['parts'])
            self.assertEqual(projected['sections'], original['sections'])
        self.assertTrue(saw_override)

    def test_unknown_source_and_reference_still_rejected(self):
        wire = self.wire()
        wire['selections'][0]['source_ref'] = 'source_99'
        with self.assertRaises(StructuredOutputError):
            decode_original_spans(wire, self.schema, (self.task,self.views))
        wire = self.wire()
        wire['selections'][0]['temporal_reference'] = dict(period_ref='p99999')
        with self.assertRaises(StructuredOutputError):
            decode_original_spans(wire, self.schema, (self.task,self.views))

    def test_literal_markers_and_mixed_metadata_keys_are_not_reinterpreted(self):
        text = '原符号⟦v1⟧与⟦n1_1⟧，费率0.15%。'
        marked = _mark_reference_line(text, [('v19','0.15')])
        self.assertEqual(MARK.sub('',marked).replace('⟦⟦','⟦'), text)
        with self.assertRaises(ContractError):
            _mark_reference_line(text, [('v19','0.05')])
        mixed = [dict(a=None), dict(b=None)]
        self.assertEqual(_columnar_records(mixed), mixed)

    def test_real_model_adapter_accepts_complete_four_source_payload_once(self):
        reply = dict(object='response', status='completed', error=None, incomplete_details=None, output=[dict(
            type='message', role='assistant', status='completed', content=[dict(type='output_text',text=json.dumps(self.wire()))])], usage={})
        with tempfile.TemporaryDirectory(prefix='multi-original-', dir=ROOT/'result/acceptance') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash',
                api_key='SYNTHETIC_MULTI_ORIGINAL_KEY', extraction_mode='direct'))
            with patch.object(ModelService,'_request_bytes',return_value=json.dumps(reply).encode()) as transport:
                result,receipt = model_object(ModelService(settings,offline=False),copy.deepcopy(self.task),'extract',self.payload,{'evidence_specs'})
        self.assertEqual(transport.call_count, 1)
        request = json.loads(transport.call_args.args[1].data)
        instructions = canonical(request['input'])
        self.assertNotIn('number_tokens按number_token_columns', instructions)
        self.assertNotIn('该行reference列', instructions)
        self.assertEqual(receipt['original_prompt_projection_version'], 2)
        self.assertEqual(result, decode_original_spans(self.wire(),self.schema,(self.task,self.views)))

    def test_projection_policy_changes_input_not_old_study(self):
        self.assertNotEqual(input_fingerprint(self.task), input_fingerprint(self.old))
        self.assertEqual(RETAINED.read_bytes(), self.raw)


if __name__ == '__main__':
    unittest.main()
