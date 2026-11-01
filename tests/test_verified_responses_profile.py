"""Responses profile eligibility, separate from retained Beta profile tests."""
import json
from pathlib import Path
import unittest

try:
    from tests import test_verified_research_endpoint_profile as fixtures
except ModuleNotFoundError:
    import test_verified_research_endpoint_profile as fixtures


class VerifiedResponsesProfileTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.VerifiedResearchEndpointProfileTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.study['temporary_endpoint_override']['endpoint']='https://api.deepseek.com/responses'
        for call in self.fixture.study['ledger']['dispatches']:
            call['completion_api']='deepseek_responses_v1'

    def test_verified_responses_changes_no_credential_or_model(self):
        before=self.fixture.settings.read(private=True)
        result=self.fixture.apply()
        after=self.fixture.settings.read(private=True)
        self.assertEqual(after,dict(before,endpoint='https://api.deepseek.com/responses'))
        self.assertNotIn('SYNTHETIC_LOCAL_ONLY',Path(result['receipt']).read_text())

    def test_unproved_transport_cannot_change_production_endpoint(self):
        self.fixture.study['ledger']['dispatches'][0].pop('completion_api')
        before=self.fixture.settings.read(private=True)
        with self.assertRaises(RuntimeError):self.fixture.apply()
        self.assertEqual(before,self.fixture.settings.read(private=True))

    def test_similar_external_route_not_eligible(self):
        self.fixture.study['temporary_endpoint_override']['endpoint']='https://different.invalid/responses'
        with self.assertRaises(RuntimeError):self.fixture.apply()

    def test_plain_config_json_has_no_secret_values(self):
        result=self.fixture.apply()
        receipt=json.loads(Path(result['receipt']).read_text())
        self.assertFalse(receipt['credential_value_changed'])
        self.assertFalse(receipt['model_changed'])


if __name__=='__main__':unittest.main()
