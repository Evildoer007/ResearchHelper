"""Retain the identity checks with the explicit acquisition prompt revision."""
import copy
import importlib

original = importlib.import_module(
    'tests.test_identity_period_current_guidance' if __package__ else 'test_identity_period_current_guidance')
from researchhelper.reasoning import RULES_VERSION, evidence_contract


class NavigationIdentityPeriodTests(original.CurrentIdentityPeriodProvenanceTests):
    def test_contract_provides_exact_reference_without_mutation(self):
        self.witness()
        before = copy.deepcopy(self.task)
        contract = evidence_contract(self.task)['identity_period_provenance']
        self.assertEqual(contract['admissible_bases'][0]['identity'], self.spec['identity'])
        self.assertEqual(self.task, before)
        self.assertEqual(RULES_VERSION, 41)
