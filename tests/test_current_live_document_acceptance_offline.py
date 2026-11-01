"""Independent acceptance-entry guards; never resolve or send a real key."""
import os
import unittest
from unittest.mock import patch

from tests.accept_current_live_document_reading import AUTHORIZATION, eligible_configuration, run, task_context
from tests.accept_packaged_https_trust import verified_before_authentication


class LiveDocumentEntryGuards(unittest.TestCase):
    def test_missing_authorization_never_opens_runtime(self):
        with patch.dict(os.environ, {}, clear=True), patch('tests.accept_current_live_document_reading.AuthorizedRuntime') as runtime:
            with self.assertRaises(RuntimeError):
                run()
            runtime.assert_not_called()

    def test_authorization_without_explicit_candidate_never_opens_runtime(self):
        with patch.dict(os.environ, {'RESEARCHHELPER_AUTHORIZED_LIVE_CASE': AUTHORIZATION}, clear=True), patch('tests.accept_current_live_document_reading.AuthorizedRuntime') as runtime:
            with self.assertRaises(RuntimeError):
                run()
            runtime.assert_not_called()

    def test_only_independent_official_flash_configuration_is_eligible(self):
        valid = {'endpoint': 'https://api.deepseek.com/v1', 'model': 'deepseek-flash', 'api_key': 'FAKE_ONLY'}
        self.assertEqual(eligible_configuration(valid), valid)
        for change in ({'endpoint': 'https://foreign.invalid/v1'}, {'endpoint': 'https://api.deepseek.com:8443/v1'},
                {'endpoint': 'https://fake@api.deepseek.com/v1'}, {'endpoint': 'https://api.deepseek.com/v1?secret=x'},
                {'model': 'deepseek-v4-pro'}, {'api_key': ''}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                eligible_configuration(dict(valid, **change))

    def test_context_is_exact_and_contains_no_credential_or_material(self):
        task = {'id': 'owned', 'version': 1, 'batch': 0, 'cutoff': '2026-10-13T00:00:00Z',
                'input_hash': 'hash', 'credential': 'FAKE_ONLY', 'material': 'text'}
        self.assertEqual(set(task_context(task)), {'id', 'version', 'batch', 'cutoff', 'input_hash'})

    def test_tls_evidence_requires_authenticated_transport_status_and_cleanup(self):
        record = {'failure_code': 'authentication_failed', 'http_status': 401, 'https_attempts': 1,
            'credential_is_fabricated': True, 'fabricated_key_removed': True,
            'production_paths_denied': True, 'service_stopped': True, 'mount_detached': True}
        self.assertTrue(verified_before_authentication(record))
        for change in ({'failure_code': 'tls_failure'}, {'http_status': None}, {'https_attempts': 2},
                {'fabricated_key_removed': False}, {'production_paths_denied': False}):
            with self.subTest(change=change):
                self.assertFalse(verified_before_authentication(dict(record, **change)))


if __name__ == '__main__':
    unittest.main()
