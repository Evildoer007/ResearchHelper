"""Planning time follows owned task budget; no real network, mode or retry."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError,budget
from researchhelper.engine import ResearchAgent
from researchhelper.model import ModelService
from researchhelper.reasoning import model_object,phase_output_example

ROOT=Path(__file__).resolve().parents[1]


class OfflineSettings:
    def __init__(self):
        self.value=dict(endpoint='https://example.invalid',model='offline-fixture',api_key='OFFLINE_FAKE_NEVER_REAL')
    def read(self,private=False): return copy.deepcopy(self.value)


class ResearchPlanDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.settings=OfflineSettings();self.service=ModelService(self.settings,offline=False)
        self.task=dict(id='planning_deadline_fixture',batch=1,ledger=budget())
        self.messages=[dict(role='user',content='只规划原问题的必要证据与方法，不扩大范围。')]

    def execute(self,*,formal=True):
        original=copy.deepcopy(self.messages),copy.deepcopy(self.settings.value)
        response=json.dumps(dict(choices=[dict(finish_reason='stop',message=dict(content='{}'))])).encode()
        with patch.object(self.service,'_request_bytes',return_value=response) as request:
            result=(self.service.reason_research_plan(self.task,self.messages) if formal else
                    self.service.reason_phase(self.task,self.messages,'plan'))
        self.assertEqual(result,'{}');self.assertEqual((self.messages,self.settings.value),original)
        return self.task['ledger']['dispatches'][-1],json.loads(request.call_args.args[1].data)

    def test_formal_plan_uses_bounded_180_seconds(self):
        record,body=self.execute()
        self.assertEqual(record['timeout_seconds'],180)
        self.assertEqual(record['wait_policy'],'bounded_research_plan_v1')
        self.assertNotIn('thinking',body)
        self.assertEqual(body['max_tokens'],self.service.output_limit(self.settings.value))

    def test_legacy_phase_call_does_not_gain_planning_authority_from_text(self):
        self.messages[0]['content']='extract research_plan planning_deadline '*3000
        record,_=self.execute(formal=False)
        self.assertEqual(record['timeout_seconds'],90);self.assertNotIn('wait_policy',record)

    def test_remaining_task_time_and_delivery_reserve_take_priority(self):
        self.task['ledger']['limits']['seconds']=200;self.task['ledger']['used']['seconds']=70
        record,_=self.execute();self.assertEqual(record['timeout_seconds'],70)

    def test_stricter_transport_cap_is_preserved(self):
        self.service.MAX_WAIT_SECONDS=.25
        record,_=self.execute();self.assertEqual(record['timeout_seconds'],.25)

    def test_explicit_reasoning_effort_is_not_lowered(self):
        self.settings.value.update(endpoint='https://api.deepseek.com',model='deepseek-flash',reasoning_effort='high')
        _,body=self.execute()
        self.assertEqual(body['thinking'],{'type':'enabled'});self.assertEqual(body['reasoning_effort'],'high')

    def test_a_formal_plan_is_charged_once_and_does_not_reset_the_ledger(self):
        self.task['ledger']['used']['model_calls']=4
        self.execute();self.assertEqual(self.task['ledger']['used']['model_calls'],5)
        self.assertEqual(len(self.task['ledger']['dispatches']),1)

    def test_other_phases_and_delivery_cannot_borrow_the_planning_policy(self):
        for kwargs in (dict(phase='read'),dict(phase='extract'),dict(phase='plan',delivery=True),dict(phase=None)):
            with self.assertRaises(ContractError):
                self.service.reason(self.task,self.messages,planning_deadline=True,**kwargs)
        self.assertEqual(self.task['ledger']['used']['model_calls'],0)

    def test_nonboolean_policy_is_not_a_dispatch_permission(self):
        for value in (1,'true',{},None):
            with self.assertRaises(ContractError):
                self.service.reason(self.task,self.messages,phase='plan',planning_deadline=value)
        self.assertEqual(self.task['ledger']['used']['model_calls'],0)

    def test_exhausted_time_does_not_start_a_request(self):
        self.task['ledger']['used']['seconds']=self.task['ledger']['limits']['seconds']-60
        with patch.object(self.service,'_request_bytes') as request:
            with self.assertRaises(ContractError): self.service.reason_research_plan(self.task,self.messages)
        request.assert_not_called();self.assertEqual(self.task['ledger']['used']['model_calls'],0)

    def test_transport_failure_does_not_dispatch_a_second_request(self):
        with patch.object(self.service,'_request_bytes',side_effect=TimeoutError) as request:
            with self.assertRaises(ContractError): self.service.reason_research_plan(self.task,self.messages)
        self.assertEqual(request.call_count,1);self.assertEqual(self.task['ledger']['used']['model_calls'],1)

    def test_ordinary_engine_routes_formal_planning_through_the_owned_entry(self):
        temporary=tempfile.TemporaryDirectory(prefix='planning-entry-',dir=ROOT/'result/acceptance')
        self.addCleanup(temporary.cleanup)
        task=ResearchAgent(ResearchArchive(Path(temporary.name)/'store'),ROOT).create('解释机制与适用条件')
        calls=[]
        class CapturingModel:
            def reason_research_plan(self,task,messages):
                calls.append('formal');return json.dumps(phase_output_example('plan'))
            def reason_phase(self,*args): raise AssertionError('formal planning did not use the owned entry')
        model_object(CapturingModel(),task,'plan',dict(question=task['question']),{'questions','data_requests','calculations'})
        self.assertEqual(calls,['formal'])

    def test_explicit_strict_plan_forwards_the_same_policy(self):
        self.settings.value.update(endpoint='https://api.deepseek.com/beta/chat/completions',model='deepseek-flash')
        with patch.object(self.service,'reason',return_value='{}') as reason, \
                patch('researchhelper.output_schema.planning_output_schema',return_value={'type':'object'}):
            self.service.reason_structured_plan(self.task,self.messages)
        self.assertEqual(reason.call_args.kwargs['phase'],'plan')
        self.assertIs(reason.call_args.kwargs['planning_deadline'],True)


if __name__=='__main__': unittest.main()
