"""Replay actual capacity failure, preserving all text and question obligations."""
import copy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.contracts import canonical
from researchhelper.model import ModelService,ModelSettings
from researchhelper.output_schema import financial_reference_prompt,financial_reference_output_schema
from researchhelper.reasoning import evidence_contract,question_specs,model_object,MAX_CONTEXT_CHARACTERS

ROOT=Path(__file__).resolve().parents[1]
RETAINED=ROOT/'result/acceptance/one-sentence-research-nnmcq41i/task.json'


class PassageContextCapacityTests(unittest.TestCase):
    def setUp(self):
        self.original=RETAINED.read_bytes()
        self.task=json.loads(self.original)
        bundle=self.task['reasoning_state']['reading']['bundle']
        views=bundle['views']
        contract=evidence_contract(self.task,views)
        self.payload=dict(question=self.task['question'],intent=self.task['intent'],
            objects=self.task['objects'],cutoff=self.task['cutoff'],questions=question_specs(self.task),
            calculations=self.task['calculation_specs'],evidence_contract=contract,
            field_registry={},untrusted_sources=views,source_reading={
                key:bundle[key] for key in ('reading_hash','coverage','limitations')})
        self.payload['calculation_input_targets']=copy.deepcopy(contract['calculation_input_requirements']['proposal_requirements'])
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No network'))
        guard.start();self.addCleanup(guard.stop)

    def tearDown(self):self.assertEqual(RETAINED.read_bytes(),self.original)

    def test_actual_four_questions_and_all_read_characters_fit_without_capacity_increase(self):
        self.assertEqual(len(self.task['questions']),4)
        self.assertEqual(MAX_CONTEXT_CHARACTERS,160000)
        projected=financial_reference_prompt(self.payload,self.task)
        schema=financial_reference_output_schema(self.task,self.payload['untrusted_sources'])
        self.assertLess(len(canonical(self.payload)),MAX_CONTEXT_CHARACTERS)
        self.assertLess(len(canonical(projected))+len(canonical(schema)),MAX_CONTEXT_CHARACTERS)
        for before,after in zip(self.payload['untrusted_sources'],projected['untrusted_sources']):
            for first,second in zip(before['parts'],after['parts']):
                self.assertEqual(first['text'],''.join(text for _,text in second['quote_choices']))
        self.assertEqual(projected['questions'],self.payload['questions'])

    def test_normal_model_path_dispatches_once_after_context_compaction(self):
        temporary=tempfile.TemporaryDirectory(prefix='passage-capacity-',dir=ROOT/'result/acceptance')
        self.addCleanup(temporary.cleanup)
        settings=ModelSettings(temporary.name)
        settings.save(dict(endpoint='https://api.deepseek.com/responses',model='deepseek-flash',
            api_key='FAKE_CONTEXT_CAPACITY_KEY',reasoning_effort='low',extraction_mode='direct'))
        model=ModelService(settings,offline=False)
        projected=financial_reference_prompt(self.payload,self.task)
        wire=dict(version=8,source_reference=projected['financial_reference_binding'],
            current_version_only=True,financial_cells=[],text_quotes=[])
        response=dict(object='response',status='completed',error=None,incomplete_details=None,
            output=[dict(type='message',role='assistant',status='completed',content=[
                dict(type='output_text',text=json.dumps(wire))])],usage={})
        before=self.task['ledger']['used']['model_calls']
        with patch.object(model,'_request_bytes',return_value=json.dumps(response).encode()) as transport:
            output,receipt=model_object(model,self.task,'extract',self.payload,{'evidence_specs'})
        self.assertEqual(transport.call_count,1)
        self.assertEqual(self.task['ledger']['used']['model_calls'],before+1)
        self.assertEqual(output,{'evidence_specs':[]})  # Format-only mock, not financial approval.
        self.assertEqual(receipt['output_protocol'],'strict_financial_reference_v1')
        self.assertEqual(receipt['financial_selection_rules_version'],1)
        body=json.loads(transport.call_args.args[1].data)
        system=body['input'][0]['content']
        self.assertIn('question_id和need_ids两个键',system)
        self.assertNotIn('每项含id、artifact_id、object、record_locator、text',system)


if __name__=='__main__':unittest.main()
