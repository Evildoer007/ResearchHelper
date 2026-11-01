"""No broad network exemption is introduced to make offline tests pass."""
import unittest

from tests.packaged_loopback_runner import loopback_profile, NETWORK_PREFIX, run


class PackagedLoopbackPolicyTests(unittest.TestCase):
    def test_external_denial_and_existing_file_restrictions_remain(self):
        files='(deny file-write* (subpath "/isolated/payload"))'
        profile=loopback_profile(NETWORK_PREFIX+files)
        self.assertIn('(deny network*)',profile)
        self.assertTrue(profile.endswith(files))
        self.assertEqual(profile.count('localhost:*'),2)
        self.assertNotIn('(remote tcp "*',profile)

    def test_unknown_or_already_relaxed_profiles_are_rejected(self):
        for profile in ('(version 1)(allow default)', NETWORK_PREFIX+'(allow network*)'):
            with self.assertRaises(ValueError):loopback_profile(profile)

    def test_non_http_test_modules_cannot_request_permission(self):
        for modules in ([],['test_argument_support_review'],['test_task_read_snapshot','test_model_settings_offline']):
            with self.assertRaises(ValueError):run('skill','/unused','/unused','/unused',modules,1)
