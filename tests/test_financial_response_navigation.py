"""Actual retained API receipt: retrieval progress is not financial approval."""
import copy
import importlib
import unittest

from researchhelper import acquisition
from researchhelper.contracts import ContractError
from researchhelper.financial_sources import _metadata_records
from researchhelper.planning import input_fingerprint

fixtures = importlib.import_module((__package__ + '.' if __package__ else '') + 'test_metadata_navigation_progress')
POLICY = 'financial_response_navigation_version'


class FinancialResponseNavigationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.MetadataNavigationProgressTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        self.task = self.fixture.task
        self.task[POLICY] = 1
        self.task['source_records'] = [copy.deepcopy(self.fixture.policy)]

    def test_actual_raw_business_response_is_progress_without_unit_approval(self):
        before = acquisition.navigation_progress(self.task)
        self.task['source_records'].append(copy.deepcopy(self.fixture.basic))
        after = acquisition.navigation_progress(self.task)
        self.assertEqual(len(after[-1] - before[-1]), 1)
        received = _metadata_records(self.task, 'basic_data_service')
        self.assertEqual(len(received), 1)
        self.assertFalse(received[0]['parsed']['units_verified'])
        self.assertFalse(received[0]['parsed']['financial_values_approved'])

    def test_unchanged_received_response_does_not_keep_navigation_alive(self):
        self.task['source_records'].append(copy.deepcopy(self.fixture.basic))
        before = acquisition.navigation_progress(self.task)
        self.assertEqual(acquisition.navigation_progress(self.task), before)
        self.assertFalse(any(new - old for old, new in zip(before, acquisition.navigation_progress(self.task))))

    def test_unbound_foreign_or_modified_response_is_not_progress(self):
        for mutation in ('acquisition', 'task', 'version', 'snapshot'):
            record = copy.deepcopy(self.fixture.basic)
            if mutation == 'acquisition': record.pop('acquisition')
            elif mutation == 'task': record['task_id'] = 'foreign'
            elif mutation == 'version': record['research_version'] += 1
            else: record['snapshot']['errorcode'] = -1
            self.task['source_records'] = [copy.deepcopy(self.fixture.policy), record]
            self.assertEqual(acquisition.navigation_progress(self.task)[-1], set(), mutation)

    def test_legacy_tasks_keep_progress_dimensions_and_budget(self):
        ledger = copy.deepcopy(self.task['ledger'])
        self.task.pop(POLICY)
        old = acquisition.navigation_progress(self.task)
        self.task[POLICY] = 1
        self.assertEqual(acquisition.navigation_progress(self.task)[:-1], old)
        self.assertEqual(self.task['ledger'], ledger)

    def test_unknown_and_boolean_policy_versions_are_rejected(self):
        for value in (True, 2, '1', 1.0):
            self.task[POLICY] = value
            with self.assertRaises(ContractError): acquisition.navigation_progress(self.task)

    def test_new_policy_is_bound_to_the_task_input(self):
        self.task.pop(POLICY)
        before = input_fingerprint(self.task)
        self.task[POLICY] = 1
        self.assertNotEqual(input_fingerprint(self.task), before)
        self.task.pop(POLICY)
        self.assertEqual(input_fingerprint(self.task), before)


if __name__ == '__main__':
    unittest.main()
