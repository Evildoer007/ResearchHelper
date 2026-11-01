"""Authorization/transport preflight only; never invokes the live runner."""
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.request import Request

from researchhelper.contracts import ContractError
from researchhelper.model import IsolatedModelService
from tests import review_authorized_ifind_etf as probe
from tests.review_authorized_source_permissions import AuthorizedSourceCollector
from researchhelper.source_transport import IsolatedEvidenceCollector


class AuthorizedIFindETFTests(unittest.TestCase):
    def test_no_literal_approval_never_reads_credentials_or_creates_files(self):
        for confirmed in (False, None, 1, 'yes'):
            with self.subTest(confirmed=confirmed), patch.object(probe, 'owned_credentials') as read, \
                    patch.object(Path, 'mkdir') as create:
                with self.assertRaises(ValueError):
                    probe.run(confirmed=confirmed)
                read.assert_not_called()
                create.assert_not_called()

    def test_consumed_authorization_cannot_read_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'authorization-dispatch.json').touch()
            with patch.object(probe, 'OUTPUT', root), patch.object(probe, 'owned_credentials') as read:
                with self.assertRaises(ValueError):
                    probe.run(confirmed=True)
                read.assert_not_called()

    def test_model_cap_blocks_before_fifth_dispatch(self):
        model = probe.AuthorizedETFModel(None, deadline=time.monotonic() + 300)
        with patch.object(IsolatedModelService, 'reason_phase', return_value={}) as dispatch:
            for index in range(4):
                model.reason_phase({}, [{'content': str(index)}], 'analyze')
            with self.assertRaises(ContractError):
                model.reason_phase({}, [{'content': 'fifth'}], 'analyze')
        self.assertEqual(dispatch.call_count, 4)

    def test_same_model_input_is_not_automatically_retried(self):
        model = probe.AuthorizedETFModel(None, deadline=time.monotonic() + 300)
        with patch.object(IsolatedModelService, 'reason_phase', side_effect=OSError) as dispatch:
            with self.assertRaises(OSError):
                model.reason_phase({}, [], 'analyze')
            with self.assertRaises(ContractError):
                model.reason_phase({}, [], 'analyze')
        self.assertEqual(dispatch.call_count, 1)

    def test_expired_scope_blocks_model_before_dispatch(self):
        model = probe.AuthorizedETFModel(None, deadline=time.monotonic() - 1)
        with patch.object(IsolatedModelService, 'reason_phase') as dispatch:
            with self.assertRaises(ContractError):
                model.reason_phase({}, [], 'plan')
        dispatch.assert_not_called()

    def test_transport_wait_is_bounded_by_original_deadline(self):
        model = probe.AuthorizedETFModel(None, deadline=time.monotonic() + 20)
        record = {}
        with patch.object(IsolatedModelService, '_request_bytes', return_value=b'{}') as dispatch:
            model._request_bytes({}, None, time.monotonic(), 120, record)
        self.assertGreater(record['timeout_seconds'], 0)
        self.assertLessEqual(dispatch.call_args.args[3], 20)

    def test_unapproved_sources_never_dispatch(self):
        collector = probe.AuthorizedETFCollector(time.monotonic() + 300, offline=False)
        with patch.object(AuthorizedSourceCollector, 'external') as dispatch:
            for source in ('tinyshare', 'wind', 'other'):
                with self.subTest(source=source), self.assertRaises(ContractError):
                    collector.external({}, source, 'anything', {})
        dispatch.assert_not_called()

    def test_expired_scope_blocks_data_before_dispatch(self):
        collector = probe.AuthorizedETFCollector(time.monotonic() - 1, offline=False)
        with patch.object(AuthorizedSourceCollector, 'external') as dispatch:
            with self.assertRaises(ContractError):
                collector.external({}, 'ifind', 'anything', {})
        dispatch.assert_not_called()

    def test_different_query_dates_are_not_misclassified_as_retries(self):
        collector = probe.AuthorizedETFCollector(time.monotonic() + 300, offline=False)
        endpoint = 'https://quantapi.51ifind.com/api/v1/cmd_history_quotation'
        with patch.object(IsolatedEvidenceCollector, '_http_json', return_value={}) as dispatch:
            for date in ('2026-09-29', '2026-09-30'):
                collector._http_json({}, Request(endpoint, data=date.encode()), 30, {})
        self.assertEqual(dispatch.call_count, 2)

    def test_failed_http_request_is_not_retried(self):
        collector = probe.AuthorizedETFCollector(time.monotonic() + 300, offline=False)
        request = Request('https://quantapi.51ifind.com/api/v1/cmd_history_quotation', data=b'{}')
        with patch.object(IsolatedEvidenceCollector, '_http_json', side_effect=OSError) as dispatch:
            for _ in range(2):
                with self.assertRaises(ContractError):
                    collector._http_json({}, request, 30, {})
        self.assertEqual(dispatch.call_count, 1)
