"""Readable source/span choices preserve canonical reading, without repairs."""
import copy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.model import ModelService,ModelSettings
from researchhelper.output_schema import (READING_REFERENCE_PROTOCOL,READING_REFERENCE_FUNCTION_NAME,
    reading_reference_description,reading_reference_output_schema,decode_reading_references,
    reading_output_schema,decode_reading_output,structured_json_completion)
from researchhelper.reasoning import model_object
from researchhelper.source_reader import catalog

ROOT=Path(__file__).resolve().parents[1]


class ReadingReferenceProtocolTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory(prefix='reading-references-',dir=ROOT/'result/acceptance')
        self.addCleanup(folder.cleanup);self.folder=Path(folder.name)
        self.agent=ResearchAgent(ResearchArchive(self.folder/'store'),ROOT,require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task=self.agent.create('核对两份测试材料中的业务陈述')
        for name,text in (('first.txt','发布日期：2026年10月15日。营业收入的变化需要核对。'),
                          ('second.txt','发布日期：2026年10月15日。现金流组成与限制另行说明。')):
            self.task=self.agent.add_material(self.task['id'],name,text.encode())
        self.description=catalog(self.task)
        self.choices=reading_reference_description(self.task,self.description)
        self.schema=reading_reference_output_schema(self.task,self.description)
        self.wire=dict(version=1,binding_ref=self.choices['binding_ref'],requests=[dict(
            source_ref=self.choices['sources'][0]['source_ref'],queries=['营业收入'],
            span_refs=[self.choices['sources'][0]['span_choices'][0]['span_ref']],
            reason='核对当前问题',question_ids=['q1'])])
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No networking'))
        guard.start();self.addCleanup(guard.stop)

    def decode(self,wire=None):
        return decode_reading_references(self.wire if wire is None else wire,self.schema,self.task,self.description)

    def service(self):
        settings=ModelSettings(self.folder/'settings')
        settings.save(dict(endpoint='https://api.deepseek.com/responses',model='deepseek-flash',
                           api_key='FAKE_READING_REF_KEY',reasoning_effort='low',extraction_mode='direct'))
        return ModelService(settings,offline=False)

    def response(self):
        return dict(object='response',status='completed',error=None,incomplete_details=None,
            output=[dict(type='message',role='assistant',status='completed',
                content=[dict(type='output_text',text=json.dumps(self.wire))])],
            usage=dict(input_tokens=10,output_tokens=10))

    def test_short_selection_expands_to_exact_original_binding_and_span(self):
        original=self.description['items'][0]
        selected=self.decode()['requests'][0]
        self.assertEqual(selected['artifact_id'],original['request_binding']['artifact_id'])
        self.assertEqual(selected['snapshot_hash'],original['request_binding']['snapshot_hash'])
        self.assertEqual(selected['parts'],[original['reading_spans'][0]])
        self.assertEqual(selected['queries'],['营业收入'])
        self.assertFalse(self.task['evidence'])

    def test_model_wire_does_not_require_hashes_or_character_offsets(self):
        item=self.wire['requests'][0]
        self.assertEqual(set(item),{'source_ref','queries','span_refs','reason','question_ids'})
        self.assertNotIn('snapshot_hash',json.dumps(self.schema))
        self.assertLessEqual(len(self.wire['binding_ref']),16)

    def test_source_or_plan_changes_invalidate_entire_catalog(self):
        task=copy.deepcopy(self.task);task['input_hash']='changed'
        with self.assertRaises(ContractError):
            decode_reading_references(self.wire,self.schema,task,self.description)

    def test_wrong_binding_is_rejected_without_replacement(self):
        wire=copy.deepcopy(self.wire);wire['binding_ref']='wrong-binding'
        with self.assertRaises(ContractError):self.decode(wire)

    def test_unknown_source_diagnostic_does_not_echo_submitted_value(self):
        wire=copy.deepcopy(self.wire);wire['requests'][0]['source_ref']='private-unregistered-value'
        with self.assertRaises(ContractError) as context:self.decode(wire)
        self.assertNotIn('private-unregistered-value',str(context.exception))
        self.assertNotIn('private-unregistered-value',json.dumps(context.exception.diagnostic))

    def test_span_from_other_source_is_not_rebound(self):
        wire=copy.deepcopy(self.wire)
        wire['requests'][0]['span_refs']=[self.choices['sources'][1]['span_choices'][0]['span_ref']]
        with self.assertRaises(ContractError):self.decode(wire)

    def test_unknown_span_is_not_guessed(self):
        wire=copy.deepcopy(self.wire);wire['requests'][0]['span_refs']=['span_1_999']
        with self.assertRaises(ContractError):self.decode(wire)

    def test_duplicate_span_is_not_silently_deduplicated(self):
        wire=copy.deepcopy(self.wire);wire['requests'][0]['span_refs']*=2
        with self.assertRaises(ContractError):self.decode(wire)

    def test_empty_or_oversized_read_selection_is_rejected(self):
        for kind in ('empty','queries','requests'):
            wire=copy.deepcopy(self.wire)
            if kind=='empty':wire['requests'][0].update(queries=[],span_refs=[])
            elif kind=='queries':wire['requests'][0]['queries']=['重复词']*9
            else:wire['requests']*=13
            with self.assertRaises(ContractError):self.decode(wire)

    def test_forged_catalog_cannot_supply_another_source(self):
        description=copy.deepcopy(self.description)
        description['items'][0]['request_binding']['snapshot_hash']='forged'
        with self.assertRaises(ContractError):reading_reference_output_schema(self.task,description)

    def test_decoding_requires_explicit_current_task_and_catalog(self):
        raw=json.dumps(self.wire)
        result=json.loads(structured_json_completion(raw,self.schema,protocol=READING_REFERENCE_PROTOCOL,
            context=(self.task,self.description)))
        self.assertEqual(result,self.decode())
        with self.assertRaises(ContractError):
            structured_json_completion(raw,self.schema,protocol=READING_REFERENCE_PROTOCOL)

    def test_existing_full_reading_protocol_is_unchanged(self):
        result=self.decode()
        original=decode_reading_output(dict(version=1,requests=result['requests']),
                                      reading_output_schema(self.task,self.description))
        self.assertEqual(original,result)

    def test_retained_failed_financial_catalog_has_exact_short_bindings(self):
        path=ROOT/'result/acceptance/one-sentence-research-4lzhr4jd/task.json'
        before=path.read_bytes();task=json.loads(before)
        description=catalog(task)
        choices=reading_reference_description(task,description)
        schema=reading_reference_output_schema(task,description)
        wire=dict(version=1,binding_ref=choices['binding_ref'],requests=[dict(
            source_ref=choices['sources'][0]['source_ref'],queries=['营业收入','净利润'],span_refs=[],
            reason='隔离核对真实留存目录，不重跑研究',question_ids=[task['questions'][0]['id']])])
        result=decode_reading_references(wire,schema,task,description)
        original=next(item for item in description['items'] if item['kind'] in ('material','filing'))
        self.assertEqual(result['requests'][0]['artifact_id'],original['id'])
        self.assertEqual(result['requests'][0]['snapshot_hash'],original['request_binding']['snapshot_hash'])
        self.assertEqual(path.read_bytes(),before)

    def test_official_transport_uses_new_schema_and_preserves_budget(self):
        service=self.service()
        with patch.object(service,'_request_bytes',return_value=json.dumps(self.response()).encode()) as transport:
            result=json.loads(service.reason_structured_reading_references(self.task,[],self.description))
        self.assertEqual(result,self.decode())
        self.assertEqual(self.task['ledger']['used']['model_calls'],1)
        request=transport.call_args.args[1]
        self.assertEqual(json.loads(request.data)['text']['format']['name'],READING_REFERENCE_FUNCTION_NAME)

    def test_normal_model_object_presents_and_records_the_same_protocol(self):
        service=self.service()
        with patch.object(service,'_request_bytes',return_value=json.dumps(self.response()).encode()) as transport:
            result,_=model_object(service,self.task,'read',{'source_catalog':self.description},{'requests'})
        self.assertEqual(result,self.decode())
        data=json.loads(transport.call_args.args[1].data)
        self.assertIn('source_ref',json.dumps(data,ensure_ascii=False))
        event=self.task['ledger']['dispatches'][-1]
        self.assertEqual(event['output_contract']['output_protocol'],READING_REFERENCE_PROTOCOL)
        self.assertEqual(event['output_contract']['output_schema_hash'],fingerprint(self.schema))


if __name__=='__main__':unittest.main()
