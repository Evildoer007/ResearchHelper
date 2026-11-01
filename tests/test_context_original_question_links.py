"""One explicit need owner even when a method needs financial context.

Actual retained text is replayed read-only. Decoding is not evidence approval.
"""
import copy
import json
import tempfile
import unittest
from unittest.mock import patch

from tests import test_annual_fee_difference as fixtures
from researchhelper.contracts import ContractError, canonical
from researchhelper.output_schema import (original_span_output_schema, original_span_prompt,
    decode_original_spans, StructuredOutputError)
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import MAX_CONTEXT_CHARACTERS

POLICY = 'original_context_question_link_version'


def example(shape):
    """Produce a local text-location fixture, never financial quantities."""
    if 'enum' in shape:return shape['enum'][0]
    if 'anyOf' in shape:return example(shape['anyOf'][0])
    kind = shape.get('type')
    if kind == 'object':return {key:example(value) for key,value in shape['properties'].items()}
    if kind == 'array':return [example(shape['items'])] if shape.get('minItems', 0) else []
    if kind == 'integer':return shape.get('minimum', 0)
    if kind == 'string':return 'fixture'
    raise AssertionError('Unexpected fixture schema')


class ContextOriginalQuestionLinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.AnnualFeeSourceContractTests.setUpClass()
        cls.base = fixtures.AnnualFeeSourceContractTests
        cls.task = dict(cls.base.task, **{POLICY:1})
        cls.task['input_hash'] = input_fingerprint(cls.task)
        cls.views = cls.base.views
        cls.schema = original_span_output_schema(cls.task, cls.views)

    @classmethod
    def tearDownClass(cls):fixtures.AnnualFeeSourceContractTests.tearDownClass()

    def wire(self, schema=None):
        schema = schema or self.schema
        variant = next(v for v in schema['properties']['selections']['items']['anyOf']
                       if v['properties']['kind']['enum'] == ['text'])
        item = example(variant)
        need_shape = variant['properties']['review']['properties']['need_links']['items']['anyOf'][0]
        link = example(need_shape)
        link['need_ids'] = [need_shape['properties']['need_ids']['items']['enum'][0]]
        item['review']['need_links'] = [link]
        return dict(version=12, binding_ref=schema['properties']['binding_ref']['enum'][0], selections=[item])

    def decode(self, wire=None):
        return decode_original_spans(wire or self.wire(), self.schema, (self.task,self.views))

    def test_context_formats_declare_owner_only_in_nonempty_need_links(self):
        self.assertEqual(self.schema['properties']['version']['enum'], [12])
        for variant in self.schema['properties']['selections']['items']['anyOf']:
            props = variant['properties']
            self.assertNotIn('question_ids', props)
            self.assertEqual(props['review']['properties']['need_links']['minItems'], 1)
            for choice in props['review']['properties']['need_links']['items']['anyOf']:
                self.assertEqual(choice['properties']['need_ids']['minItems'], 1)
            if props['kind']['enum'] == ['number']:
                fields = {entry['properties']['field']['enum'][0] for entry in props['contexts']['items']['anyOf']}
                self.assertEqual(fields, {'fund_id','share_class','period','fee_basis','rate_period','metric'})

    def test_explicit_links_expand_to_identical_legacy_business_proposal(self):
        old = self.wire(self.base.schema)
        old['selections'][0]['question_ids'] = [link['question_id'] for link in old['selections'][0]['review']['need_links']]
        expected = decode_original_spans(old, self.base.schema, (self.base.task,self.views))
        actual = self.decode()
        self.assertEqual(actual, expected)
        self.assertNotIn('source_check',actual['evidence_specs'][0])

    def test_extra_owner_empty_or_duplicate_links_never_get_repaired(self):
        for change in ('extra', 'empty_links', 'empty_needs', 'duplicate'):
            wire = self.wire(); item = wire['selections'][0]
            if change == 'extra':item['question_ids'] = ['q1']
            elif change == 'empty_links':item['review']['need_links'] = []
            elif change == 'empty_needs':item['review']['need_links'][0]['need_ids'] = []
            else:item['review']['need_links'].append(copy.deepcopy(item['review']['need_links'][0]))
            with self.subTest(change=change), self.assertRaises(ContractError):self.decode(wire)

    def test_legacy_and_boolean_policy_keep_original_container(self):
        for value in (None, True, False, 0, 2):
            task = dict(self.base.task, **{POLICY:value})
            task['input_hash'] = input_fingerprint(task)
            schema = original_span_output_schema(task,self.views)
            self.assertTrue(all('question_ids' in v['properties']
                                for v in schema['properties']['selections']['items']['anyOf']))

    def test_complete_sources_and_financial_contracts_fit_unchanged_capacity(self):
        prompt = original_span_prompt(self.base.payload,self.task)
        self.assertEqual(len(prompt['untrusted_sources']),len(self.views))
        self.assertEqual(prompt['questions'],self.base.payload['questions'])
        self.assertLessEqual(len(canonical(prompt))+len(canonical(self.schema)),MAX_CONTEXT_CHARACTERS)

    def test_unknown_need_cannot_borrow_another_question(self):
        wire = self.wire()
        wire['selections'][0]['review']['need_links'][0]['need_ids'] = ['unknown']
        with self.assertRaises(StructuredOutputError):self.decode(wire)

    def test_adapter_once_uses_same_owner_projection_without_external_calls(self):
        from researchhelper.model import ModelService, ModelSettings
        from researchhelper.reasoning import model_object
        response = dict(object='response',status='completed',error=None,incomplete_details=None,
            output=[dict(type='message',role='assistant',status='completed',
                         content=[dict(type='output_text',text=json.dumps(self.wire()))])],usage={})
        task = copy.deepcopy(self.task)
        before = task['ledger']['used']['model_calls']
        with tempfile.TemporaryDirectory() as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses',model='deepseek-flash',
                               api_key='LOCAL_ONLY_FIXTURE',extraction_mode='direct'))
            with patch.object(ModelService,'_request_bytes',return_value=json.dumps(response).encode()) as transport:
                result, receipt = model_object(ModelService(settings,offline=False),task,'extract',
                    self.base.payload,{'evidence_specs'})
        self.assertEqual(result,self.decode())
        self.assertEqual(transport.call_count,1)
        self.assertEqual(task['ledger']['used']['model_calls'],before+1)
        self.assertEqual(receipt[POLICY],1)
