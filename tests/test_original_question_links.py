"""Single explicit question/need declarations, not inferred associations."""
import copy
import json
import socket
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT, test_named_quantity_catalogue as fixtures
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, canonical
from researchhelper.engine import ResearchAgent
from researchhelper.model import ModelService, ModelSettings
from researchhelper.output_schema import (StructuredOutputError, original_span_output_schema,
    original_span_prompt, decode_original_spans, provider_request_schema, ORIGINAL_SPAN_PROTOCOL)
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import MAX_CONTEXT_CHARACTERS, model_object


class OriginalQuestionLinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.NamedQuantityCatalogueTests.setUpClass()
        cls.base = fixtures.NamedQuantityCatalogueTests()
        cls.task = dict(copy.deepcopy(cls.base.task), original_question_link_version=1)
        cls.views, cls.payload = cls.base.views, cls.base.payload
        cls.schema = original_span_output_schema(cls.task, cls.views)
        cls.prompt = original_span_prompt(cls.payload, cls.task)

    @classmethod
    def tearDownClass(cls):
        fixtures.NamedQuantityCatalogueTests.tearDownClass()

    def setUp(self):
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline explicit link test'))
        guard.start()
        self.addCleanup(guard.stop)

    def wire(self):
        wire = self.base.wire()
        wire.update(version=11, binding_ref=self.prompt['span_binding_ref'])
        for selection in wire['selections']:
            selection.pop('question_ids')
        return wire

    def decode(self, wire=None):
        return decode_original_spans(wire or self.wire(), self.schema, (self.task, self.views))

    def test_model_declares_question_once_with_nonempty_needs(self):
        self.assertEqual(self.schema['properties']['version']['enum'], [11])
        for variant in self.schema['properties']['selections']['items']['anyOf']:
            self.assertNotIn('question_ids', variant['properties'])
            self.assertNotIn('question_ids', variant['required'])
            links = variant['properties']['review']['properties']['need_links']
            self.assertEqual(links['minItems'], 1)
            for link in links['items']['anyOf']:
                self.assertEqual(link['properties']['need_ids']['minItems'], 1)

    def test_explicit_single_link_restores_identical_proposal_without_approval(self):
        actual = self.decode()
        self.assertEqual(actual, self.base.decode())
        spec = actual['evidence_specs'][0]
        self.assertNotIn('source_check', spec)
        self.assertNotIn('applicability_check', spec)

    def test_multiple_explicit_links_restore_exactly_those_questions(self):
        wire = self.wire()
        selected = wire['selections'][0]
        first = selected['review']['need_links'][0]['question_id']
        variant = next(item for item in self.schema['properties']['selections']['items']['anyOf']
            if item['properties']['source_ref']['enum'] == [selected['source_ref']]
            and item['properties']['kind']['enum'] == ['number'])
        other = next(link for link in variant['properties']['review']['properties']['need_links']['items']['anyOf']
                     if link['properties']['question_id']['enum'] != [first])
        ident = other['properties']['question_id']['enum'][0]
        need = other['properties']['need_ids']['items']['enum'][0]
        selected['review']['need_links'].append(dict(question_id=ident, need_ids=[need]))
        spec = self.decode(wire)['evidence_specs'][0]
        self.assertEqual(spec['question_ids'], [first, ident])
        self.assertEqual(set(spec['review']['evidence_need_texts']), {first, ident})

    def test_empty_links_or_empty_needs_do_not_gain_default_associations(self):
        for empty in ('links', 'needs'):
            wire = self.wire()
            if empty == 'links':
                wire['selections'][0]['review']['need_links'] = []
            else:
                wire['selections'][0]['review']['need_links'][0]['need_ids'] = []
            with self.subTest(empty=empty), self.assertRaises(StructuredOutputError):
                self.decode(wire)

    def test_redundant_question_field_is_rejected_not_silently_overwritten(self):
        wire = self.wire()
        wire['selections'][0]['question_ids'] = ['q1']
        with self.assertRaises(StructuredOutputError):
            self.decode(wire)

    def test_duplicate_questions_and_unknown_need_ids_are_not_merged_or_repaired(self):
        for kind in ('duplicate', 'unknown'):
            wire = self.wire()
            links = wire['selections'][0]['review']['need_links']
            if kind == 'duplicate':
                links.append(copy.deepcopy(links[0]))
            else:
                links[0]['need_ids'] = ['unknown_need']
            with self.subTest(kind=kind), self.assertRaises(ContractError):
                self.decode(wire)

    def test_old_protocol_is_not_automatically_repaired_or_upgraded(self):
        self.assertEqual(self.base.schema['properties']['version']['enum'], [10])
        wire = self.base.wire()
        qid = wire['selections'][0]['question_ids'][0]
        alternate = next(ident for ident in self.base.prompt['evidence_contract']['question_requirements'] if ident != qid)
        wire['selections'][0]['question_ids'] = [alternate]
        with self.assertRaises(ContractError):
            self.base.decode(wire)

    def test_complete_sources_and_capacity_are_unchanged(self):
        self.assertEqual(self.prompt['untrusted_sources'], self.base.prompt['untrusted_sources'])
        self.assertEqual(self.prompt['questions'], self.base.prompt['questions'])
        wire_schema = provider_request_schema(self.task, self.schema, ORIGINAL_SPAN_PROTOCOL)
        self.assertLessEqual(len(canonical(self.prompt)) + len(canonical(wire_schema)), MAX_CONTEXT_CHARACTERS)

    def test_actual_transport_uses_new_declaration_once_without_new_inference(self):
        response = dict(object='response', status='completed', error=None, incomplete_details=None,
            output=[dict(type='message', role='assistant', status='completed',
                         content=[dict(type='output_text', text=json.dumps(self.wire()))])], usage={})
        task = copy.deepcopy(self.task)
        before = task['ledger']['used']['model_calls']
        with tempfile.TemporaryDirectory(prefix='original-links-', dir=ROOT / 'result/acceptance') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash',
                               api_key='FAKE_ORIGINAL_LINK_KEY', extraction_mode='direct'))
            with patch.object(ModelService, '_request_bytes', return_value=json.dumps(response).encode()) as transport:
                result, receipt = model_object(ModelService(settings, offline=False), task, 'extract',
                                               self.payload, {'evidence_specs'})
        self.assertEqual(result, self.decode())
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(task['ledger']['used']['model_calls'], before + 1)
        self.assertEqual(receipt['original_question_link_version'], 1)

    def test_policy_is_input_bound_and_not_applied_to_fixture_or_document_tasks(self):
        with tempfile.TemporaryDirectory(prefix='original-link-policy-', dir=ROOT / 'result/acceptance') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            task = agent.create('分析制造业PMI')
            self.assertEqual(task['original_question_link_version'], 1)
            changed = dict(task)
            changed.pop('original_question_link_version')
            self.assertNotEqual(input_fingerprint(task), input_fingerprint(changed))
            self.assertNotIn('original_question_link_version', agent.create_case('mechanism'))
            self.assertNotIn('original_question_link_version', agent.create('核对文件', intent='原件内容核对'))
