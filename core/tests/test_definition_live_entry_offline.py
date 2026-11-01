"""Entry authorization and spent-marker checks perform no preflight or calls."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests import review_definition_live_research as entry


class DefinitionLiveEntryTests(unittest.TestCase):
    def test_unconfirmed_entry_does_not_read_credentials_or_gate(self):
        with patch.object(entry, 'source_candidate') as gate, patch.object(entry.direct, 'run') as run:
            with self.assertRaises(ValueError):
                entry.run()
        gate.assert_not_called(); run.assert_not_called()

    def test_existing_marker_does_not_repeat_or_reset_a_run(self):
        with tempfile.TemporaryDirectory(dir=entry.ROOT / 'result/acceptance') as folder:
            output = Path(folder)
            marker = output / 'authorization-dispatch.json'
            marker.touch()
            with patch.object(entry, 'OUTPUT', output), patch.object(entry, 'source_candidate') as gate, \
                    patch.object(entry.direct, 'run') as run:
                with self.assertRaises(ValueError):
                    entry.run(confirmed=True)
            gate.assert_not_called(); run.assert_not_called()
            self.assertTrue(marker.exists())


if __name__ == '__main__':
    unittest.main()
