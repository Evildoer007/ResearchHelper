"""The actual extraction boundary must describe the active selector branches."""
import copy
import gzip
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.contracts import canonical, validate_evidence
from researchhelper.data_requirements import requirement_slots
from researchhelper.evidence import candidate_evidence
from researchhelper.model import ModelService
from researchhelper.model_presentation import restore
from researchhelper.output_schema import (decode_original_spans, original_span_output_schema,
                                         ORIGINAL_SELECTION_REJECTIONS)
from researchhelper.planning import question_specs
from researchhelper.reasoning import evidence_contract, model_object


def literal(schema):
    if 'anyOf' in schema: return literal(schema['anyOf'][0])
    if 'enum' in schema: return schema['enum'][0]
    if schema['type'] == 'object': return {key: literal(value) for key, value in schema['properties'].items()}
    if schema['type'] == 'array': return []
    if schema['type'] == 'string': return 'offline_prompt_control'
    raise AssertionError('No invented field or selector')


class Captured(Exception):
    pass


class ExtractionObserver:
    def supports_structured_analysis(self): return True

    def input_budget(self):
        return ModelService.documented_input_budget(dict(
            endpoint='https://api.deepseek.com/responses', model='deepseek-flash'))

    def reason_structured_original_spans(self, task, messages, views):
        self.messages = messages
        self.schema = original_span_output_schema(task, views)
        raise Captured()


class SourceLabelPromptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        supplied = os.environ.get('RESEARCHHELPER_SOURCE_LABEL_FIXTURE')
        if supplied:
            cls.fixture = json.loads(gzip.decompress(Path(supplied).read_bytes()))
        else:
            from tests.test_source_label_selection import prepare_fixture
            cls.fixture = prepare_fixture()

    def setUp(self):
        guard = patch('socket.socket.connect', side_effect=AssertionError('No provider or model calls'))
        guard.start(); self.addCleanup(guard.stop)
        if os.environ.get('RESEARCHHELPER_FROZEN_TEST_EXECUTABLE'):
            from frozen_archive_test_loader import frozen_pdf_worker
            from researchhelper.material_parser import MaterialParser
            worker = patch.object(MaterialParser, 'worker_target', staticmethod(frozen_pdf_worker))
            worker.start(); self.addCleanup(worker.stop)
        self.task, self.views = (copy.deepcopy(self.fixture[key]) for key in ('task', 'views'))
        self.before = canonical((self.task, self.views))

    def tearDown(self):
        self.assertEqual(canonical((self.task, self.views)), self.before)

    def observe(self, task=None):
        task = self.task if task is None else task
        payload = dict(question=task['question'], intent=task['intent'], objects=task['objects'],
            cutoff=task['cutoff'], questions=question_specs(task), calculations=task['calculation_specs'],
            evidence_contract=evidence_contract(task, self.views), field_registry={},
            untrusted_sources=self.views, source_reading=None)
        observer = ExtractionObserver()
        with self.assertRaises(Captured):
            model_object(observer, task, 'extract', payload, {'evidence_specs'})
        body = json.loads(observer.messages[1]['content'])
        if body.get('format') == 'researchhelper.table_input': body = restore(body)
        return observer, body

    def branches(self, schema, slot):
        field = schema['properties']['requirements']['properties'][slot['key']]
        found = next((b for b in field['anyOf'] if b['properties']['status']['enum'] == ['found']), None)
        return found['properties']['items']['items']['anyOf'] if found else []

    def test_metadata_branch_is_described_without_mandatory_observation_or_span_fields(self):
        observer, body = self.observe()
        system = observer.messages[0]['content']
        slot = next(s for s in requirement_slots(self.task) if '用于来源标注' in s['text'])
        selected = next(b for b in self.branches(observer.schema, slot) if 'source_label_ref' in b['properties'])
        self.assertEqual(set(selected['properties']), {'id', 'kind', 'source_ref', 'source_label_ref', 'review'})
        self.assertIn('来源标注使用独立source_label_ref分支', system)
        self.assertIn('不再提交text_reference或temporal_reference', system)
        self.assertNotIn('text用text_reference选择实际页段或行；', system)
        self.assertNotIn('本次原文日期用来源绑定的observation_ref字符串', system)
        self.assertTrue(any(v.get('source_label_choices') for v in body['untrusted_sources']))
        self.assertEqual(observer.schema, original_span_output_schema(self.task, self.views))

    def test_prompt_branch_roundtrip_still_requires_source_and_applicability_checks(self):
        observer, _ = self.observe(); schema = observer.schema
        slots = requirement_slots(self.task)
        slot = next(s for s in slots if '用于来源标注' in s['text'])
        branch = next(b for b in self.branches(schema, slot) if 'source_label_ref' in b['properties'])
        wire = dict(version=12, binding_ref=schema['properties']['binding_ref']['enum'][0], selections=[],
            requirements={s['key']: dict(status='not_found', reason='仅核对提示分支，不认定原件缺数') for s in slots})
        wire['requirements'][slot['key']] = dict(status='found', items=[literal(branch)])
        result = decode_original_spans(wire, schema, (self.task, self.views))
        self.assertNotIn(ORIGINAL_SELECTION_REJECTIONS, result)
        self.assertEqual(len(result['evidence_specs']), 1)
        spec = result['evidence_specs'][0]
        source = next(s for s in self.task['source_records'] if s['id'] == spec['artifact_id'])
        item = candidate_evidence(self.task, source, spec)
        self.assertEqual(item['source_check'], '通过'); self.assertEqual(item['applicability_check'], '待核验')
        ResearchAnalyzer().assess(self.task, item, spec['review'], self.task['source_records'])
        validate_evidence(item, self.task)
        self.assertNotIn('observation_date', item); self.assertIsNone(item['published_at'])

    def test_monthly_numeric_branches_keep_their_own_date_and_number_references(self):
        observer, _ = self.observe()
        for slot in requirement_slots(self.task):
            if slot['constraints']['value_kind'] != 'number': continue
            branches = self.branches(observer.schema, slot)
            self.assertTrue(branches)
            for branch in branches:
                fields = branch['properties']
                self.assertIn('number_reference', fields); self.assertIn('temporal_reference', fields)
                self.assertNotIn('source_label_ref', fields)

    def test_date_only_branch_remains_available_but_is_not_a_financial_observation(self):
        observer, _ = self.observe()
        slot = next(s for s in requirement_slots(self.task) if '用于来源标注' in s['text'])
        branches = [b for b in self.branches(observer.schema, slot) if 'date_role' in b['properties']]
        self.assertTrue(branches)
        self.assertTrue(all(b['properties']['date_role']['enum'] == ['document_declared_publication'] for b in branches))
        self.assertIn('金融观测日期、完整报告区间与原页标注日期分开', observer.messages[0]['content'])

    def test_legacy_without_page_property_policies_keeps_original_guidance(self):
        # Detached grammar comparison only; no returned evidence is adopted.
        task = copy.deepcopy(self.task)
        task.pop('source_label_selection_version'); task.pop('document_date_role_version')
        observer, body = self.observe(task)
        self.assertNotIn('来源标注使用独立source_label_ref分支', observer.messages[0]['content'])
        self.assertIn('text用text_reference选择实际页段或行；', observer.messages[0]['content'])
        self.assertFalse(any(v.get('source_label_choices') for v in body['untrusted_sources']))


if __name__ == '__main__': unittest.main()
