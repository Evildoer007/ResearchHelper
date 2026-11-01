"""Verify the real-study boundary without reading private config or dispatching."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests import review_analysis_execution_live_research as entry


class AnalysisExecutionLiveEntryTests(unittest.TestCase):
    def test_no_confirmation_no_dispatch(self):
        with patch.object(entry.direct, 'run') as dispatch:
            with self.assertRaises(ValueError):
                entry.run()
            dispatch.assert_not_called()

    def test_spent_marker_does_not_repeat(self):
        with TemporaryDirectory() as folder:
            output = Path(folder)
            (output / 'authorization-dispatch.json').touch()
            with patch.object(entry, 'OUTPUT', output), patch.object(entry.direct, 'run') as dispatch:
                with self.assertRaises(ValueError):
                    entry.run(confirmed=True)
                dispatch.assert_not_called()

    def test_current_release_and_isolated_output_are_restored_after_error(self):
        original = entry.direct.OUTPUT, entry.direct.RELEASE
        observed = {}

        def failed(*, confirmed):
            observed.update(confirmed=confirmed, output=entry.direct.OUTPUT, release=entry.direct.RELEASE)
            raise RuntimeError('fabricated failure before credentials or requests')

        with TemporaryDirectory() as folder, patch.object(entry, 'OUTPUT', Path(folder)):
            with patch.object(entry.direct, 'run', side_effect=failed):
                with self.assertRaises(RuntimeError):
                    entry.run(confirmed=True)
            self.assertEqual(observed, {'confirmed': True, 'output': Path(folder), 'release': entry.RELEASE})
        self.assertEqual((entry.direct.OUTPUT, entry.direct.RELEASE), original)
