"""Replay retained membership and closing price with controlled dispatch receipts."""
import copy
import tempfile
import unittest

from tests import ROOT
from tests.accept_index_members_data_phase import acquisition_inventory
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from tests.market_observation_fixtures import INDEX_CAPTURES, append_index_member_replay, read_captures


class IndexDataFollowupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = INDEX_CAPTURES.read_bytes()
        cls.samples = read_captures(cls.original, grouped=True)

    @classmethod
    def tearDownClass(cls):
        if INDEX_CAPTURES.read_bytes() != cls.original:
            raise AssertionError('Closed study changed')

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True, require_argument_support=True)
        self.addCleanup(agent.close_runtime)
        self.task = agent.create('核对上证50在2026年10月16日的成分名单及样本当日收盘价。',
            entry='指数', research_mode='current')
        append_index_member_replay(self.task, self.samples)

    def test_name_only_index_replay_has_a_matching_member_quote(self):
        result = acquisition_inventory(self.task)
        self.assertTrue(result['autonomous_member_followup_observed'])
        self.assertEqual(result['pools'][0]['rows'], 50)
        self.assertFalse(result['pools'][0]['complete_set_verified'])
        self.assertEqual(result['quote_sources'][0]['parameters']['codes'], '600519.SH')
        self.assertTrue(all(row['snapshot_hash_valid'] for row in result['source_checks']))
        self.assertEqual(self.task['evidence'], [])

    def test_missing_pool_is_not_a_member_followup(self):
        task = copy.deepcopy(self.task)
        task['source_records'] = [row for row in task['source_records'] if row['operation'] != 'data_pool']
        self.assertFalse(acquisition_inventory(task)['autonomous_member_followup_observed'])

    def test_missing_or_wrong_version_acquisition_receipt_is_not_linkage(self):
        for changes in ({'acquisition': None}, {'research_version': 999}):
            task = copy.deepcopy(self.task)
            task['source_records'][1].update(changes)
            with self.subTest(changes=changes):
                self.assertFalse(acquisition_inventory(task)['autonomous_member_followup_observed'])

    def test_tampered_code_date_or_empty_values_do_not_pass(self):
        for change in ('code', 'date', 'value'):
            task = copy.deepcopy(self.task)
            quote = task['source_records'][1]
            if change == 'code':
                quote['parameters']['codes'] = '000001.SZ'
            elif change == 'date':
                quote['parameters']['startdate'] = '2026-10-15'
            else:
                quote['snapshot']['tables'][0]['table']['close'] = []
            with self.subTest(change=change):
                self.assertFalse(acquisition_inventory(task)['autonomous_member_followup_observed'])

    def test_reordering_cannot_make_a_future_list_the_source_of_an_earlier_quote(self):
        task = copy.deepcopy(self.task)
        # Response receipt indices must still agree with their actual events.
        first, second = [row['acquisition']['dispatch_index'] for row in task['source_records']]
        events = task['ledger']['dispatches']
        events[first], events[second] = events[second], events[first]
        task['source_records'][0]['acquisition']['dispatch_index'] = second
        task['source_records'][1]['acquisition']['dispatch_index'] = first
        self.assertFalse(acquisition_inventory(task)['autonomous_member_followup_observed'])
