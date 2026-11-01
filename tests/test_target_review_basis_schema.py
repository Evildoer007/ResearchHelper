"""The wire schema enforces target citations without hiding counterevidence."""
import copy
import importlib
import unittest

from researchhelper import argument_support as review
from researchhelper.contracts import ContractError
from researchhelper.output_schema import _matches

fixtures = importlib.import_module((__package__+'.' if __package__ else '')+
                                   'test_argument_reference_protocol')


class TargetReviewBasisSchemaTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ArgumentReferenceProtocolTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        self.schema, self.request = self.fixture.schema, self.fixture.request
        self.wire = copy.deepcopy(self.fixture.wire)
        self.target = next(target for target in self.request['targets'] if any(
            item['id'] not in target['evidence_refs'] for item in self.request['evidence']))
        self.foreign = 'evidence:'+next(item['id'] for item in self.request['evidence']
            if item['id'] not in self.target['evidence_refs'])
        self.segment = next(item for item in self.wire['reviews']
                            if item['target_id']==self.target['id'])['segments'][0]

    def test_valid_cited_support_remains_in_the_wire_schema(self):
        self.assertTrue(_matches(self.wire,self.schema))
        self.assertTrue(self.fixture.decode(self.wire)['supported'])

    def test_uncited_support_is_rejected_by_schema_before_semantic_adoption(self):
        self.segment['basis_refs']=[self.foreign]
        self.assertFalse(_matches(self.wire,self.schema))
        with self.assertRaises(ContractError): self.fixture.decode(self.wire)

    def test_empty_positive_basis_cannot_be_schema_valid(self):
        self.segment['basis_refs']=[]
        self.assertFalse(_matches(self.wire,self.schema))

    def test_uncited_counterevidence_is_still_available_to_negative_review(self):
        for verdict in ('insufficient','contradicted'):
            self.segment.update(verdict=verdict,basis_refs=[self.foreign])
            with self.subTest(verdict=verdict):
                self.assertTrue(_matches(self.wire,self.schema))
                self.assertFalse(self.fixture.decode(self.wire)['supported'])

    def test_missing_evidence_can_be_reported_without_inventing_a_basis(self):
        self.segment.update(verdict='insufficient',basis_refs=[])
        self.assertTrue(_matches(self.wire,self.schema))
        self.assertFalse(self.fixture.decode(self.wire)['supported'])

    def test_unknown_and_duplicate_references_remain_invalid(self):
        for refs in (['evidence:unavailable'],['scope:task','scope:task']):
            self.segment.update(verdict='insufficient',basis_refs=refs)
            with self.subTest(refs=refs): self.assertFalse(_matches(self.wire,self.schema))

    def test_missing_units_and_questions_still_fail_complete_coverage(self):
        wire=copy.deepcopy(self.wire)
        wire['reviews'][0]['segments'].pop()
        with self.assertRaises(ContractError): self.fixture.decode(wire)
        wire=copy.deepcopy(self.wire)
        wire['questions'].pop()
        with self.assertRaises(ContractError): self.fixture.decode(wire)

    def test_schema_does_not_change_the_canonical_review_contract(self):
        self.assertEqual(review.VERSION,2)
        result=self.fixture.decode(self.wire)
        self.assertFalse(result['source_approval'])
        self.assertFalse(result['independent_causal_proof'])
