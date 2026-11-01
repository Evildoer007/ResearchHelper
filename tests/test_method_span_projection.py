"""Whole two-original source inputs fit without dropping research data."""
import copy
import json
import re
import socket
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from tests.probe_etf_extraction_context import RETAINED,payload_for
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import canonical,ContractError,fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.financial_sources import original_line_index
from researchhelper.model import ModelService,ModelSettings
from researchhelper.output_schema import (original_span_prompt,original_span_output_schema,decode_original_spans,
    _original_span_choices,_project_method_span_prompt,StructuredOutputError)
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import MAX_CONTEXT_CHARACTERS,model_object

MARK=re.compile(r'(?<!⟦)⟦(n[0-9]+_[0-9]+)⟧')


def restore(text):
    return MARK.sub('',text).replace('⟦⟦','⟦')


class MethodSpanProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw=RETAINED.read_bytes();cls.old=json.loads(cls.raw)
        cls.task=copy.deepcopy(cls.old)
        cls.task['original_prompt_projection_version']=1
        cls.task['input_hash']=input_fingerprint(cls.task)
        cls.payload=payload_for(cls.task);cls.views=cls.payload['untrusted_sources']
        cls.prompt=original_span_prompt(cls.payload,cls.task)
        cls.schema=original_span_output_schema(cls.task,cls.views)
        cls.choices=_original_span_choices(cls.task,cls.views)

    @classmethod
    def tearDownClass(cls):
        if RETAINED.read_bytes()!=cls.raw:raise AssertionError('Closed task changed')

    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('Offline projection check'))
        guard.start();self.addCleanup(guard.stop)

    def wire(self):
        choice=self.choices[0]
        token=next(t for t in choice['number_tokens'] if t['token']=='0.15'
                   and choice['lines'][t['line']]['part']=='第30页')
        variant=next(v for v in self.schema['properties']['selections']['items']['anyOf']
            if v['properties']['source_ref']['enum']==[choice['source_ref']]
            and v['properties']['kind']['enum']==['number'])
        p=variant['properties'];link=p['review']['properties']['need_links']['items']['anyOf'][0]['properties']
        period=next(row[0] for row in self.prompt['untrusted_sources'][0]['reporting_period_choices']
            if '2026' in row[2] and '2025' not in row[2])
        return dict(version=12,binding_ref=self.prompt['span_binding_ref'],selections=[dict(
            id='ev_management',source_ref=choice['source_ref'],kind='number',object=p['object']['enum'][0],
            nature='合同政策条款',question_ids=[link['question_id']['enum'][0]],
            review=dict(scope='current_original_descriptive_only',need_links=[dict(
                question_id=link['question_id']['enum'][0],need_ids=[link['need_ids']['items']['enum'][0]])]),
            text_span=dict(start=token['line'],end=token['line']),number_ref=token['ref'],unit='%',
            unit_span=dict(start=token['line'],end=token['line']),contexts=[],temporal_reference=dict(period_ref=period))])

    def test_complete_actual_two_original_payload_fits_unchanged_limit(self):
        before=original_span_prompt(payload_for(self.old),self.old)
        old_schema=original_span_output_schema(self.old,self.views)
        self.assertGreater(len(canonical(before))+len(canonical(old_schema)),MAX_CONTEXT_CHARACTERS)
        size=len(canonical(self.prompt))+len(canonical(self.schema))
        self.assertLessEqual(size,MAX_CONTEXT_CHARACTERS,size)
        self.assertEqual(self.prompt['questions'],self.payload['questions'])
        self.assertEqual(self.prompt['calculations'],self.payload['calculations'])
        self.assertEqual(self.task['questions'],self.old['questions'])
        self.assertEqual(self.task['calculation_specs'],self.old['calculation_specs'])

    def test_every_character_numeric_address_and_metadata_cell_is_preserved(self):
        for choice,view in zip(self.choices,self.prompt['untrusted_sources']):
            expected={line['index']:line['text'] for line in choice['lines']}
            actual={line:restore(text) for part in view['parts'] for line,text in part['indexed_lines']}
            self.assertEqual(actual,expected)
            addresses={ref:(line,token) for ref,line,token in (
                (t['ref'],t['line'],t['token']) for t in choice['number_tokens'])}
            displayed={}
            for part,old in zip(view['parts'],choice['view']['parts']):
                for key,value in old.items():
                    if key!='text':self.assertEqual(part[key],value)
                for line,text in part['indexed_lines']:
                    for match in MARK.finditer(text):
                        ref=match[1];source_line,raw=addresses[ref]
                        self.assertEqual(source_line,line)
                        self.assertTrue(text[match.end():].startswith(raw))
                        displayed[ref]=(line,raw)
            self.assertEqual(displayed,addresses)
            self.assertEqual(view['directory_archive_ref']['content_hash'],fingerprint(choice['view']['directory']))

    def test_dates_and_publication_metadata_are_retained_not_promoted(self):
        for old,new,view in zip(self.payload['evidence_contract']['sources'],
                              self.prompt['evidence_contract']['sources'],self.prompt['untrusted_sources']):
            for field in ('artifact_id','snapshot_hash','publication_candidates','current_availability_reference','object_binding'):
                self.assertEqual(old[field],new[field])
            rows=[dict(zip(view['observation_anchor_columns'],row)) for row in view['observation_anchors']]
            self.assertEqual(old['original_date_anchors'],[{k:v for k,v in row.items() if k!='index'} for row in rows])
        for field in ('research_scope','calculation_unit_rule','calculation_input_requirements','question_ids'):
            self.assertEqual(self.prompt['evidence_contract'][field],self.payload['evidence_contract'][field])
        self.assertEqual([[x['text'] for x in needs] for needs in self.prompt['evidence_contract']['question_requirements'].values()],
                         [[x['text'] for x in needs] for needs in self.payload['evidence_contract']['question_requirements'].values()])

    def test_decoding_keeps_raw_value_and_unit_without_creating_context_or_approval(self):
        spec=decode_original_spans(self.wire(),self.schema,(self.task,self.views))['evidence_specs'][0]
        self.assertEqual((spec['raw_token'],spec['value'],spec['unit']),('0.15',0.15,'%'))
        self.assertNotIn('calculation_context',spec)
        self.assertNotIn('source_check',spec)
        self.assertNotIn('⟦',spec['text'])

    def test_unknown_finite_reference_and_cross_source_never_resolve(self):
        for ref in ('n1_9999','n2_1'):
            wire=self.wire();wire['selections'][0]['number_ref']=ref
            with self.subTest(ref=ref),self.assertRaises(StructuredOutputError):
                decode_original_spans(wire,self.schema,(self.task,self.views))

    def test_wrong_unit_remains_rejected(self):
        wire=self.wire();wire['selections'][0]['unit']='亿元'
        with self.assertRaises(ContractError):decode_original_spans(wire,self.schema,(self.task,self.views))

    def test_literal_marker_in_original_text_is_escaped_and_exactly_recoverable(self):
        task=dict(original_prompt_projection_version=1,questions=[],calculation_specs=[])
        text='原文⟦n1_9⟧，金额10.5元。\n'
        source=dict(artifact_id='original',snapshot_hash='hash')
        view=dict(source_ref='source_1',parts=[dict(locator='段1',indexed_lines=[[0,text]],
                                                 number_tokens=[['n1_1',0,'10.5']])])
        prompt=dict(untrusted_sources=[view],evidence_contract=dict(sources=[source],question_requirements={}))
        choice=dict(source_ref='source_1',kind='original',view=dict(id='original',snapshot_hash='hash'))
        result=_project_method_span_prompt(prompt,[choice],task)
        marked=result['untrusted_sources'][0]['parts'][0]['indexed_lines'][0][1]
        self.assertEqual(restore(marked),text)
        self.assertEqual(MARK.findall(marked),['n1_1'])

    def test_actual_model_adapter_accepts_complete_projected_payload_once(self):
        reply=dict(object='response',status='completed',error=None,incomplete_details=None,output=[dict(
            type='message',role='assistant',status='completed',content=[dict(type='output_text',text=json.dumps(self.wire()))])],usage={})
        task=copy.deepcopy(self.task)
        with tempfile.TemporaryDirectory(prefix='span-projection-',dir=ROOT/'result/acceptance') as folder:
            settings=ModelSettings(folder);settings.save(dict(endpoint='https://api.deepseek.com/responses',
                model='deepseek-flash',api_key='SYNTHETIC_PROJECTION_KEY',extraction_mode='direct'))
            with patch.object(ModelService,'_request_bytes',return_value=json.dumps(reply).encode()) as transport:
                result,receipt=model_object(ModelService(settings,offline=False),task,'extract',self.payload,{'evidence_specs'})
        self.assertEqual(result,decode_original_spans(self.wire(),self.schema,(self.task,self.views)))
        self.assertEqual(transport.call_count,1)
        self.assertEqual(receipt['original_period_selection_version'],1)

    def test_legacy_payload_is_not_relabelled_or_old_study_reopened(self):
        old=original_span_prompt(payload_for(self.old),self.old)
        self.assertNotIn('original_prompt_encoding',old)
        self.assertEqual(self.old['status'],'已取消')
        self.assertEqual(RETAINED.read_bytes(),self.raw)

    def test_projection_policy_is_input_bound_and_not_a_synthetic_case_upgrade(self):
        with tempfile.TemporaryDirectory() as folder:
            agent=ResearchAgent(ResearchArchive(folder),ROOT,offline=True,require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            task=agent.create('比较基金费用');old=copy.deepcopy(task);old.pop('original_prompt_projection_version')
            self.assertNotEqual(input_fingerprint(task),input_fingerprint(old))
            self.assertNotIn('original_prompt_projection_version',agent.create_case('mechanism'))
