"""Verify the rejected subject and context cases using a retained financial original."""
import copy
import hashlib
import unittest
from unittest.mock import patch

from tests.test_financial_context_choices import legacy_financial_context, misplaced_entity_basis
from tests.test_financial_value_reference import FIXTURE
from researchhelper.reasoning import filing_object_binding,evidence_contract
from researchhelper.output_schema import financial_extraction_output_schema
from researchhelper.evidence import candidate_evidence
from researchhelper.contracts import ContractError

class FilingObjectBindingTests(unittest.TestCase):
    def setUp(self):
        self.task, self.views, specs, self.digest = legacy_financial_context()
        self.record=next(record for record in self.task['source_records'] if record['kind']=='filing')
        self.specs = [dict(misplaced_entity_basis(spec), object=spec['calculation_context']['entity_id'])
                      for spec in specs]

    def tearDown(self):
        self.assertEqual(hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),self.digest)

    def test_legal_entity_name_does_not_silently_become_a_security_code(self):
        original=self.specs[0]
        self.assertNotEqual(original['object'],self.record['directory']['issuer'])
        with self.assertRaisesRegex(ContractError,'公告主体'):
            candidate_evidence(self.task,self.record,original)

    def test_explicit_verified_issuer_fixes_source_object_without_changing_any_fact(self):
        for spec in self.specs:
            original=copy.deepcopy(spec)
            spec['object']=filing_object_binding(self.task,self.record)
            # This check isolates the source-object gate. The independent
            # test below keeps the known bad context rejection fully active.
            with patch('researchhelper.evidence.validate_calculation_context_basis'):
                result=candidate_evidence(self.task,self.record,spec)
            self.assertEqual(result['source_check'],'通过')
            self.assertEqual({key:value for key,value in spec.items() if key!='object'},
                {key:value for key,value in original.items() if key!='object'})

    def test_missing_context_is_still_rejected_after_source_object_is_corrected(self):
        spec=self.specs[0];spec['object']=filing_object_binding(self.task,self.record)
        with self.assertRaisesRegex(ContractError,'计算口径'):
            candidate_evidence(self.task,self.record,spec)

    def test_schema_and_contract_distinguish_security_object_from_entity_context(self):
        views=self.views
        source=evidence_contract(self.task,views)['sources'][0]
        self.assertEqual(source['object_binding']['value'],self.record['directory']['issuer'])
        schema=financial_extraction_output_schema(self.task,views)
        self.assertEqual(schema['properties']['financial_cells']['items']['properties']['object']['enum'],
                         [self.record['directory']['issuer']])

    def test_unmapped_issuer_does_not_create_a_new_alias_or_scope(self):
        record=copy.deepcopy(self.record);record['directory']['issuer']='999999.SH'
        self.assertIsNone(filing_object_binding(self.task,record))
