"""No-network checks for the new, one-shot research boundary."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests import review_api_value_authorized_ifind_research as entry


class ValueLiveEntryTests(unittest.TestCase):
    def test_missing_confirmation_never_dispatches(self):
        with patch.object(entry.direct, "run") as dispatch:
            with self.assertRaises(ValueError):
                entry.run()
            dispatch.assert_not_called()

    def test_spent_marker_never_replays(self):
        with TemporaryDirectory() as folder:
            output = Path(folder)
            (output / "authorization-dispatch.json").touch()
            with patch.object(entry, "OUTPUT", output), patch.object(entry.direct, "run") as dispatch:
                with self.assertRaises(ValueError):
                    entry.run(confirmed=True)
                dispatch.assert_not_called()

    def test_release_binding_is_scoped_and_restored(self):
        previous = entry.direct.OUTPUT, entry.direct.RELEASE
        observed = {}

        def execute(*, confirmed):
            observed.update(confirmed=confirmed, output=entry.direct.OUTPUT, release=entry.direct.RELEASE)
            return {"fixture": True}

        with TemporaryDirectory() as folder, patch.object(entry, "OUTPUT", Path(folder)):
            with patch.object(entry.direct, "run", side_effect=execute):
                self.assertEqual(entry.run(confirmed=True), {"fixture": True})
            self.assertEqual(observed, {"confirmed": True, "output": Path(folder), "release": entry.RELEASE})
        self.assertEqual((entry.direct.OUTPUT, entry.direct.RELEASE), previous)
