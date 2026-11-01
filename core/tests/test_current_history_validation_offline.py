"""Authorization checks do not access credentials, providers or a model."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests import rebuild_current_history_validation as validation


class CurrentHistoryValidationTests(unittest.TestCase):
    def test_explicit_confirmation_required(self):
        with patch.object(validation.probe, "run") as dispatch:
            for value in (False, None, 1, "yes"):
                with self.assertRaises(ValueError):
                    validation.run(confirmed=value)
            dispatch.assert_not_called()

    def test_consumed_authorization_cannot_run_again(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "authorization-dispatch.json").touch()
            with patch.object(validation, "OUTPUT", output), patch.object(validation.probe, "run") as dispatch:
                with self.assertRaises(ValueError):
                    validation.run(confirmed=True)
                dispatch.assert_not_called()

    def test_wrong_contract_does_not_dispatch(self):
        with patch.object(validation, "SUPPLEMENT_RULES_VERSION", 3), patch.object(validation.probe, "run") as dispatch:
            with self.assertRaises(ValueError):
                validation.run(confirmed=True)
            dispatch.assert_not_called()

    def test_isolated_context_restored_after_dispatch_failure(self):
        original_output, original_authorization = validation.probe.OUTPUT, validation.probe.AUTHORIZATION
        with tempfile.TemporaryDirectory() as directory:
            output, previous = Path(directory) / "new", Path(directory) / "previous"
            previous.mkdir()
            (previous.parent / "authorization-dispatch.json").touch()
            (previous / "receipt.json").write_text("{}")

            def fail(*, confirmed):
                self.assertIs(confirmed, True)
                self.assertEqual(validation.probe.OUTPUT, output)
                self.assertEqual(validation.probe.AUTHORIZATION, validation.AUTHORIZATION)
                raise RuntimeError("isolated failure")

            with patch.object(validation, "OUTPUT", output), patch.object(validation, "PREVIOUS", previous):
                with patch.object(validation.probe, "run", side_effect=fail):
                    with self.assertRaises(RuntimeError):
                        validation.run(confirmed=True)
        self.assertEqual(validation.probe.OUTPUT, original_output)
        self.assertEqual(validation.probe.AUTHORIZATION, original_authorization)
