"""New App reviews use local basis addresses; canonical host reviews stay v2."""
import copy
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper import argument_support as review
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.output_schema import _matches

fixtures=importlib.import_module((__package__+'.' if __package__ else '')+'test_argument_reference_protocol')
ROOT=Path(__file__).resolve().parents[1]


class ReviewBasisAliasTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.ArgumentReferenceProtocolTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        self.task=copy.deepcopy(self.fixture.task)
        self.task['argument_reference_format']=2
        self.proposal=copy.deepcopy(self.fixture.proposal)
        self.request=review.review_request(self.task,self.proposal)
        self.schema=review.reference_review_schema(self.request)
        self.aliases=review.reference_basis_choices(self.request)
        self.reverse={value:key for key,value in self.aliases.items()}
        self.wire=copy.deepcopy(self.fixture.wire)
        self.wire.update(version=2,binding_ref=self.request['review_hash'][:16])
        for item in self.wire['reviews']:
            for segment in item['segments']:
                segment['basis_refs']=[self.reverse[ref] for ref in segment['basis_refs']]

    def decode(self,wire=None):
        return review.decode_reference_review(self.wire if wire is None else wire,self.schema,self.request)

    def test_aliases_decode_to_the_same_canonical_assessments(self):
        actual=self.decode()
        expected=self.fixture.decode()
        self.assertEqual(actual['assessments'],expected['assessments'])
        self.assertEqual(actual['question_assessments'],expected['question_assessments'])
        self.assertFalse(actual['source_approval'])
        self.assertEqual(review.VERSION,2)

    def test_new_wire_has_short_basis_addresses_not_composite_identifiers(self):
        self.assertEqual(self.schema['properties']['version']['enum'],[2])
        self.assertTrue(all(len(ref)<8 and ':' not in ref for ref in self.aliases))
        self.assertTrue(_matches(self.wire,self.schema))

    def test_legacy_encoding_remains_valid_only_for_its_original_request(self):
        self.assertTrue(_matches(self.fixture.wire,self.fixture.schema))
        wire=copy.deepcopy(self.wire)
        wire['reviews'][0]['segments'][0]['basis_refs']=['scope:task']
        with self.assertRaises(ContractError):self.decode(wire)

    def test_unknown_basis_and_forged_binding_are_not_guessed(self):
        for field,value in (('basis_refs',['e99999']),('basis_refs',['evidence:foreign'])):
            wire=copy.deepcopy(self.wire)
            wire['reviews'][0]['segments'][0][field]=value
            with self.subTest(value=value),self.assertRaises(ContractError):self.decode(wire)
        wire=copy.deepcopy(self.wire);wire['binding_ref']='foreign'
        with self.assertRaises(ContractError):self.decode(wire)

    def test_uncited_positive_basis_is_not_licensed_by_a_short_address(self):
        target=next(target for target in self.request['targets'] if any(
            item['id'] not in target['evidence_refs'] for item in self.request['evidence']))
        foreign=next(item['id'] for item in self.request['evidence'] if item['id'] not in target['evidence_refs'])
        segment=next(item for item in self.wire['reviews'] if item['target_id']==target['id'])['segments'][0]
        segment['basis_refs']=[self.reverse['evidence:'+foreign]]
        self.assertFalse(_matches(self.wire,self.schema))
        with self.assertRaises(ContractError):self.decode()
        segment['verdict']='contradicted'
        self.assertTrue(_matches(self.wire,self.schema))
        self.assertFalse(self.decode()['supported'])

    def test_changed_wording_invalidates_old_addresses_even_when_their_names_match(self):
        proposal=copy.deepcopy(self.proposal)
        next(iter(proposal['answers'].values()))['numeric_segments']['answer'].append(dict(text='新的说明。'))
        changed=review.review_request(self.task,proposal)
        self.assertEqual(review.reference_basis_choices(changed),self.aliases)
        with self.assertRaises(ContractError):review.decode_reference_review(self.wire,self.schema,changed)

    def test_prompt_presents_basis_addresses_and_target_specific_allowed_support(self):
        before=copy.deepcopy(self.request)
        messages=review.review_messages(self.request,use_reference_units=True)
        presented=json.loads(messages[1]['content'])
        self.assertIn('只返回版本2',messages[0]['content'])
        self.assertIn('不拼接evidence:',messages[0]['content'])
        for name,kind in (('evidence','evidence'),('calculations','calculation'),('knowledge','knowledge')):
            for item in presented[name]:
                self.assertEqual(self.aliases[item['basis_ref']],kind+':'+item['id'])
        self.assertTrue(all('scope' in item['allowed_support_basis_refs'] for item in presented['targets']))
        self.assertEqual(self.request,before)

    def test_new_formal_tasks_use_new_encoding_without_modifying_legacy_tasks(self):
        with tempfile.TemporaryDirectory(prefix='basis-alias-',dir=ROOT/'result/acceptance') as folder:
            agent=ResearchAgent(ResearchArchive(folder),ROOT,offline=True,require_argument_support=True)
            try:
                task=agent.create('核对现有研究资料。')
                self.assertEqual(task['argument_reference_format'],2)
            finally:
                for worker in agent.close_runtime():worker.join(timeout=5)
        self.assertNotIn('argument_reference_format',self.fixture.task)

    def test_official_transport_accepts_alias_wire_and_retains_one_call_accounting(self):
        service=self.fixture.service()
        self.task['ledger']['used']['model_calls']=0  # Mock-only isolated accounting.
        response=dict(object='response',status='completed',error=None,incomplete_details=None,
            output=[dict(type='message',role='assistant',status='completed',content=[
                dict(type='output_text',text=json.dumps(self.wire))])],usage={})
        before=self.task['ledger']['used']['model_calls']
        with patch.object(service,'_request_bytes',return_value=json.dumps(response).encode()) as transport:
            result=json.loads(service.reason_argument_support(self.task,self.proposal))
        self.assertEqual(result,self.decode())
        self.assertEqual(self.task['ledger']['used']['model_calls'],before+1)
        transport.assert_called_once()
