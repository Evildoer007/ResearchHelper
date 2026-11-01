"""The new OS gate cannot be replaced by parent-only or incomplete evidence."""
import copy
import errno
import unittest

from tests.harness_release_gate import (LOCAL_ONLY_SANDBOX, PROBE_ADDRESS,
    NESTED_PROFILE_TESTS, validate_network_gate)


class NetworkGateTests(unittest.TestCase):
    def fixture(self):
        now = {'kernel.py': 'fixed-input-hash'}
        source = dict(status='source_accepted_pending_delivery', source_hashes=now,
            approved_for_staged_build=True, approved_for_installation=False,
            unexpected_records=[], skipped_test_ids=[], old_tests_unchanged=True,
            source_unchanged=True, production_unchanged=True, closed_research_unchanged=True)
        checks = [{'protocol': protocol, 'errno': errno.EPERM, 'denied': True} for protocol in ('tcp','udp')]
        proof = dict(parent=checks, child=copy.deepcopy(checks), probe_address=PROBE_ADDRESS,
            parent_loopback_allowed=True, child_loopback_allowed=True, child_finished=True, server_stopped=True)
        record = dict(status='source_network_accepted_pending_delivery', source_hashes=now,
            sandbox_profile=LOCAL_ONLY_SANDBOX, source_process_finished=True, source_unchanged=True,
            approved_for_installation=False, full_harness_complete=False,
            isolation_proofs=[proof, copy.deepcopy(proof)],
            nested_profile_execution=dict(status='passed', source_hashes=now,
                unique_test_ids=sorted(NESTED_PROFILE_TESTS), tests_run=2,
                failure_records=[], skipped_test_ids=[], original_sandbox_commands_unchanged=True,
                source_unchanged=True))
        return record, source, now

    def test_complete_inherited_proof_is_source_only_not_installation(self):
        record, source, now = self.fixture()
        validate_network_gate(record, source, now)
        self.assertFalse(record['approved_for_installation'])

    def test_child_timeout_missing_protocol_stale_source_and_installation_are_rejected(self):
        for defect in ('timeout', 'missing-udp', 'stale', 'install', 'parent-only'):
            record, source, now = self.fixture()
            if defect == 'timeout':
                record['isolation_proofs'][0]['child'][0]['errno'] = errno.ETIMEDOUT
            elif defect == 'missing-udp':
                record['isolation_proofs'][0]['child'].pop()
            elif defect == 'stale':
                now = {'kernel.py': 'new-input-hash'}
            elif defect == 'install':
                record['approved_for_installation'] = True
            else:
                record['isolation_proofs'][0]['child'] = []
            with self.subTest(defect=defect), self.assertRaises(AssertionError):
                validate_network_gate(record, source, now)


if __name__ == '__main__':
    unittest.main()
