"""Authorization is ongoing; preflight stays credential-free and one-shot."""

import json
import unittest
from unittest.mock import MagicMock, patch

from tests import review_query_live_research as entry


class QueryLiveEntryTests(unittest.TestCase):
    def test_source_gate_is_readonly_and_does_not_read_credentials(self):
        gate = json.loads(entry.GATE.read_text())
        unchanged = all(entry.direct.executor.digest(entry.ROOT / name) == value for name, value in gate['source_hashes'].items())
        with patch.object(entry.direct.executor, 'owned_credentials', side_effect=AssertionError('No credentials')):
            if unchanged:
                value = entry.source_candidate()
            else:
                # A later source fix must invalidate the old dispatch gate.
                # Check the closed run's original limits without approving it
                # for another run or changing its marker/hash expectations.
                with self.assertRaises(AssertionError): entry.source_candidate()
                value = json.loads((entry.OUTPUT / 'source-candidate.json').read_text())
        self.assertEqual(value['maximum_data_requests'], 4)
        self.assertEqual(value['maximum_model_calls'], 4)
        self.assertEqual(value['maximum_seconds'], 300)
        self.assertIn('researchhelper/provenance.py', value['source_hashes'])
        self.assertFalse(value['full_harness_complete'])

    def test_closed_marker_cannot_dispatch_again(self):
        output = MagicMock()
        (output / 'authorization-dispatch.json').exists.return_value = True
        with patch.object(entry, 'OUTPUT', output), patch.object(entry, 'source_candidate') as gate, \
                patch.object(entry.direct, 'run') as dispatch, self.assertRaises(ValueError):
            entry.run()
        gate.assert_not_called(); dispatch.assert_not_called()

    def test_changed_source_hash_cannot_read_or_dispatch_credentials(self):
        with patch.object(entry.direct.executor, 'digest', return_value='wrong'), \
                patch.object(entry.direct.executor, 'owned_credentials') as credentials, self.assertRaises(AssertionError):
            entry.source_candidate()
        credentials.assert_not_called()
