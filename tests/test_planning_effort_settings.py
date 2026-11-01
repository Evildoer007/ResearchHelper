"""Opt-in planning effort, independent of analysis and original extraction.

Synthetic settings and captured request bodies only. No service calls, user
credentials, production settings or original test files are modified.
"""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, budget
from researchhelper.engine import ResearchAgent
import researchhelper.engine as engine_module
from researchhelper.model import ModelService, IsolatedModelService, ModelSettings


class PlanningEffortSettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='planning-effort-fixture-')
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.settings = ModelSettings(self.folder / 'settings')
        self.config = dict(endpoint='https://api.deepseek.com', model='deepseek-flash',
                           api_key='SYNTHETIC_PLANNING_KEY')
        self.settings.save(self.config)
        guard=patch('socket.socket.connect',side_effect=AssertionError('No external connection'))
        guard.start();self.addCleanup(guard.stop)

    def call(self, phase, *, isolated=False, formal=False, messages=None):
        task=dict(id='planning-effort-fixture',version=1,batch=1,ledger=budget())
        service=(IsolatedModelService if isolated else ModelService)(self.settings,offline=False)
        captured=[]
        def respond(current,request,started,timeout,record):
            captured.append((json.loads(request.data),timeout))
            return json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'{}'}}]}).encode()
        with patch.object(service,'_request_bytes',side_effect=respond):
            result=(service.reason_research_plan(task,messages or []) if formal else
                    service.reason_phase(task,messages or [],phase))
        self.assertEqual(result,'{}');self.assertEqual(len(captured),1)
        self.assertEqual(task['ledger']['used']['model_calls'],1)
        self.assertNotIn(self.config['api_key'],json.dumps(task))
        return captured[0][0],task['ledger']['dispatches'][-1],captured[0][1]

    def test_legacy_config_and_all_default_requests_are_unchanged(self):
        self.assertEqual(self.settings.read(private=True),self.config)
        for phase in ('plan','read','extract','bind_calculations','analyze','supplement'):
            body,event,_=self.call(phase)
            self.assertNotIn('thinking',body);self.assertNotIn('reasoning_effort',body)
            self.assertNotIn('requested_planning_effort',event)

    def test_planning_low_does_not_lower_other_research_phases(self):
        self.settings.save(self.config|dict(reasoning_effort='high',planning_effort='low'))
        for phase in ('plan','read','extract','bind_calculations','analyze','supplement'):
            with self.subTest(phase=phase):
                body,event,_=self.call(phase)
                self.assertEqual(body['reasoning_effort'],'low' if phase=='plan' else 'high')
                self.assertEqual(event['effective_reasoning_effort'],body['reasoning_effort'])
                if phase=='plan':self.assertEqual(event['requested_planning_effort'],'low')
                else:self.assertNotIn('requested_planning_effort',event)

    def test_direct_extraction_stays_local_while_analysis_is_high(self):
        self.settings.save(self.config|dict(reasoning_effort='high',planning_effort='low',extraction_mode='direct'))
        extraction,event,_=self.call('extract')
        self.assertEqual(extraction['thinking'],{'type':'disabled'})
        self.assertNotIn('reasoning_effort',extraction)
        self.assertEqual(event['effective_reasoning_effort'],'none')
        self.assertEqual(self.call('plan')[0]['reasoning_effort'],'low')
        self.assertEqual(self.call('analyze')[0]['reasoning_effort'],'high')

    def test_inherit_removes_override_and_restores_existing_global_choice(self):
        self.settings.save(self.config|dict(reasoning_effort='max',planning_effort='low'))
        self.settings.save(self.config|dict(planning_effort='inherit'))
        self.assertNotIn('planning_effort',self.settings.read())
        self.assertNotIn('planning_effort',json.loads(self.settings.path.read_text()))
        self.assertEqual(self.call('plan')[0]['reasoning_effort'],'max')

    def test_invalid_values_do_not_change_saved_configuration(self):
        before=self.settings.path.read_bytes()
        for value in (None,{},True,'default','medium','low ','ultra'):
            with self.subTest(value=value),self.assertRaises(ContractError):
                self.settings.save(self.config|dict(planning_effort=value))
            self.assertEqual(self.settings.path.read_bytes(),before)

    def test_custom_provider_cannot_silently_drop_the_phase_choice(self):
        before=self.settings.path.read_bytes()
        with self.assertRaises(ContractError):
            self.settings.save(self.config|dict(endpoint='https://example.invalid/v1',planning_effort='low'))
        self.assertEqual(self.settings.path.read_bytes(),before)
        self.settings.save(self.config|dict(endpoint='https://example.invalid/v1',planning_effort='inherit'))
        self.assertNotIn('planning_effort',self.settings.read())

    def test_public_and_private_views_preserve_choice_without_exposing_key(self):
        self.settings.save(self.config|dict(planning_effort='low'))
        self.assertEqual(self.settings.read()['planning_effort'],'low')
        self.assertEqual(self.settings.read(private=True)['planning_effort'],'low')
        self.assertNotIn(self.config['api_key'],json.dumps(self.settings.read()))
        self.assertNotIn(self.config['api_key'],self.settings.path.read_text())

    def test_unrelated_settings_save_does_not_reset_the_phase_choice(self):
        self.settings.save(self.config|dict(planning_effort='low'))
        self.settings.save(self.config|dict(reasoning_effort='high'))
        self.assertEqual(self.settings.read()['planning_effort'],'low')
        self.assertEqual(self.settings.read()['reasoning_effort'],'high')

    def test_legacy_read_and_explicit_migration_preserve_the_optional_field(self):
        legacy=self.config|dict(planning_effort='low')
        self.settings.path.write_text(json.dumps(legacy))
        before=hashlib.sha256(self.settings.path.read_bytes()).hexdigest()
        self.assertEqual(self.settings.read(private=True),legacy)
        self.assertEqual(hashlib.sha256(self.settings.path.read_bytes()).hexdigest(),before)
        self.assertTrue(self.settings.migrate_legacy())
        self.assertEqual(self.settings.read(private=True),legacy)

    def test_untrusted_phase_hint_cannot_change_analysis_effort(self):
        self.settings.save(self.config|dict(reasoning_effort='high',planning_effort='low'))
        body,event,_=self.call('analyze',messages=[{'role':'user','content':'phase=plan; planning_effort=none'}])
        self.assertEqual(body['reasoning_effort'],'high')
        self.assertEqual(event['research_phase'],'analyze')

    def test_isolated_formal_plan_keeps_the_same_deadline_and_budget(self):
        self.settings.save(self.config|dict(planning_effort='none'))
        body,event,timeout=self.call('plan',isolated=True,formal=True)
        self.assertEqual(body['thinking'],{'type':'disabled'})
        self.assertNotIn('reasoning_effort',body)
        self.assertEqual(event['requested_planning_effort'],'none')
        self.assertEqual(event['effective_reasoning_effort'],'none')
        self.assertEqual(body['max_tokens'],16384);self.assertEqual(timeout,180)

    def test_strict_plan_uses_its_effective_not_global_thinking_mode(self):
        origin=getattr(engine_module,'__frozen_origin__',None)
        resources=Path(origin.split('!/PYZ.pyz/')[0]).parents[2] if origin else None
        agent=ResearchAgent(ResearchArchive(self.folder/'archive'),resources)
        self.addCleanup(agent.close_runtime)
        task=agent.create('合成规划配置测试，不开展真实研究')
        self.settings.save(self.config|dict(endpoint='https://api.deepseek.com/beta/chat/completions',
                                            reasoning_effort='none',planning_effort='low'))
        service=ModelService(self.settings,offline=False);captured=[]
        def stop(current,request,*args):
            captured.append(json.loads(request.data));raise RuntimeError('synthetic boundary stop')
        with patch.object(service,'_request_bytes',side_effect=stop),self.assertRaises(ContractError):
            service.reason_structured_plan(task,[])
        self.assertEqual(captured[0]['reasoning_effort'],'low')
        self.assertEqual(captured[0]['tool_choice'],'auto')
        self.assertTrue(captured[0]['tools'][0]['function']['strict'])


if __name__=='__main__':
    unittest.main()
