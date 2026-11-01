"""Independent local authorization gates; no supplier or model requests."""
import time
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request

from researchhelper.contracts import ContractError
from researchhelper.transport import TransportFailure
from tests.review_authorized_source_permissions import (
    AuthorizedSourceCollector, NoQueryRetryConnection, NoRedirect, run,
    complete_unsent_preflight, workers_settled)


class AuthorizedSourcePermissionTests(unittest.TestCase):
    def test_requires_explicit_authorization_before_files_or_calls(self):
        for value in (False, None, 1, 'yes'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                run(confirmed=value)

    def test_deadline_blocks_before_transport(self):
        collector = AuthorizedSourceCollector(time.monotonic() - 1, offline=False)
        with patch('researchhelper.source_transport.run_isolated') as transport, self.assertRaises(ContractError):
            collector._request({}, {}, 30, {})
        transport.assert_not_called()

    def test_http_failure_never_enters_inherited_retry(self):
        collector = AuthorizedSourceCollector(time.monotonic() + 120, offline=False)
        request = Request('https://quantapi.51ifind.com/api/v1/cmd_history_quotation', data=b'{}')
        with patch('researchhelper.source_transport.IsolatedEvidenceCollector._http_json', side_effect=OSError) as transport:
            for _ in range(2):
                with self.assertRaises(ContractError):
                    collector._http_json({}, request, 30, {})
            self.assertEqual(transport.call_count, 1)

    def test_sdk_repeat_query_blocked_but_download_metering_retained(self):
        underlying = Mock()
        connection = NoQueryRetryConnection(underlying)
        connection.send({'kind': 'authorize', 'index': 1, 'role': 'query'})
        connection.send({'kind': 'authorize', 'index': 2, 'role': 'download'})
        with self.assertRaises(TransportFailure):
            connection.send({'kind': 'authorize', 'index': 3, 'role': 'query'})
        self.assertEqual(underlying.send.call_count, 2)

    def test_http_redirect_never_dispatches_another_request(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.invalid'))

    def test_preflight_continuation_requires_authorization_before_read(self):
        with self.assertRaises(ValueError):
            complete_unsent_preflight()

    def test_cleanup_requires_actual_network_fields_not_unknown_names(self):
        event = {'network_worker_pid': 1, 'source_worker_reaped': True}
        self.assertFalse(workers_settled([event]))
        event.update(network_worker_reaped=True, transport_sender_stopped=True, transport_receiver_stopped=True)
        self.assertTrue(workers_settled([event]))

    def test_current_quote_parameters_pass_product_preflight_without_network(self):
        from researchhelper.evidence import validate_source_request
        from researchhelper.contracts import now
        parameters = {'codes': '510300.SH', 'indicators': 'close', 'startdate': '2026-09-29',
            'enddate': '2026-09-30', 'functionpara': {'Currency': 'RMB', 'CPS': '1', 'Fill': 'Omit'}}
        task = {'cutoff': now(), 'objects': ['510300.SH']}
        self.assertEqual(validate_source_request(task, 'ifind', 'cmd_history_quotation', parameters), parameters)
