"""Streaming fixtures only: no model, provider, saved App configuration or SDK."""
import io
import importlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, budget
from researchhelper.model import ModelService, ModelSettings
from researchhelper.transport import TransportFailure
from researchhelper.model_stream import DIAGNOSTIC_LIMITS
stage_fixture=importlib.import_module('tests.test_model_progress_checkpoint'
    if __package__ else 'test_model_progress_checkpoint')


def frame(delta=None, finish=None, *, usage=None, ident='fixture-response', model='deepseek-flash'):
    choice=[] if delta is None else [dict(index=0, delta=delta, finish_reason=finish)]
    return b'data: '+json.dumps(dict(id=ident, model=model, choices=choice, usage=usage),
        ensure_ascii=False).encode()+b'\n\n'


def wire(*frames, done=True):
    return b''.join(frames)+(b'data: [DONE]\n\n' if done else b'')


class Response(io.BytesIO):
    headers={'Content-Type': 'text/event-stream; charset=utf-8'}


class StreamTests(unittest.TestCase):
    def parse(self, data, *, width=None):
        record=dict(response_protocol='deepseek_sse_v1')
        response=Response(data)
        if width:
            read=response.read1
            response.read1=lambda size: read(min(size,width))
        result=ModelService(None)._read_provider_response(response,time.monotonic(),2,record)
        return json.loads(result),record

    def rejected(self, data):
        with self.assertRaises(ContractError):self.parse(data)

    def test_utf8_and_json_escapes_survive_single_byte_chunks(self):
        text=json.dumps({'answer':'原文"甲"\n符号\\乙'},ensure_ascii=False)
        payload,record=self.parse(wire(frame({'role':'assistant','content':text[:5]}),
            frame({'content':text[5:]},'stop')),width=1)
        self.assertEqual(payload['choices'][0]['message']['content'],text)
        self.assertTrue(record['response_stream_complete'])

    def test_reasoning_is_counted_but_never_retained(self):
        payload,record=self.parse(wire(frame({'reasoning_content':'PRIVATE_THOUGHT'}),
            frame({'content':'{"ok":true}'},'stop')))
        self.assertEqual(record['stream_reasoning_characters'],len('PRIVATE_THOUGHT'))
        self.assertNotIn('PRIVATE_THOUGHT',json.dumps([payload,record]))
        self.assertNotIn('reasoning_content',payload['choices'][0]['message'])

    def test_actual_usage_after_finish_is_retained(self):
        usage=dict(prompt_tokens=3,completion_tokens=4,total_tokens=7,
            completion_tokens_details={'reasoning_tokens':2})
        payload,_=self.parse(wire(frame({'content':'{}'},'stop'),frame(usage=usage)))
        self.assertEqual(payload['usage'],usage)

    def test_no_usage_does_not_invent_zero_cost(self):
        payload,record=self.parse(wire(frame({'content':'{}'},'stop')))
        self.assertNotIn('usage',payload)
        self.assertNotIn('usage',record)

    def test_comments_and_crlf_are_supported(self):
        data=b': keep-alive\r\n\r\n'+wire(frame({'content':'{}'},'stop')).replace(b'\n',b'\r\n')
        payload,_=self.parse(data,width=3)
        self.assertEqual(payload['choices'][0]['message']['content'],'{}')

    def test_missing_done_is_rejected_even_with_complete_json(self):
        self.rejected(wire(frame({'content':'{}'},'stop'),done=False))

    def test_missing_finish_is_not_accepted_by_done(self):
        self.rejected(wire(frame({'content':'{}'})))

    def test_data_after_done_is_rejected(self):
        self.rejected(wire(frame({'content':'{}'},'stop'))+frame({'content':'extra'}))

    def test_second_finish_is_rejected(self):
        self.rejected(wire(frame({'content':'{}'},'stop'),frame({},'stop')))

    def test_content_after_finish_is_rejected(self):
        self.rejected(wire(frame({'content':'{}'},'stop'),frame({'content':'extra'})))

    def test_nonzero_or_boolean_choice_index_is_rejected(self):
        for index in (1,True):
            item=json.loads(frame({'content':'{}'},'stop')[6:])
            item['choices'][0]['index']=index
            self.rejected(wire(b'data: '+json.dumps(item).encode()+b'\n\n'))

    def test_changed_response_identity_is_rejected(self):
        self.rejected(wire(frame({'content':'{'}),frame({'content':'}'},'stop',ident='other')))

    def test_changed_model_is_rejected(self):
        self.rejected(wire(frame({'content':'{'}),frame({'content':'}'},'stop',model='other')))

    def test_nonassistant_role_is_rejected(self):
        self.rejected(wire(frame({'role':'user','content':'{}'},'stop')))

    def test_provider_error_is_not_archived_as_text(self):
        self.rejected(wire(b'data: {"error":{"message":"PRIVATE_PROVIDER_ERROR"}}\n\n'))

    def test_malformed_json_or_encoding_is_rejected(self):
        for chunk in (b'data: not-json\n\n',b'data: \xff\n\n'):
            with self.subTest(chunk=repr(chunk)):self.rejected(wire(chunk))

    def test_nonstring_delta_is_rejected(self):
        self.rejected(wire(frame({'content':{'answer':'not a delta'}},'stop')))

    def test_fragmented_tool_arguments_are_not_executed(self):
        one={'index':0,'id':'call1','type':'function','function':{'name':'rh_submit_plan_v1','arguments':'{"questions":'}}
        two={'index':0,'function':{'arguments':'[]}'}}
        payload,_=self.parse(wire(frame({'tool_calls':[one]}),frame({'tool_calls':[two]},'tool_calls')))
        call=payload['choices'][0]['message']['tool_calls'][0]
        self.assertEqual(call['function']['arguments'],'{"questions":[]}')
        self.assertEqual(call['function']['name'],'rh_submit_plan_v1')

    def test_length_finish_remains_length_not_success(self):
        payload,_=self.parse(wire(frame({'reasoning_content':'only thinking'},'length')))
        self.assertEqual(payload['choices'][0]['finish_reason'],'length')

    def test_large_event_fails_before_unbounded_buffering(self):
        self.rejected(b'data: '+b'x'*(256*1024+1))

    def test_unknown_response_protocol_is_rejected(self):
        with self.assertRaises(ContractError):
            ModelService(None)._read_provider_response(Response(b'{}'),time.monotonic(),1,
                {'response_protocol':'unknown'})

    def test_wrong_content_type_is_not_silently_treated_as_sse(self):
        response=Response(wire(frame({'content':'{}'},'stop')))
        response.headers={'Content-Type':'application/json'}
        with self.assertRaises(ContractError):
            ModelService(None)._read_provider_response(response,time.monotonic(),1,
                {'response_protocol':'deepseek_sse_v1'})


class StreamRequestTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory(prefix='stream-fixture-',dir=Path(__file__).resolve().parents[1]/'result/acceptance')
        self.addCleanup(folder.cleanup)
        self.settings=ModelSettings(folder.name)
        self.settings.save(dict(endpoint='https://api.deepseek.com',model='deepseek-flash',api_key='SYNTHETIC_KEY'))
        self.service=ModelService(self.settings,offline=False)
        self.task=dict(id='stream-fixture',version=1,batch=1,ledger=budget())
        self.captured=[]
        def request(task,request,started,timeout,record):
            self.captured.append((json.loads(request.data),timeout))
            with Response(wire(frame({'content':'{"ok":true}'},'stop'))) as response:
                return self.service._read_provider_response(response,started,timeout,record)
        self.worker=patch.object(self.service,'_request_bytes',side_effect=request)
        self.worker.start();self.addCleanup(self.worker.stop)

    def test_stream_preserves_effort_limits_and_single_request(self):
        before=self.settings.read()
        result=self.service.reason_research_plan(self.task,[{'role':'user','content':'synthetic'}])
        body,timeout=self.captured[0]
        self.assertEqual(result,'{"ok":true}')
        self.assertTrue(body['stream']);self.assertEqual(body['stream_options'],{'include_usage':True})
        self.assertNotIn('thinking',body);self.assertNotIn('reasoning_effort',body)
        self.assertEqual(timeout,180);self.assertEqual(body['max_tokens'],16384)
        self.assertEqual(self.task['ledger']['used']['model_calls'],1)
        self.assertEqual(self.settings.read(),before)

    def test_truncated_stream_is_never_returned_as_a_complete_answer(self):
        def truncated(task,request,started,timeout,record):
            return self.service._read_provider_response(Response(wire(frame({'content':'{"ok":true}'},'length'))),started,timeout,record)
        with patch.object(self.service,'_request_bytes',side_effect=truncated),self.assertRaises(ContractError):
            self.service.reason_research_plan(self.task,[])
        self.assertEqual(self.task['ledger']['dispatches'][-1]['failure_code'],'content_truncated')
        self.assertEqual(self.task['ledger']['used']['model_calls'],1)

    def test_offline_does_not_dispatch_stream(self):
        self.service.offline=True
        with self.assertRaises(ContractError):self.service.reason_research_plan(self.task,[])
        self.assertEqual(self.captured,[])


class StreamDiagnosticTests(unittest.TestCase):
    exchange=stage_fixture.TransportStagePolicyTests.exchange

    def test_fixed_numeric_diagnostics_are_retained(self):
        _,record=self.exchange([dict(kind='stage',stage='读取响应',diagnostics={
            'stream_events':1,'stream_reasoning_characters':8}),dict(kind='response',body=b'{}')],
            allowed_stages=frozenset({'读取响应'}),allowed_stage_diagnostics=DIAGNOSTIC_LIMITS)
        self.assertEqual(record['stream_events'],1)
        self.assertEqual(record['stream_reasoning_characters'],8)

    def test_unknown_text_boolean_and_out_of_range_diagnostics_are_rejected(self):
        for metrics in ({'private':'PRIVATE_THOUGHT'},{'stream_events':'PRIVATE_THOUGHT'},
                {'stream_events':True},{'stream_events':-1},{'stream_finish_seen':2},
                {'stream_events':65537}):
            with self.subTest(metrics=metrics),self.assertRaises(TransportFailure):
                self.exchange([dict(kind='stage',stage='读取响应',diagnostics=metrics)],
                    allowed_stages=frozenset({'读取响应'}),allowed_stage_diagnostics=DIAGNOSTIC_LIMITS)
            self.assertNotIn('PRIVATE_THOUGHT',json.dumps(self.last_record))

    def test_diagnostics_cannot_move_backwards(self):
        with self.assertRaises(TransportFailure):
            self.exchange([dict(kind='stage',stage='读取响应',diagnostics={'stream_events':2}),
                dict(kind='stage',stage='读取响应',diagnostics={'stream_events':1})],
                allowed_stages=frozenset({'读取响应'}),allowed_stage_diagnostics=DIAGNOSTIC_LIMITS)

    def test_new_diagnostics_remain_forbidden_without_explicit_policy(self):
        with self.assertRaises(TransportFailure):
            self.exchange([dict(kind='stage',stage='读取响应',diagnostics={'stream_events':1})],
                allowed_stages=frozenset({'读取响应'}))


if __name__=='__main__':unittest.main()
