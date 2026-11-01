"""One authoritative definition catalogue for plans; no numeric approval or retries."""
import copy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.knowledge import ResearchKnowledge
from researchhelper.model import ModelService, ModelSettings, ModelCompletionError
from researchhelper.output_schema import planning_output_schema, planning_protocol, decode_planning_output, StructuredOutputError
from researchhelper.reasoning import model_object, planning_digest, source_views, RESEARCH_BRIEF_VERSION


RETAINED = ROOT / 'result/acceptance/one-sentence-research-z90yx8ck/task.json'


class PlanningDefinitionContractTests(unittest.TestCase):
    def setUp(self):
        self.original = RETAINED.read_bytes()
        folder = tempfile.TemporaryDirectory(prefix='planning-definition-', dir=ROOT / 'result/acceptance')
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.agent = ResearchAgent(ResearchArchive(self.folder / 'store'), ROOT,
            offline=True, require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task = self.agent.create('解释ETF折溢价为什么不等于可实现套利', entry='ETF', questions=[dict(
            id='q1', kind='fact', question='ETF折溢价为什么不等于可实现套利', answer_scope='general_definition',
            knowledge_needs=['rh_k_etf_premium_measures'])])
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No networking'))
        guard.start(); self.addCleanup(guard.stop)

    def tearDown(self):
        self.assertEqual(RETAINED.read_bytes(), self.original)

    def wire(self):
        return dict(version=2, questions=[dict(id='q1', kind='fact', required=True, group='q1',
            question='ETF折溢价为什么不等于可实现套利', answer_scope='general_definition',
            knowledge_needs=['rh_k_etf_premium_measures'])], data_requests=[], calculations=[])

    def test_new_tasks_enable_scope_without_reopening_old_or_fixture_tasks(self):
        self.assertEqual(self.task['planning_definition_scope_version'], 1)
        self.assertNotIn('planning_definition_scope_version', json.loads(self.original))
        self.assertNotIn('planning_definition_scope_version', self.agent.create_case('mechanism'))

    def test_schema_and_candidate_catalogue_match_for_all_eight_entries(self):
        for entry in ('个股', 'ETF', '指数', '行业', '产业链', '事件', '宏观', '混合'):
            task = dict(self.task, entry=entry)
            candidates = ResearchKnowledge().planning_definitions(task)['candidates']
            shapes = planning_output_schema(task)['properties']['questions']['items']['anyOf']
            actual = [value['properties']['knowledge_needs']['items']['enum']
                for value in shapes if 'knowledge_needs' in value['properties']]
            self.assertEqual(actual, [[item['id'] for item in candidates]] if candidates else [])
            self.assertNotIn('rh_k_nav_premium', [item['id'] for item in candidates])

    def test_candidates_are_detached_and_do_not_approve_facts_or_computations(self):
        knowledge = ResearchKnowledge()
        before = knowledge.planning_definitions(self.task)
        changed = knowledge.planning_definitions(self.task)
        self.assertFalse(changed['fact_approval']); self.assertFalse(changed['calculation_approval'])
        changed['candidates'][0]['content'] = 'changed'
        self.assertEqual(knowledge.planning_definitions(self.task), before)

    def test_plan_checkpoint_binds_the_definition_versions_and_full_candidate_content(self):
        original = planning_digest(self.task)
        changed = ResearchKnowledge().planning_definitions(self.task)
        changed['candidates'][0]['version'] += 1
        changed['candidates'][0]['conditions'] += '不同条件'
        with patch.object(ResearchKnowledge, 'planning_definitions', return_value=changed):
            self.assertNotEqual(planning_digest(self.task), original)

    def test_legacy_plan_digest_keeps_its_existing_input_contract(self):
        self.task.pop('planning_definition_scope_version')
        expected = fingerprint(dict(input_hash=self.task['input_hash'], sources=source_views(self.task),
            research_brief_version=RESEARCH_BRIEF_VERSION))
        self.assertEqual(planning_digest(self.task), expected)

    def test_method_and_invented_definition_ids_remain_rejected(self):
        for ident in ('rh_k_nav_premium', 'invented_definition'):
            wire = self.wire(); wire['questions'][0]['knowledge_needs'] = [ident]
            with self.assertRaises(StructuredOutputError):
                decode_planning_output(wire, planning_output_schema(self.task))

    def service(self, responses=False):
        settings = ModelSettings(self.folder / ('responses' if responses else 'beta'))
        settings.save(dict(endpoint='https://api.deepseek.com/responses' if responses else
            'https://api.deepseek.com/beta/chat/completions', model='deepseek-flash', api_key='SYNTHETIC_DEFINITION_KEY'))
        return ModelService(settings, offline=False)

    def response(self, responses=False, wire=None):
        text = json.dumps(wire or self.wire())
        if responses:
            return json.dumps(dict(object='response', status='completed', error=None, incomplete_details=None,
                output=[dict(type='message', role='assistant', status='completed',
                    content=[dict(type='output_text', text=text)])], usage={})).encode()
        return json.dumps(dict(choices=[dict(finish_reason='tool_calls', message=dict(role='assistant', content=None,
            tool_calls=[dict(id='offline_definition_plan', type='function', function=dict(
                name=planning_protocol(self.task)[1], arguments=text))]))], usage={})).encode()

    def model_plan(self, model, payload=None):
        return model_object(model, self.task, 'plan', payload or dict(question=self.task['question']),
            {'questions', 'data_requests', 'calculations'})

    def test_actual_beta_request_has_same_catalogue_as_schema(self):
        model = self.service()
        with patch.object(model, '_request_bytes', return_value=self.response()) as transport:
            result, receipt = self.model_plan(model)
        request = json.loads(transport.call_args.args[1].data)
        payload = json.loads(request['messages'][-1]['content'])
        self.assertEqual(payload['planning_definition_contract'], ResearchKnowledge().planning_definitions(self.task))
        self.assertIn('planning_definition_contract.candidates', request['messages'][0]['content'])
        self.assertNotIn('只能选择本次知识包中knowledge_role', request['messages'][0]['content'])
        self.assertEqual(result['questions'][0]['knowledge_needs'], ['rh_k_etf_premium_measures'])
        self.assertEqual(receipt['planning_definition_scope_version'], 1)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 1)

    def test_actual_responses_request_uses_same_definition_contract(self):
        model = self.service(responses=True)
        with patch.object(model, '_request_bytes', return_value=self.response(responses=True)) as transport:
            result, receipt = self.model_plan(model)
        sent = json.loads(transport.call_args.args[1].data)
        self.assertIn('planning_definition_contract', json.dumps(sent, ensure_ascii=False))
        self.assertEqual(receipt['planning_definition_scope_version'], 1)
        self.assertEqual(result['questions'][0]['knowledge_needs'], ['rh_k_etf_premium_measures'])

    def test_forged_candidate_contract_is_rejected_before_dispatch(self):
        model = self.service()
        with patch.object(model, '_request_bytes') as transport:
            with self.assertRaises(ContractError):
                self.model_plan(model, dict(planning_definition_contract={'candidates': []}))
        transport.assert_not_called()
        self.assertEqual(self.task['ledger']['used']['model_calls'], 0)

    def test_completed_invalid_reference_is_rejected_without_repair_or_second_call(self):
        wire = self.wire()
        wire['questions'][0]['knowledge_needs'] = ['rh_k_nav_premium']
        model = self.service()
        with patch.object(model, '_request_bytes', return_value=self.response(wire=wire)) as transport:
            with self.assertRaises(ModelCompletionError):
                self.model_plan(model)
        transport.assert_called_once()
        self.assertEqual(self.task['ledger']['used']['model_calls'], 1)
        self.assertEqual(self.task['ledger']['used']['data_requests'], 0)
        self.assertNotIn('planning_submission_recovery', self.task.get('reasoning_state', {}))
        self.assertFalse(self.task['evidence'] or self.task['calculations'])

    def test_legacy_task_keeps_original_prompt_without_new_contract(self):
        self.task.pop('planning_definition_scope_version')
        model = self.service()
        with patch.object(model, '_request_bytes', return_value=self.response()) as transport:
            _, receipt = self.model_plan(model)
        sent = json.loads(transport.call_args.args[1].data)
        self.assertNotIn('planning_definition_contract', json.loads(sent['messages'][-1]['content']))
        self.assertIn('只能选择本次知识包中knowledge_role', sent['messages'][0]['content'])
        self.assertNotIn('planning_definition_scope_version', receipt)

    def test_new_concept_delivers_only_explanation_not_market_or_arbitrage_approval(self):
        self.agent.archive.save(self.task)
        done = self.agent.start(self.task['id'])
        self.assertEqual(done['status'], '已完成', done.get('error'))
        self.assertFalse(done['evidence']); self.assertFalse(done['calculations']); self.assertFalse(done['theses'])
        self.assertFalse(done['questions'][0]['knowledge_integrity']['fact_approval'])
        self.assertIn('冲击成本', done['questions'][0]['answer'])
        self.assertEqual(self.agent.export(done['id'])['content']['planning_definition_scope_version'], 1)
