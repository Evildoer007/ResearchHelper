"""Offline proposal completion; no archived study or source is re-approved."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from tests import ROOT, read_retained_export
from tests.test_operating_original_protocol import payload
from researchhelper.contracts import ContractError,charge,fingerprint
from researchhelper.data_requirements import unproposed_numeric
from researchhelper.engine import ResearchAgent
from researchhelper.output_schema import original_span_output_schema,_original_span_choices,decode_original_spans
from researchhelper.planning import input_fingerprint


class RequiredNumericCompletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path=ROOT/'result/acceptance/one-sentence-research-xe3jpgn9/task.json'
        original = read_retained_export(cls.path)
        cls.before=hashlib.sha256(original).hexdigest()
        cls.closed=json.loads(original)

    def setUp(self):
        # An offline post-extraction fixture, not a resumed real task. Its
        # simulated ledger tests reservation arithmetic; no network is called.
        self.task=copy.deepcopy(self.closed)
        self.task.update(status='运行中',delivery=None,pending_content=None,required_numeric_completion_version=1,
                         typed_numeric_selection_version=2)
        self.task['ledger']['used'].update(model_calls=3,seconds=50)
        self.task['input_hash']=input_fingerprint(self.task)
        self.views=self.task['reasoning_state']['reading']['bundle']['views']
        self.first=copy.deepcopy(self.task['host_result']['evidence_specs'])
        self.digest=self.task['reasoning_state']['extraction']['input_digest']
        self.request=payload(self.task,self.views)
        self.agent=ResearchAgent.__new__(ResearchAgent)
        self.agent.model=SimpleNamespace(supports_structured_analysis=lambda:True,reason_structured_original_spans=lambda:None)
        self.agent._active=Mock(return_value=self.task);self.agent._active_supplement=Mock(return_value=None)
        self.agent._save_model_checkpoint=Mock();self.agent._gap=Mock()

    def submit(self,model,task,phase,request,fields):
        self.assertEqual(phase,'extract');self.assertEqual(fields,{'evidence_specs'})
        self.assertEqual(len(request['input_completion']['missing_numeric_needs']),4)
        self.agent._save_model_checkpoint.assert_called()
        charge(task['ledger'],'model_calls')
        schema=original_span_output_schema(task,self.views)
        branches=schema['properties']['selections']['items']['anyOf']
        self.assertTrue(all(b['properties']['kind']['enum']==['number'] for b in branches))
        p=branches[0]['properties'];ref=p['source_ref']['enum'][0]
        choice=next(c for c in _original_span_choices(task,self.views) if c['source_ref']==ref)
        number=next(n for n in choice['number_tokens'] if n['token']=='0.15')
        unit=next(u for u in choice['unit_references'] if u['unit']=='%')
        period=next(d for d in choice['period_anchors'] if d['period']=='2026-01-01/2026-06-30')
        link=p['review']['properties']['need_links']['items']['anyOf'][0]['properties']
        wire=dict(version=12,binding_ref=schema['properties']['binding_ref']['enum'][0],selections=[dict(
            id=p['id']['enum'][0],kind='number',source_ref=ref,object=p['object']['enum'][0],nature='合同政策条款',
            number_reference=dict(number_ref=number['ref'],unit_ref=unit['ref']),
            temporal_reference=dict(period_ref=period['ref']),review=dict(scope='current_original_descriptive_only',
                need_links=[dict(question_id=link['question_id']['enum'][0],need_ids=[link['need_ids']['items']['enum'][0]])]))])
        if 'numeric_requirements' in schema['properties']:
            item=wire['selections'].pop();item['review'].pop('need_links')
            from researchhelper.data_requirements import numeric_slots
            slots=numeric_slots(task);wire['numeric_requirements']={s['key']:dict(status='not_found',reason='离线示例只演示一项原值') for s in slots}
            wire['numeric_requirements'][slots[0]['key']]=dict(status='found',items=[item])
        result=decode_original_spans(wire,schema,(task,self.views))
        return result,dict(phase='extract',output_hash=fingerprint(result))

    def complete(self):
        return self.agent._complete_initial_numeric_inputs(self.task,self.request,self.digest,None)

    def test_known_text_only_completion_gets_one_bound_numeric_call_without_new_data(self):
        data_used=self.task['ledger']['used']['data_requests'];limits=copy.deepcopy(self.task['ledger']['limits'])
        self.assertEqual(len(unproposed_numeric(self.task,self.first)),4)
        with patch('researchhelper.engine.model_object',side_effect=self.submit) as model:result=self.complete()
        self.assertEqual(model.call_count,1)
        self.assertEqual(result['evidence_specs'][:len(self.first)],self.first)
        self.assertEqual(result['evidence_specs'][-1]['value'],0.15)
        self.assertNotIn('source_check',result['evidence_specs'][-1])
        self.assertEqual(self.task['reasoning_state']['required_numeric_completion']['status'],'已提交')
        self.assertEqual(self.task['ledger']['used']['data_requests'],data_used)
        self.assertEqual(self.task['ledger']['used']['model_calls'],4)
        self.assertEqual(self.task['ledger']['limits'],limits)
        with patch('researchhelper.engine.model_object') as again:self.complete()
        again.assert_not_called()

    def test_unknown_or_failed_call_is_not_retried(self):
        def fail(*args):
            charge(self.task['ledger'],'model_calls');raise ContractError('Synthetic offline failure')
        with patch('researchhelper.engine.model_object',side_effect=fail) as first:self.complete()
        self.assertEqual(first.call_count,1)
        self.assertEqual(self.task['reasoning_state']['required_numeric_completion']['status'],'未采用')
        with patch('researchhelper.engine.model_object') as again:self.complete()
        again.assert_not_called();self.assertEqual(self.task['host_result']['evidence_specs'],self.first)

    def test_delivery_reserve_closed_task_and_failed_batch_stop_before_dispatch(self):
        for change in ('budget','closed','batch'):
            task=copy.deepcopy(self.task)
            if change=='budget':task['ledger']['used']['model_calls']=5
            if change=='closed':task['status']='部分完成'
            if change=='batch':task['reasoning_state']['source_extraction_batches']={'items':{'x':{'status':'未采用'}}}
            self.assertFalse(ResearchAgent._required_numeric_completion_available(task,task['host_result']))

    @classmethod
    def tearDownClass(cls):
        assert hashlib.sha256(read_retained_export(cls.path)).hexdigest()==cls.before


if __name__=='__main__':unittest.main()
