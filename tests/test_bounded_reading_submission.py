"""Real catalog bounds instead of model-invented short-page character counts."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.model import ModelService
from researchhelper.output_schema import (reading_output_schema, decode_reading_output,
    structured_completion, READING_PROTOCOL, READING_FUNCTION_NAME)
from researchhelper.reasoning import model_object
from researchhelper.source_reader import catalog, binding, read, ReadingSelectionError

ROOT = Path(__file__).resolve().parents[1]


class BoundedReadingSubmissionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='bounded-reading-', dir=ROOT/'result/acceptance')
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(Path(folder.name)/'store'), ROOT)
        self.task = self.agent.create('核对材料的收入口径', objects=['合成材料'])
        self.task = self.agent.add_material(self.task['id'], 'scope.txt',
            '发布日期：2026年7月19日。\n营业收入的单位为元。风险：合并范围变化。'.encode())
        self.description = catalog(self.task)
        item = self.description['items'][0]
        self.request = dict(item['request_binding'], queries=['营业收入'],
            parts=[copy.deepcopy(item['reading_spans'][0])], reason='核对原值与限制', question_ids=['q1'])
        self.schema = reading_output_schema(self.task, self.description)
        self.wire = {'version': 1, 'requests': [self.request]}

    def test_catalog_exposes_only_real_bounded_spans(self):
        span = self.request['parts'][0]
        material = self.task['materials'][0]['snapshot']
        part = next(p for p in material['parts'] if p['locator'] == span['part'])
        self.assertEqual(span, dict(part=part['locator'], start=0, end=len(part['text'])))
        result = decode_reading_output(self.wire, self.schema)
        bundle = read(self.task, result['requests'], binding(self.task))
        self.assertTrue(bundle['views'])
        self.assertFalse(self.task['evidence'])

    def test_guessing_8000_on_short_page_is_not_a_valid_submission(self):
        wire = copy.deepcopy(self.wire)
        wire['requests'][0]['parts'][0]['end'] = 8000
        with self.assertRaises(ContractError): decode_reading_output(wire, self.schema)
        with self.assertRaises(ReadingSelectionError) as rejected:
            read(self.task, wire['requests'], binding(self.task))
        self.assertEqual(rejected.exception.diagnostic['code'], 'invalid_span')

    def test_keyword_only_can_search_whole_original(self):
        wire = copy.deepcopy(self.wire); wire['requests'][0]['parts'] = []
        selected = decode_reading_output(wire, self.schema)
        self.assertTrue(read(self.task, selected['requests'], binding(self.task))['views'])

    def test_forged_catalog_and_other_task_binding_rejected(self):
        forged = copy.deepcopy(self.description); forged['items'][0]['reading_spans'][0]['end'] = 8000
        with self.assertRaises(ContractError): reading_output_schema(self.task, forged)
        other = copy.deepcopy(self.description); other['binding']['task_id'] = 'other'
        with self.assertRaises(ContractError): reading_output_schema(self.task, other)

    def test_wrong_source_and_unoffered_span_rejected_without_substitution(self):
        for changed in ('artifact_id', 'part'):
            wire = copy.deepcopy(self.wire)
            if changed == 'artifact_id': wire['requests'][0][changed] = 'not-offered'
            else: wire['requests'][0]['parts'][0][changed] = '第999页'
            with self.assertRaises(ContractError): decode_reading_output(wire, self.schema)

    def test_wire_roundtrip_removes_only_version_and_keeps_exact_request(self):
        message = {'role': 'assistant', 'tool_calls': [{'id': 'submission', 'type': 'function',
            'function': {'name': READING_FUNCTION_NAME, 'arguments': json.dumps(self.wire)}}]}
        result = json.loads(structured_completion(message, 'tool_calls', self.schema, protocol=READING_PROTOCOL))
        self.assertEqual(result, {'requests': [self.request]})

    def test_model_phase_routes_through_bound_reading_protocol(self):
        outer = self
        class Model:
            def supports_structured_analysis(self): return True
            def reason_structured_reading(self, task, messages, description):
                outer.assertEqual(description, outer.description)
                outer.assertIn(READING_FUNCTION_NAME, messages[0]['content'])
                return json.dumps(decode_reading_output(outer.wire, reading_output_schema(task, description)))
        payload = {'question': self.task['question'], 'source_catalog': self.description}
        original = fingerprint(self.task)
        result, receipt = model_object(Model(), self.task, 'read', payload, {'requests'})
        self.assertEqual(result['requests'], [self.request])
        self.assertEqual(receipt['output_protocol'], READING_PROTOCOL)
        self.assertEqual(fingerprint(self.task), original)

    def test_service_uses_same_schema_and_keeps_read_stage_budget(self):
        service = ModelService(None, offline=False)
        with patch.object(service, 'supports_structured_analysis', return_value=True), \
                patch.object(service, 'reason', return_value='submitted') as dispatch:
            service.reason_structured_reading(self.task, [], self.description)
        kwargs = dispatch.call_args.kwargs
        self.assertEqual(kwargs['phase'], 'read')
        self.assertEqual(kwargs['output_schema'], self.schema)
        self.assertEqual(kwargs['output_protocol'], READING_PROTOCOL)
        self.assertNotIn('delivery', kwargs)

