"""Retain all identity-proof checks with the current reasoning revision.

The original file remains unchanged. Only its exact prompt-revision assertion
is updated here, from39 to40; every source, immutability and provenance check
is inherited, including the behavioral assertions in the replaced method.
"""
import copy
import importlib

original = importlib.import_module(
    "tests.test_identity_period_provenance" if __package__ else "test_identity_period_provenance")
from researchhelper.reasoning import RULES_VERSION, evidence_contract


class CurrentIdentityPeriodProvenanceTests(original.IdentityPeriodProvenanceTests):
    def test_contract_provides_exact_reference_without_mutation(self):
        self.witness()
        before = copy.deepcopy(self.task)
        contract = evidence_contract(self.task)["identity_period_provenance"]
        self.assertEqual(contract["admissible_bases"][0]["identity"], self.spec["identity"])
        self.assertEqual(self.task, before)
        self.assertEqual(RULES_VERSION, 40)
