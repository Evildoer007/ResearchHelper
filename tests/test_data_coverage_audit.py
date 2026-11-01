"""Audit labels cannot silently approve raw numbers or missing scope."""
import unittest
from tests.audit_retained_data_coverage import coverage_status, require_local, BASE


class DataCoverageAuditTests(unittest.TestCase):
    def test_raw_amounts_are_not_adopted_facts(self):
        self.assertIn('口径未核实', coverage_status(adopted_facts=0, raw_rows=48))

    def test_metadata_does_not_prove_the_universe(self):
        self.assertIn('完整范围', coverage_status(adopted_facts=0, raw_rows=48, metadata_verified=True))

    def test_existing_facts_only_describe_the_listed_sample(self):
        value = coverage_status(adopted_facts=9, raw_rows=48)
        self.assertIn('仅所列样本', value)
        self.assertNotIn('全部', value)

    def test_unrecorded_file_or_external_file_cannot_be_used_as_proof(self):
        with self.assertRaises(FileNotFoundError): require_local(BASE/'not_an_acceptance_record.json')
        with self.assertRaises(ValueError): require_local(BASE.parents[1]/'README.md')


if __name__ == '__main__': unittest.main()
