"""Independent first-use checks. Real loopback HTTP, no external requests."""

import unittest

from tests import test_runtime_policy_offline as runtime_fixture


class ResearchReadinessTests(runtime_fixture.RuntimePolicyTests):
    # Run only these new cases explicitly; do not repeat the inherited matrix.
    def test_empty_and_partial_configuration_are_not_ready(self):
        self.assertEqual(self.bootstrap()['research_readiness'], {'model_configured': False})
        endpoint = runtime_fixture.MODEL['endpoint']
        for fields in ({'endpoint': endpoint}, {'endpoint': endpoint, 'model': 'fixture'},
                       {'endpoint': endpoint, 'model': '', 'api_key': 'fabricated-credential'}):
            self.assertEqual(self.call('/api/settings/model', fields, self.admin)[0], 200)
            self.assertFalse(self.bootstrap()['research_readiness']['model_configured'])
        self.assertEqual(self.server.agent.archive.list(), [])

    def test_saved_model_is_ready_but_does_not_enable_or_call_services(self):
        self.save_model()
        self.assertEqual(self.call('/api/settings/data', runtime_fixture.SOURCES, self.admin)[0], 200)
        boot = self.bootstrap()
        self.assertTrue(boot['research_readiness']['model_configured'])
        self.assertEqual(boot['execution']['mode'], 'offline')
        self.assertEqual(self.server.agent.archive.list(), [])
        self.assertEqual(self.external_attempts, [])

    def test_sales_gets_only_readiness_and_cannot_change_settings(self):
        self.save_model()
        self.assertEqual(self.call('/api/settings/data', runtime_fixture.SOURCES, self.admin)[0], 200)
        boot = self.bootstrap(self.sales)
        self.assertEqual(boot['research_readiness'], {'model_configured': True})
        self.assertEqual(boot['model_settings'], {})
        self.assertEqual(boot['source_settings'], {})
        for value in runtime_fixture.MODEL.values():
            self.assertNotIn(value, str(boot))
        for route in ('model', 'data', 'execution'):
            self.assertEqual(self.call('/api/settings/' + route, {}, self.sales)[0], 403)

    def test_cleared_key_updates_readiness_for_both_roles(self):
        self.save_model()
        status, _, _ = self.call('/api/settings/model', {'clear_key': True}, self.admin)
        self.assertEqual(status, 200)
        for cookie in (self.admin, self.sales):
            self.assertFalse(self.bootstrap(cookie)['research_readiness']['model_configured'])
        self.assertEqual(self.server.agent.archive.list(), [])


NEW_CASES = ('test_empty_and_partial_configuration_are_not_ready',
             'test_saved_model_is_ready_but_does_not_enable_or_call_services',
             'test_sales_gets_only_readiness_and_cannot_change_settings',
             'test_cleared_key_updates_readiness_for_both_roles')


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(ResearchReadinessTests(name) for name in NEW_CASES)


if __name__ == '__main__':
    unittest.main()
