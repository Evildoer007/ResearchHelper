"""Bounded DeepSeek SSE decoding; no tool execution or retained thought text.

This is a wire codec, not a second research path. The complete normalized
message still crosses the ordinary model, phase and evidence validators.
"""
import json

from .contracts import DuplicateJSONField, unique_json_object
from .transport import TransportFailure

PROTOCOL='deepseek_sse_v1'
MAX_WIRE_BYTES=16*1024*1024
MAX_EVENT_BYTES=256*1024
MAX_MESSAGE_BYTES=4*1024*1024
MAX_EVENTS=65536
DIAGNOSTIC_FIELDS=frozenset({'stream_events','stream_reasoning_characters',
    'stream_content_characters','stream_tool_characters','stream_finish_seen'})
DIAGNOSTIC_LIMITS={key:MAX_WIRE_BYTES for key in DIAGNOSTIC_FIELDS}
DIAGNOSTIC_LIMITS.update(stream_events=MAX_EVENTS,stream_finish_seen=1)


def _nonfinite_number(value):
    raise ValueError('Non-finite JSON number')


class DeepSeekStream:
    def __init__(self,record,on_progress=None):
        self.record,self.on_progress=record,on_progress
        self.buffer=b'';self.lines=[];self.event_size=0
        self.content=[];self.tools={};self.message_bytes=0
        self.metadata={};self.finish_reason=None;self.done=False;self.usage=None
        self.events=0;self.first_line=True;self.last_progress=None
        record.update({field:0 for field in DIAGNOSTIC_FIELDS})

    def reject(self,code='invalid_stream_response'):
        self.record['failure_code']=code
        raise TransportFailure(code, '模型流式响应未完整通过核验，不采用片段或自动重试')

    def progress(self):
        metrics={key:self.record[key] for key in DIAGNOSTIC_FIELDS}
        state=(metrics['stream_reasoning_characters']>0,metrics['stream_content_characters']>0,
            metrics['stream_tool_characters']>0,metrics['stream_finish_seen'])
        count=metrics['stream_events']
        if self.on_progress and (state!=self.last_progress or count and count&(count-1)==0):
            self.last_progress=state
            self.on_progress(metrics)

    def feed(self,chunk):
        self.buffer+=chunk
        while b'\n' in self.buffer:
            line,self.buffer=self.buffer.split(b'\n',1)
            line=line.removesuffix(b'\r')
            if self.first_line:
                line=line.removeprefix(b'\xef\xbb\xbf');self.first_line=False
            if not line:
                if self.lines:
                    self.event(b'\n'.join(self.lines))
                self.lines=[];self.event_size=0
            elif line.startswith(b'data:'):
                data=line[5:].removeprefix(b' ')
                self.lines.append(data);self.event_size+=len(data)+1
            elif line==b'data':
                self.lines.append(b'');self.event_size+=1
            elif line.startswith(b'event:') and line[6:].strip() not in (b'',b'message'):
                self.reject('unsupported_stream_event')
            elif not line.startswith((b':',b'id:',b'retry:')):
                self.reject('invalid_stream_frame')
            if self.event_size>MAX_EVENT_BYTES:
                self.reject('stream_event_too_large')
        if len(self.buffer)+self.event_size>MAX_EVENT_BYTES:
            self.reject('stream_event_too_large')

    def _text(self,delta,key,counter,target=None):
        value=delta.get(key)
        if value is None:return
        if not isinstance(value,str):self.reject()
        self.record[counter]+=len(value)
        if target is not None:
            self.message_bytes+=len(value.encode('utf-8'))
            if self.message_bytes>MAX_MESSAGE_BYTES:self.reject('response_too_large')
            target.append(value)

    def event(self,data):
        if self.done:self.reject('stream_data_after_done')
        if data==b'[DONE]':
            if self.finish_reason is None:self.reject('stream_finish_missing')
            self.done=True;self.progress();return
        self.events+=1
        if self.events>MAX_EVENTS:self.reject('stream_too_many_events')
        self.record['stream_events']=self.events
        try:
            payload=json.loads(data.decode('utf-8'),parse_constant=_nonfinite_number,
                               object_pairs_hook=unique_json_object)
        except DuplicateJSONField:
            self.reject('duplicate_response_field')
        except (ValueError, RecursionError):
            self.reject('invalid_stream_encoding')
        if not isinstance(payload,dict) or 'error' in payload:self.reject('stream_provider_error')
        for key in ('id','model'):
            value=payload.get(key)
            if value is not None:
                if not isinstance(value,str) or not value:self.reject()
                if key in self.metadata and self.metadata[key]!=value:self.reject('stream_identity_changed')
                self.metadata[key]=value
        choices=payload.get('choices')
        if not isinstance(choices,list) or len(choices)>1:self.reject()
        if not choices:
            if self.finish_reason is None or not isinstance(payload.get('usage'),dict):self.reject()
        else:
            choice=choices[0]
            if (not isinstance(choice,dict) or type(choice.get('index')) is not int
                    or choice['index']!=0 or not isinstance(choice.get('delta'),dict)):
                self.reject()
            delta=choice['delta']
            if self.finish_reason is not None:self.reject('stream_data_after_finish')
            if delta.get('role') not in (None,'assistant') or delta.get('function_call') is not None:
                self.reject()
            self._text(delta,'reasoning_content','stream_reasoning_characters')
            self._text(delta,'content','stream_content_characters',self.content)
            calls=delta.get('tool_calls')
            if calls is not None:
                if not isinstance(calls,list) or len(calls)>8:self.reject()
                for entry in calls:
                    if (not isinstance(entry,dict) or type(entry.get('index')) is not int
                            or not 0<=entry['index']<8):self.reject()
                    call=self.tools.setdefault(entry['index'],{'id':[],'name':[],'arguments':[]})
                    if entry.get('type') not in (None,'function'):self.reject()
                    function=entry.get('function',{})
                    if not isinstance(function,dict):self.reject()
                    for container,key in ((entry,'id'),(function,'name'),(function,'arguments')):
                        self._text(container,key,'stream_tool_characters',call[key])
            finish=choice.get('finish_reason')
            if finish is not None:
                if not isinstance(finish,str) or not finish:self.reject()
                self.finish_reason=finish;self.record['stream_finish_seen']=1
        usage=payload.get('usage')
        if usage is not None:
            if not isinstance(usage,dict) or self.finish_reason is None or self.usage is not None:self.reject()
            self.usage=usage
        self.progress()

    def complete(self):
        if self.buffer or self.lines or not self.done:self.reject('stream_incomplete')
        if sorted(self.tools)!=list(range(len(self.tools))):self.reject()
        message={'role':'assistant','content':''.join(self.content)}
        if self.tools:
            calls=[]
            for index in sorted(self.tools):
                value=self.tools[index]
                ident,name,arguments=(''.join(value[key]) for key in ('id','name','arguments'))
                if not ident or not name:self.reject()
                calls.append(dict(id=ident,type='function',function=dict(name=name,arguments=arguments)))
            message['tool_calls']=calls
        result={**self.metadata,'choices':[dict(index=0,message=message,finish_reason=self.finish_reason)]}
        if self.usage is not None:result['usage']=self.usage
        body=json.dumps(result,ensure_ascii=False).encode('utf-8')
        if len(body)>MAX_MESSAGE_BYTES:self.reject('response_too_large')
        self.record.update(response_stream_complete=True,stream_progress_is_lower_bound=False)
        return body
