"""Deferred capabilities remain visible without becoming callable or approved."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import data_request_contract, validate_source_request
from researchhelper.financial_sources import metadata_request_shapes, source_capability_roadmap
from researchhelper.knowledge import ResearchKnowledge
from researchhelper.output_schema import planning_output_schema, provider_request_schema, PLAN_REQUEST_PROTOCOL
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import model_object, PHASE_FIELDS
from researchhelper.reports import compose, check_content

ROOT = Path(__file__).resolve().parents[1]
RETAINED = ROOT/'result/acceptance/one-sentence-research-tktf9nsc/task.json'
FLAG = 'source_capability_roadmap_version'


class PromptCaptured(RuntimeError):
    pass


class RecordingModel:
    def reason_research_plan(self, task, messages):
        self.messages = messages
        raise PromptCaptured('Stopped before any transport')


class SourceCapabilityRoadmapTests(unittest.TestCase):
    def setUp(self):
        self.original = RETAINED.read_bytes()
        self.old = json.loads(self.original)
        self.task = copy.deepcopy(self.old)
        self.task[FLAG] = 1
        self.task['input_hash'] = input_fingerprint(self.task)
        guard = patch('socket.socket.connect', side_effect=AssertionError('Offline only'))
        guard.start(); self.addCleanup(guard.stop)

    def tearDown(self):
        self.assertEqual(RETAINED.read_bytes(), self.original)

    def test_real_failed_plan_mistook_deferred_edb_for_unimplemented_capability(self):
        self.assertEqual(self.old['status'], '部分完成')
        requests = self.old['reasoning_state']['plan']['proposed']['data_requests']
        self.assertTrue(all(r['source']=='official' for r in requests))
        self.assertTrue(any('iFinD EDB' in r['selection_reason'] for r in requests))
        self.assertIsNone(source_capability_roadmap(self.old))
        self.assertFalse(any('capability_roadmap' in tool for tool in metadata_request_shapes(self.old)))

    def test_entry_describes_future_route_but_only_policy_is_callable(self):
        before = copy.deepcopy(self.task)
        roadmap = source_capability_roadmap(self.task)
        self.assertEqual(roadmap['routes'][0]['operations'], ['get_limit_policy','search_edb','edb_service'])
        self.assertFalse(roadmap['grants_permission'])
        self.assertFalse(roadmap['financial_approval'])
        self.assertEqual([t['operation'] for t in metadata_request_shapes(self.task)], ['get_limit_policy'])
        self.assertEqual(self.task, before)

    def test_actual_provider_schema_retains_guidance_after_factoring(self):
        schema = planning_output_schema(self.task)
        actual = provider_request_schema(self.task, schema, PLAN_REQUEST_PROTOCOL)
        text = json.dumps(actual, ensure_ascii=False)
        self.assertIn(source_capability_roadmap(self.task)['guidance'], text)
        self.assertIn('search_edb', text)
        self.assertIn('edb_service', text)

    def test_actual_system_prompt_explains_the_same_capability_route(self):
        model = RecordingModel()
        payload = dict(intent=self.task['intent'], question=self.task['question'],
                       data_request_contract=data_request_contract(self.task))
        with self.assertRaises(PromptCaptured):
            model_object(model,self.task,'plan',payload,PHASE_FIELDS['plan'])
        self.assertIn(source_capability_roadmap(self.task)['guidance'],model.messages[0]['content'])

    def test_roadmap_never_grants_unknown_indicator_or_permission(self):
        before = copy.deepcopy(self.task['ledger'])
        with self.assertRaises(ContractError):
            validate_source_request(self.task,'ifind','edb_service',dict(codes='M_UNRECEIVED',
                startdate='2026-01-01',enddate='2026-09-30'))
        self.assertEqual(self.task['ledger'],before)

    def test_versions_are_explicit_and_current_mode_only(self):
        for value in (True,'1',2,1.0):
            with self.assertRaises(ContractError):
                source_capability_roadmap(dict(self.task,**{FLAG:value}))
        historical = dict(self.task,research_mode='historical')
        self.assertIsNone(source_capability_roadmap(historical))
        self.assertIsNone(source_capability_roadmap(dict(self.task,intent='原件内容核对')))

    def test_only_implemented_task_features_are_advertised(self):
        task = copy.deepcopy(self.task);task.pop('economic_discovery_version')
        self.assertFalse(any('search_edb' in route['operations'] for route in source_capability_roadmap(task)['routes']))

    def test_policy_is_bound_and_freezes_into_new_reports(self):
        with tempfile.TemporaryDirectory() as folder:
            agent = ResearchAgent(ResearchArchive(Path(folder)),ROOT,offline=True,require_argument_support=True)
            task = agent.create('解释当前CPI观测的用途',entry='宏观',research_mode='current')
            self.assertEqual(task[FLAG],1)
            old = copy.deepcopy(task);old.pop(FLAG)
            self.assertNotEqual(input_fingerprint(task),input_fingerprint(old))
            task['knowledge']=ResearchKnowledge().read(task['entry'],task['question'],context=task)
            task['status']='部分完成';task['gaps']=['仅离线契约检查。']
            content=compose(task);check_content(content)
            self.assertEqual(content[FLAG],1)


if __name__=='__main__':unittest.main()
