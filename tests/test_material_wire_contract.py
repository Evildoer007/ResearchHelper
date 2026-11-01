"""Prompt wire keys match registered decoders; unknown approval fields fail."""
import copy
import unittest

from researchhelper import reasoning


class MaterialWireContractTests(unittest.TestCase):
    def setUp(self):
        self.payload={'evidence_contract':{'source_bindings_omitted':0,
            'sources':[{'artifact_id':'original','kind':'material','snapshot_hash':'bound'}]},
            'untrusted_sources':[{'id':'original','kind':'material','snapshot_hash':'bound',
                'parts':[{'locator':'p1','text':'Actual source text.'}]}]}

    def test_presented_keys_include_only_canonical_fields_and_supported_shortcuts(self):
        presented,applied=reasoning.material_prompt_projection(self.payload)
        self.assertTrue(applied)
        self.assertEqual(set(presented['evidence_contract']['allowed_fields']),
            reasoning.MODEL_EVIDENCE_FIELDS|{'financial_value_reference','material_context_reference'})
        self.assertNotIn('publication_mode',presented['evidence_contract']['allowed_fields'])

    def test_canonical_evidence_does_not_gain_compact_or_approval_fields(self):
        self.assertFalse({'financial_value_reference','material_context_reference','approved',
            'source_verified','publication_mode'}.intersection(reasoning.MODEL_EVIDENCE_FIELDS))

    def test_projection_preserves_original_source_and_caller_input(self):
        before=copy.deepcopy(self.payload)
        presented,_=reasoning.material_prompt_projection(self.payload)
        self.assertEqual(self.payload,before)
        self.assertEqual(presented['untrusted_sources'],before['untrusted_sources'])

    def test_unknown_fields_report_names_without_approving_or_leaking_values(self):
        error=reasoning.EvidenceProposalFieldsError(2,{'approved','private content'})
        self.assertEqual(error.diagnostic['fields'],['approved'])
        self.assertEqual(error.diagnostic['field_count'],2)
        self.assertEqual(error.diagnostic['specification_index'],2)
        self.assertFalse(error.diagnostic['evidence_approved'])
        self.assertNotIn('private content',str(error.diagnostic))
