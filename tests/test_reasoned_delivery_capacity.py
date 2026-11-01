"""Thought and answer share output capacity; task budgets remain unchanged."""
import copy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.argument_support import VERSION
from researchhelper.contracts import ContractError, budget
from researchhelper.model import ModelService, ModelSettings

ROOT=Path(__file__).resolve().parents[1]


class ReasonedDeliveryCapacityTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory(prefix='reasoned-capacity-',dir=ROOT/'result/acceptance')
        self.addCleanup(folder.cleanup)
        self.settings=ModelSettings(folder.name)
        self.settings.save(dict(endpoint='https://api.deepseek.com/responses',model='deepseek-flash',
            api_key='FAKE_CAPACITY_KEY',reasoning_effort='low',planning_effort='low',extraction_mode='direct'))
        self.service=ModelService(self.settings,offline=False)
        self.task=dict(id='capacity_fixture',version=1,batch=1,argument_support_contract=VERSION,
            ledger=budget(dict(data_requests=100,model_calls=8,seconds=600)))
        self.response=dict(object='response',status='completed',error=None,incomplete_details=None,
            output=[dict(type='message',role='assistant',status='completed',
                content=[dict(type='output_text',text='{}')])],usage={})
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No network'))
        guard.start();self.addCleanup(guard.stop)

    def request(self,phase,**kwargs):
        with patch.object(self.service,'_request_bytes',return_value=json.dumps(self.response).encode()) as call:
            self.service.reason(self.task,[],phase=phase,**kwargs)
        return json.loads(call.call_args.args[1].data), self.task['ledger']['dispatches'][-1]

    def test_analysis_keeps_low_and_has_capacity_for_thought_plus_visible_text(self):
        body,record=self.request('analyze')
        self.assertEqual(body['reasoning'],{'effort':'low'})
        self.assertEqual(body['max_output_tokens'],65536)
        self.assertEqual(body['model'],'deepseek-flash')
        self.assertEqual(record['timeout_seconds'],180)
        self.assertEqual(record['output_limit_policy'],'reasoning_and_visible_output_v1')

    def test_review_receives_the_same_reasoning_capacity_without_a_new_model(self):
        body,record=self.request('review_support')
        self.assertEqual(body['max_output_tokens'],65536)
        self.assertEqual(body['reasoning'],{'effort':'low'})
        self.assertEqual(record['requested_model'],'deepseek-flash')

    def test_planning_and_direct_extraction_do_not_gain_extra_output_or_thinking(self):
        body,_=self.request('extract')
        self.assertEqual(body['max_output_tokens'],16384)
        self.assertEqual(body['reasoning'],{'effort':'none'})
        body,_=self.request('plan')
        self.assertEqual(body['max_output_tokens'],16384)
        self.assertEqual(body['reasoning'],{'effort':'low'})

    def test_explicit_no_thinking_is_not_silently_upgraded(self):
        config=self.settings.read(private=True);config['reasoning_effort']='none';self.settings.save(config)
        body,record=self.request('analyze')
        self.assertEqual(body['reasoning'],{'effort':'none'})
        self.assertEqual(body['max_output_tokens'],16384)
        self.assertEqual(record['timeout_seconds'],90)

    def test_legacy_host_proxy_and_unknown_models_keep_their_declared_old_limits(self):
        self.task.pop('argument_support_contract')
        body,_=self.request('analyze')
        self.assertEqual(body['max_output_tokens'],16384)
        self.assertEqual(ModelService.output_limit(dict(endpoint='https://proxy.invalid/responses',
            model='deepseek-flash',reasoning_effort='low'),'analyze'),8192)

    def test_long_wait_still_preserves_task_time_and_call_limits(self):
        limits=copy.deepcopy(self.task['ledger']['limits'])
        self.task['ledger']['used']['seconds']=500
        _,record=self.request('analyze')
        self.assertEqual(record['timeout_seconds'],40)
        self.assertEqual(self.task['ledger']['limits'],limits)
        self.assertEqual(self.task['ledger']['used']['model_calls'],1)

    def test_final_review_respects_remaining_time_and_cannot_make_a_ninth_call(self):
        self.task['ledger']['used'].update(model_calls=7,seconds=590)
        _,record=self.request('review_support',delivery=True,delivery_margin=5)
        self.assertEqual(record['timeout_seconds'],5)
        self.assertEqual(self.task['ledger']['used']['model_calls'],8)
        with self.assertRaises(ContractError):self.request('review_support',delivery=True,delivery_margin=5)

    def test_more_capacity_does_not_allow_an_incomplete_answer_to_be_adopted(self):
        self.response.update(status='incomplete',incomplete_details={'reason':'max_output_tokens'})
        with self.assertRaises(ContractError):self.request('analyze')
        self.assertEqual(self.task['ledger']['dispatches'][-1]['failure_code'],'content_truncated')
        self.assertEqual(self.task['ledger']['used']['model_calls'],1)


if __name__=='__main__':unittest.main()
