"""Static batch checks must preserve verified identity-dependent acquisition."""
import copy
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError
from researchhelper.evidence import available_security_codes, snapshot_record
from tests import test_identity_resolution_offline as identity


class DependentRequestPreflightTests(unittest.TestCase):
    def setUp(self):
        self.fixture = identity.IdentityResolutionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.agent = self.fixture.agent
        self.task = self.fixture.task
        guard = patch('socket.socket.connect', side_effect=AssertionError('Offline test forbids live calls'))
        guard.start()
        self.addCleanup(guard.stop)

    def needs(self):
        return [self.fixture.need('get_thscode', self.fixture.params()),
                self.fixture.quote(), self.fixture.catalogue()]

    def run_host(self, responses, needs=None):
        with patch.object(self.agent.collector, '_http_json', side_effect=responses) as transport:
            final = self.agent.start(self.task['id'], {'data_requests': needs or self.needs()},
                                     defer_delivery=True)
        return final, transport.call_count

    def test_identity_then_quote_and_catalogue_keeps_all_three_actual_requests(self):
        final, count = self.run_host([self.fixture.response(),
            {'errorcode': 0, 'tables': [{'thscode': '600000.SH', 'time': ['2026-09-30'],
                                        'table': {'close': [10.0]}}]},
            {'errorcode': 0, 'tables': []}])
        self.assertEqual(count, 3)
        self.assertEqual(available_security_codes(final), ['600000.SH'])
        self.assertEqual(final['objects'], self.task['objects'])
        self.assertEqual(final['ledger']['used']['data_requests'], 3)
        self.assertEqual(final['evidence'], [])

    def test_invalid_later_parameters_stop_even_the_identity_request(self):
        needs = self.needs()
        needs[1]['parameters']['indicators'] = 'not_registered'
        before = copy.deepcopy(self.agent.archive.get(self.task['id']))
        with patch.object(self.agent.collector, '_http_json') as transport:
            with self.assertRaises(ContractError):
                self.agent.start(self.task['id'], {'data_requests': needs})
        transport.assert_not_called()
        self.assertEqual(self.agent.archive.get(self.task['id']), before)

    def test_future_date_is_not_hidden_behind_pending_identity(self):
        needs = self.needs()
        needs[2]['parameters']['endrDate'] = '2026-10-02'
        with patch.object(self.agent.collector, '_http_json') as transport:
            with self.assertRaises(ContractError):
                self.agent.start(self.task['id'], {'data_requests': needs})
        transport.assert_not_called()

    def test_quote_before_identity_is_not_implicitly_reordered(self):
        needs = self.needs()
        with patch.object(self.agent.collector, '_http_json') as transport:
            with self.assertRaises(ContractError):
                self.agent.start(self.task['id'], {'data_requests': [needs[1], needs[0]]})
        transport.assert_not_called()

    def test_ambiguous_identity_does_not_send_quote_or_catalogue(self):
        final, count = self.run_host([self.fixture.response(codes=['600000.SH', '600000.SZ'])])
        self.assertEqual(count, 1)
        self.assertEqual(available_security_codes(final), [])
        self.assertEqual([r['operation'] for r in final['source_records']], ['get_thscode'])
        self.assertEqual(final['ledger']['used']['data_requests'], 1)
        self.assertTrue(final['gaps'])

    def test_mapping_to_another_code_does_not_authorize_requested_quote(self):
        final, count = self.run_host([self.fixture.response(codes=['600001.SH'])])
        self.assertEqual(count, 1)
        self.assertEqual([r['operation'] for r in final['source_records']], ['get_thscode'])
        self.assertEqual(final['ledger']['used']['data_requests'], 1)

    def test_business_failure_does_not_authorize_followup_requests(self):
        final, count = self.run_host([dict(self.fixture.response(), errorcode=-4102)])
        self.assertEqual(count, 1)
        self.assertEqual(available_security_codes(final), [])
        self.assertEqual(final['ledger']['used']['data_requests'], 1)

    def test_unverified_mapping_cannot_reuse_or_alias_an_imported_quote(self):
        quote = self.fixture.quote()
        raw = snapshot_record(self.task, 'ifind', quote['operation'], quote['parameters'],
            {'errorcode': 0, 'tables': [{'thscode': '600000.SH', 'time': ['2026-09-30'],
                                        'table': {'close': [10.0]}}]})
        raw.update(data_need_ids=['old_import'], question_ids=['q1'])
        self.task['source_records'].append(raw)
        self.agent.archive.save(self.task)
        before_aliases = copy.deepcopy(raw['data_need_ids'])
        final, count = self.run_host([self.fixture.response(codes=['600000.SH', '600000.SZ'])])
        self.assertEqual(count, 1)
        retained = next(row for row in final['source_records'] if row['id'] == raw['id'])
        self.assertEqual(retained['data_need_ids'], before_aliases)
        self.assertEqual(final['ledger']['used']['data_requests'], 1)
        self.assertEqual(final['evidence'], [])
