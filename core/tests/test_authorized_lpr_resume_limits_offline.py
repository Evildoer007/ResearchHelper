"""Exercise the fresh authorization gate without network or real settings."""
import time
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError
from researchhelper.model import IsolatedModelService
from tests.review_authorized_lpr_resume import AuthorizedResumeModel


class ResumeAuthorizationLimitsTests(unittest.TestCase):
    def test_call_cap_rejects_before_accounting_or_transport(self):
        model = AuthorizedResumeModel(None)
        model.started = time.monotonic()
        with patch.object(IsolatedModelService, 'reason_phase', return_value={}) as dispatch:
            for _ in range(3):
                model.reason_phase({}, [], 'analyze')
            with self.assertRaises(ContractError):
                model.reason_phase({}, [], 'analyze')
        self.assertEqual(dispatch.call_count, 3)

    def test_unstarted_expired_and_nonanalysis_never_dispatch(self):
        model = AuthorizedResumeModel(None)
        with patch.object(IsolatedModelService, 'reason_phase') as dispatch:
            with self.assertRaises(ContractError):
                model.reason_phase({}, [], 'analyze')
            model.started = time.monotonic() - 181
            with self.assertRaises(ContractError):
                model.reason_phase({}, [], 'analyze')
            model.started = time.monotonic()
            with self.assertRaises(ContractError):
                model.reason_phase({}, [], 'supplement')
        dispatch.assert_not_called()
        self.assertEqual(model.calls, 0)

    def test_transport_timeout_never_exceeds_remaining_authorization(self):
        model = AuthorizedResumeModel(None)
        model.started = time.monotonic() - 100
        record = {}
        with patch.object(IsolatedModelService, '_request_bytes', return_value=b'{}') as dispatch:
            model._request_bytes({}, None, time.monotonic(), 120, record)
        self.assertGreater(record['timeout_seconds'], 0)
        self.assertLessEqual(record['timeout_seconds'], 80)
        self.assertEqual(dispatch.call_args.args[3], record['timeout_seconds'])
