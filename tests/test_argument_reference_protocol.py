"""Full-text review by stable units, without rewriting any client statement."""
import copy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from researchhelper import argument_support as review
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.model import ModelService, ModelSettings
from researchhelper.output_schema import structured_json_completion

ROOT=Path(__file__).resolve().parents[1]
RETAINED=ROOT/'result/acceptance/one-sentence-research-9_qa5f41/task.json'


class ArgumentReferenceProtocolTests(unittest.TestCase):
    def setUp(self):
        self.original=RETAINED.read_bytes()
        self.task=json.loads(self.original)
        self.proposal=copy.deepcopy(self.task['host_result'])
        self.request=review.review_request(self.task,self.proposal)
        self.units=review.reference_units(self.request)
        self.schema=review.reference_review_schema(self.request)
        related={ident for target in self.request['targets'] for ident in target['question_ids']}
        self.wire=dict(version=1,binding_ref=self.request['review_hash'][:16],questions=[dict(
            question_id=q['id'],status='answered',reason='隔离协议夹具，不是金融质量批准。',missing_evidence=[])
            for q in self.task['questions'] if q['id'] in related],reviews=[dict(target_id=ident,
                segments=[dict(unit_ref=unit['unit_ref'],verdict='supported',basis_refs=['scope:task'],
                    explanation='模拟标签，只检查格式、覆盖和采用边界。') for unit in units])
                for ident,units in self.units.items()])
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No network'))
        guard.start();self.addCleanup(guard.stop)

    def tearDown(self):self.assertEqual(RETAINED.read_bytes(),self.original)

    def decode(self,wire=None):
        return review.decode_reference_review(self.wire if wire is None else wire,self.schema,self.request)

    def service(self):
        temporary=tempfile.TemporaryDirectory(prefix='argument-ref-service-',dir=ROOT/'result/acceptance')
        self.addCleanup(temporary.cleanup)
        settings=ModelSettings(temporary.name)
        settings.save(dict(endpoint='https://api.deepseek.com/responses',model='deepseek-flash',
            api_key='FAKE_ARGUMENT_REF_KEY',reasoning_effort='low',extraction_mode='direct'))
        self.task['ledger']['used']['model_calls']=0
        return ModelService(settings,offline=False)

    def response(self):
        return dict(object='response',status='completed',error=None,incomplete_details=None,
            output=[dict(type='message',role='assistant',status='completed',
                content=[dict(type='output_text',text=json.dumps(self.wire))])],usage={})

    def test_every_character_of_each_actual_proposed_field_is_present_once(self):
        for target in self.request['targets']:
            self.assertEqual(''.join(unit['text'] for unit in self.units[target['id']]),target['text'])
        self.assertEqual(len(self.decode()['assessments']),len(self.request['targets']))

    def test_units_preserve_decimals_quotes_spaces_and_newlines(self):
        text='  比率50.75%；公司称"不代表回款质量"。\n现金流153.56%。  '
        units=review.reference_units(dict(targets=[dict(id='probe',text=text)]))['probe']
        self.assertEqual(''.join(unit['text'] for unit in units),text)
        self.assertTrue(any('50.75%' in unit['text'] for unit in units))

    def test_long_text_is_grouped_not_clipped_after_sixty_four_sentences(self):
        text='完整语句。'*130
        units=review.reference_units(dict(targets=[dict(id='probe',text=text)]))['probe']
        self.assertLessEqual(len(units),64)
        self.assertEqual(''.join(unit['text'] for unit in units),text)

    def test_model_returns_unit_references_not_another_copy_of_client_text(self):
        self.assertTrue(all('text' not in segment for item in self.wire['reviews'] for segment in item['segments']))
        presented=json.loads(review.review_messages(self.request,use_reference_units=True)[1]['content'])
        for target in presented['targets']:
            self.assertNotIn('text',target)
            self.assertEqual(target['units'],self.units[target['id']])
        self.assertEqual(presented['proposal'],self.request['proposal'])

    def test_missing_duplicate_reordered_or_foreign_units_are_rejected(self):
        for kind in ('missing','duplicate','reordered','foreign'):
            wire=copy.deepcopy(self.wire)
            item=next(item for item in wire['reviews'] if len(item['segments'])>1)
            if kind=='missing':item['segments'].pop()
            elif kind=='duplicate':item['segments'].append(copy.deepcopy(item['segments'][0]))
            elif kind=='reordered':item['segments'].reverse()
            else:item['segments'][0]['unit_ref']='unavailable'
            with self.subTest(kind=kind),self.assertRaises(ContractError):self.decode(wire)

    def test_wrong_binding_cannot_be_replaced_by_the_current_value(self):
        wire=copy.deepcopy(self.wire);wire['binding_ref']='wrong'
        with self.assertRaises(ContractError):self.decode(wire)

    def test_changed_proposal_invalidates_a_previous_reference_review(self):
        proposal=copy.deepcopy(self.proposal)
        next(iter(proposal['answers'].values()))['numeric_segments']['answer'].append({'text':'新增句子。'})
        changed=review.review_request(self.task,proposal)
        with self.assertRaises(ContractError):review.decode_reference_review(self.wire,self.schema,changed)

    def test_inconsistent_internal_hash_is_rejected_before_submission(self):
        request=copy.deepcopy(self.request);request['targets'][0]['text']+='changed'
        with self.assertRaises(ContractError):review.reference_review_schema(request)

    def test_one_rejected_unit_still_rejects_the_whole_target(self):
        wire=copy.deepcopy(self.wire);wire['reviews'][0]['segments'][0]['verdict']='insufficient'
        result=self.decode(wire)
        self.assertFalse(result['supported'])
        self.assertFalse(next(item for item in result['assessments'] if
            item['target_id']==wire['reviews'][0]['target_id'])['supported'])

    def test_reviewer_cannot_fill_missing_support_with_an_uncited_fact(self):
        wire=copy.deepcopy(self.wire)
        target=next(target for target in self.request['targets'] if any(
            item['id'] not in target['evidence_refs'] for item in self.request['evidence']))
        foreign=next(item['id'] for item in self.request['evidence'] if item['id'] not in target['evidence_refs'])
        next(item for item in wire['reviews'] if item['target_id']==target['id'])['segments'][0]['basis_refs']=['evidence:'+foreign]
        with self.assertRaises(ContractError):self.decode(wire)

    def test_missing_question_review_is_not_full_completion(self):
        wire=copy.deepcopy(self.wire);wire['questions'].pop()
        with self.assertRaises(ContractError):self.decode(wire)

    def test_supported_prose_does_not_override_missing_critical_data(self):
        wire=copy.deepcopy(self.wire);wire['questions'][0].update(status='requires_evidence',missing_evidence=['必要依据待核验'])
        result=self.decode(wire)
        self.assertTrue(result['supported']);self.assertFalse(result['answer_complete'])

    def test_empty_explanation_and_source_approval_injection_are_rejected(self):
        for key,value in (('explanation',''),('source_check','通过')):
            wire=copy.deepcopy(self.wire);wire['reviews'][0]['segments'][0][key]=value
            with self.subTest(key=key),self.assertRaises(ContractError):self.decode(wire)

    def test_normalized_result_retains_exact_full_hash_offsets_and_no_causal_certification(self):
        result=self.decode()
        self.assertEqual(result['review_hash'],self.request['review_hash'])
        self.assertFalse(result['source_approval']);self.assertFalse(result['independent_causal_proof'])
        for assessment in result['assessments']:
            target=next(t for t in self.request['targets'] if t['id']==assessment['target_id'])
            self.assertEqual(assessment['segments'][-1]['end'],len(target['text']))

    def test_official_transport_preserves_model_effort_and_one_call_accounting(self):
        service=self.service()
        with patch.object(service,'_request_bytes',return_value=json.dumps(self.response()).encode()) as call:
            result=json.loads(service.reason_argument_support(self.task,self.proposal))
        self.assertEqual(result,self.decode())
        body=json.loads(call.call_args.args[1].data)
        self.assertEqual(body['text']['format']['name'],review.REFERENCE_FUNCTION_NAME)
        self.assertEqual(body['reasoning'],{'effort':'low'})
        self.assertEqual(self.task['ledger']['used']['model_calls'],1)

    def test_shared_decoder_uses_the_current_task_and_actual_proposal(self):
        value=structured_json_completion(json.dumps(self.wire),self.schema,
            protocol=review.REFERENCE_PROTOCOL,context=(self.task,self.proposal))
        self.assertEqual(json.loads(value),self.decode())


if __name__=='__main__':unittest.main()
