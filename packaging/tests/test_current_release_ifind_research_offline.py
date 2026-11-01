"""Independent entry checks; no model, supplier or private settings access."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests import review_current_release_ifind_research as entry


class CurrentReleaseResearchTests(unittest.TestCase):
    def test_no_authorization_does_not_dispatch(self):
        with patch.object(entry.direct, "run") as dispatch:
            with self.assertRaises(ValueError):
                entry.run()
            dispatch.assert_not_called()

    def test_existing_dispatch_is_never_replayed(self):
        with TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "authorization-dispatch.json").touch()
            with patch.object(entry, "OUTPUT", output), patch.object(entry.direct, "run") as dispatch:
                with self.assertRaises(ValueError):
                    entry.run(confirmed=True)
                dispatch.assert_not_called()

    def test_current_release_binding_is_scoped_and_restored(self):
        original_output = entry.direct.OUTPUT
        original_release = entry.direct.RELEASE
        observed = {}

        def dispatch(*, confirmed):
            observed.update(confirmed=confirmed, output=entry.direct.OUTPUT, release=entry.direct.RELEASE)
            return {"fixture": True}

        with TemporaryDirectory() as directory, patch.object(entry, "OUTPUT", Path(directory)):
            with patch.object(entry.direct, "run", side_effect=dispatch):
                self.assertEqual(entry.run(confirmed=True), {"fixture": True})
            self.assertEqual(observed, {"confirmed": True, "output": Path(directory), "release": entry.RELEASE})
        self.assertEqual(entry.direct.OUTPUT, original_output)
        self.assertEqual(entry.direct.RELEASE, original_release)

