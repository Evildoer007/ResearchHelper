"""Replay closed real receipt state without reopening or approving the task."""
import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from tests import ROOT
from tests import test_initial_acquisition_navigation as fixtures
from researchhelper import acquisition
from researchhelper.financial_sources import financial_batch_acquisition_state

FIXTURE = ROOT / 'result/acceptance/autonomous-data-phase-e4xkcfaz/task.json'


class ReceivedFinancialNavigationTests(unittest.TestCase):
    def setUp(self):
        raw = FIXTURE.read_bytes()
        self.digest = hashlib.sha256(raw).hexdigest()
        self.task = json.loads(raw)
        self.guard = patch('socket.socket.connect', side_effect=AssertionError('Offline only'))
        self.guard.start(); self.addCleanup(self.guard.stop)

    def tearDown(self):
        self.assertEqual(hashlib.sha256(FIXTURE.read_bytes()).hexdigest(), self.digest)

    def test_actual_batch_is_explicitly_received_without_approving_its_amounts(self):
        before = copy.deepcopy(self.task)
        result = financial_batch_acquisition_state(self.task)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['row_count'], 48)
        self.assertTrue(result[0]['response_already_retained'])
        self.assertFalse(result[0]['financial_values_approved'])
        self.assertFalse(result[0]['full_universe_verified'])
        self.assertEqual([item['received'] for item in result[0]['fields']], [48, 48])
        self.assertNotIn('raw_value', json.dumps(result))
        self.assertNotIn('rows', result[0])
        self.assertEqual(self.task, before)

    def test_imported_or_changed_response_cannot_claim_receipt(self):
        for record in self.task['source_records']:
            record.pop('acquisition', None)
        self.assertEqual(financial_batch_acquisition_state(self.task), [])

    def test_initial_navigation_receives_the_actual_state_not_just_the_plan(self):
        fixture = fixtures.InitialAcquisitionNavigationTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.task['financial_batch_followup_version'] = 1
        state = financial_batch_acquisition_state(self.task)
        with patch('researchhelper.financial_sources.financial_batch_acquisition_state', return_value=state):
            fixture.run_navigation()
        payload = fixture.model.call_args_list[0].args[3]
        self.assertEqual(payload['received_financial_queries'], state)


if __name__ == '__main__':
    unittest.main()
