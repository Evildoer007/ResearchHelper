"""No private reads or real requests in boundary verification."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tests import review_calculation_input_live_research as entry


class CalculationInputLiveEntryTests(unittest.TestCase):
    def test_unconfirmed_never_preflights_or_dispatches(self):
        with patch.object(entry, 'source_candidate') as candidate, patch.object(entry.direct, 'run') as dispatch:
            with self.assertRaises(ValueError):
                entry.run()
            candidate.assert_not_called()
            dispatch.assert_not_called()

    def test_spent_marker_keeps_previous_budget(self):
        with TemporaryDirectory() as folder:
            output = Path(folder)
            (output / 'authorization-dispatch.json').touch()
            with patch.object(entry, 'OUTPUT', output), patch.object(entry, 'source_candidate') as candidate:
                with self.assertRaises(ValueError):
                    entry.run(confirmed=True)
                candidate.assert_not_called()

    def test_dispatch_has_explicit_candidate_binding_and_restores_routes(self):
        before = entry.direct.OUTPUT, entry.direct.RELEASE
        observed = {}

        def execute(*, confirmed):
            observed.update(confirmed=confirmed, output=entry.direct.OUTPUT, release=entry.direct.RELEASE)
            return {'fixture': True}

        with TemporaryDirectory() as folder, patch.object(entry, 'OUTPUT', Path(folder)), \
                patch.object(entry, 'source_candidate', return_value={'source_hashes': {}}), \
                patch.object(entry.direct, 'run', side_effect=execute):
            self.assertEqual(entry.run(confirmed=True), {'fixture': True})
            self.assertEqual(observed, {'confirmed': True, 'output': Path(folder),
                                       'release': Path(folder) / 'source-candidate.json'})
        self.assertEqual((entry.direct.OUTPUT, entry.direct.RELEASE), before)
