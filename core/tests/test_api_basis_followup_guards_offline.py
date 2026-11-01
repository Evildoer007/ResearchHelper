"""Post-publication ceiling checks without files, credentials or networking."""

import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError
from tests import review_api_basis_real_followup as entry


class FollowupCeilingTests(unittest.TestCase):
    def test_no_confirmation_does_not_read_or_dispatch(self):
        with patch.object(entry.direct, "run") as dispatch, patch.object(entry.Path, "read_text") as read:
            with self.assertRaises(ValueError):
                entry.run()
            dispatch.assert_not_called()
            read.assert_not_called()

    def test_two_model_calls_stop_before_transport(self):
        model = object.__new__(entry.RemainingModel)
        model.calls = 2
        with patch.object(entry.OriginalModel, "reason_phase") as transport:
            with self.assertRaises(ContractError):
                model.reason_phase({}, [], "extract")
            transport.assert_not_called()

    def test_two_data_calls_stop_before_transport(self):
        collector = object.__new__(entry.RemainingCollector)
        collector.http_operations = {"first", "second"}
        with patch.object(entry.OriginalCollector, "_http_json") as transport:
            with self.assertRaises(ContractError):
                collector._http_json({}, None, 30, {})
            transport.assert_not_called()
