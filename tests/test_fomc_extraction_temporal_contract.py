"""Bind date representation to the adapter's actual temporal capability.

Saved studies are read-only. Mocked model transports exercise the real schema
gate; none of these tests assert real model quality or publication approval.
"""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.model import ModelService, ModelSettings
from researchhelper.output_schema import (material_extraction_output_schema, _matches,
    decode_material_extraction_output, MATERIAL_EXTRACTION_FUNCTION_NAME,
    MATERIAL_EXTRACTION_PROTOCOL, StructuredOutputError)
from researchhelper.reasoning import evidence_contract, extractable_source_views
from researchhelper.evidence import candidate_evidence


RETAINED = ROOT / 'result/acceptance/one-sentence-research-r_5_wxfy/task.json'
FLAG = 'official_temporal_contract_version'


class FOMCExtractionTemporalContractTests(unittest.TestCase):
    def setUp(self):
        self.original = RETAINED.read_bytes()
        self.task = json.loads(self.original)
        self.task[FLAG] = 1
        self.views = extractable_source_views(self.task)
        self.record = next(record for record in self.task['source_records'] if record['operation'] == 'release_original')
        self.header = next(part for part in self.views[0]['parts'] if part['text'] == 'September 16, 2026')
        self.body = next(part for part in self.views[0]['parts'] if part['text'].startswith('The Committee decided'))
        self.source = evidence_contract(self.task, self.views)['sources'][0]
        network = patch('socket.socket.connect', side_effect=AssertionError('No network'))
        network.start()
        self.addCleanup(network.stop)

    def tearDown(self):
        self.assertEqual(RETAINED.read_bytes(), self.original)

    def wire(self):
        question = self.task['questions'][0]
        return dict(version=1, evidence_specs=[dict(id='statement_action', artifact_id=self.record['id'],
            object='FOMC', text=self.body['text'], period='2026-09-16', nature='合同政策条款',
            unit='不适用', question_ids=[question['id']],
            record_locator=dict(part=self.body['locator'], period_part=self.header['locator'],
                period_quote=self.header['text']),
            availability=self.source['current_availability_reference'], publication_mode='unknown_current_original',
            review=dict(question_ids=[question['id']], allowed_natures=['合同政策条款'],
                conditions=['仅核对该日期声明当前版本，不证明历史或因果'], reason='逐字动作与日期标题',
                temporal_use='current_version', evidence_need_texts=[dict(question_id=question['id'],
                    texts=[question['evidence_needs'][0]])]))])

    def test_new_tasks_enable_contract_without_upgrading_fixtures(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'result/acceptance') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True)
            self.addCleanup(agent.close_runtime)
            self.assertEqual(agent.create('核对公开事件')[FLAG], 1)
            self.assertNotIn(FLAG, agent.create_case('mechanism'))

    def test_contract_exposes_only_registered_temporal_use_and_literal_anchor(self):
        contract = self.source['publication_mode_contract']
        self.assertEqual(contract['allowed_modes'], ['unknown_current_original'])
        self.assertFalse(contract['first_publication_verified'])
        self.assertEqual(contract['date_candidates'], [dict(date='2026-09-16',
            part=self.header['locator'], quote=self.header['text'])])

    def test_current_wire_decodes_with_unknown_publication_and_no_automatic_approval(self):
        schema = material_extraction_output_schema(self.task, self.views)
        wire = self.wire()
        self.assertTrue(_matches(wire, schema))
        decoded = decode_material_extraction_output(wire, schema)['evidence_specs'][0]
        self.assertIsNone(decoded['published_at'])
        self.assertIsNone(decoded['publication']['at'])
        evidence = candidate_evidence(self.task, self.record, decoded)
        self.assertEqual(evidence['source_check'], '通过')
        self.assertEqual(evidence['applicability_check'], '待核验')
        self.assertFalse(evidence['availability']['historical_availability_approved'])

    def test_unsupported_full_publication_wire_is_rejected_before_adoption(self):
        wire = self.wire()
        old = copy.deepcopy(self.task['host_result']['evidence_specs'][0])
        old['review']['evidence_need_texts'] = wire['evidence_specs'][0]['review']['evidence_need_texts']
        wire['evidence_specs'] = [old]
        schema = material_extraction_output_schema(self.task, self.views)
        self.assertFalse(_matches(wire, schema))
        with self.assertRaises(StructuredOutputError):
            decode_material_extraction_output(wire, schema)

    def test_wrong_date_anchor_or_first_publication_field_cannot_match(self):
        schema = material_extraction_output_schema(self.task, self.views)
        for key, value in (('period', '2026-09-17'), ('published_at', '2026-09-16')):
            wire = self.wire()
            wire['evidence_specs'][0][key] = value
            self.assertFalse(_matches(wire, schema))
        wire = self.wire()
        wire['evidence_specs'][0]['record_locator']['period_part'] = self.body['locator']
        self.assertFalse(_matches(wire, schema))

    def test_legacy_schema_keeps_its_previous_publication_choices(self):
        self.task.pop(FLAG)
        contract = evidence_contract(self.task, self.views)
        self.assertNotIn('publication_mode_contract', contract['sources'][0])
        schema = material_extraction_output_schema(self.task, self.views)
        choices = schema['properties']['evidence_specs']['items']['anyOf']
        self.assertTrue(any('published_at' in choice['properties'] for choice in choices))

    def test_local_document_is_not_restricted_by_fomc_capability(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'result/acceptance') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True)
            self.addCleanup(agent.close_runtime)
            task = agent.create('核对这份原件的明确声明')
            task = agent.add_material(task['id'], 'dated.txt', '发布日期：2026年9月16日。声明内容。'.encode())
            views = extractable_source_views(task)
            self.assertNotIn('publication_mode_contract', evidence_contract(task, views)['sources'][0])
            choices = material_extraction_output_schema(task, views)['properties']['evidence_specs']['items']['anyOf']
            self.assertTrue(any('published_at' in choice['properties'] for choice in choices))

    def test_frozen_report_preserves_the_temporal_contract_version(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'result/acceptance') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            task = agent.create('解释价格变化与总收益，不查询当前数值', questions=[dict(id='q1', kind='fact',
                question='价格变化与总收益的区别', answer_scope='general_definition',
                knowledge_needs=['rh_k_price_return_measures'])])
            done = agent.start(task['id'])
            self.assertEqual(done['status'], '已完成', done.get('error'))
            self.assertEqual(agent.export(task['id'])['content'][FLAG], 1)

    def test_actual_model_service_rebuilds_same_schema_for_both_transports(self):
        schema = material_extraction_output_schema(self.task, self.views)
        for endpoint in ('https://api.deepseek.com/beta/chat/completions', 'https://api.deepseek.com/responses'):
            with self.subTest(endpoint=endpoint), tempfile.TemporaryDirectory() as folder:
                task = copy.deepcopy(self.task)
                settings = ModelSettings(Path(folder))
                settings.save(dict(endpoint=endpoint, model='deepseek-flash', api_key='FAKE_TEMPORAL_TEST_KEY'))
                model = ModelService(settings, offline=False)
                if endpoint.endswith('responses'):
                    response = dict(object='response', status='completed', error=None, incomplete_details=None,
                        output=[dict(type='message', role='assistant', status='completed',
                            content=[dict(type='output_text', text=json.dumps(self.wire()))])], usage={})
                else:
                    response = dict(choices=[dict(finish_reason='tool_calls', message=dict(role='assistant', content=None,
                        tool_calls=[dict(id='temporal', type='function', function=dict(
                            name=MATERIAL_EXTRACTION_FUNCTION_NAME, arguments=json.dumps(self.wire())))]))], usage={})
                with patch.object(model, '_request_bytes', return_value=json.dumps(response).encode()) as transport:
                    result = json.loads(model.reason(task, [], phase='extract', output_schema=schema,
                        output_protocol=MATERIAL_EXTRACTION_PROTOCOL, schema_context=self.views))
                transport.assert_called_once()
                self.assertIsNone(result['evidence_specs'][0]['published_at'])
                self.assertEqual(task['ledger']['used']['model_calls'], self.task['ledger']['used']['model_calls'] + 1)
