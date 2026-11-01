"""Preflight the new entry without credentials, network, or dispatch."""

import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError
from tests import review_authorized_ifind_direct_extraction as entry


class DirectExtractionEntryTests(unittest.TestCase):
    def test_no_confirmation_cannot_read_credentials_or_dispatch(self):
        with patch.object(entry.executor, "run") as dispatch:
            with self.assertRaises(ValueError):
                entry.run()
            dispatch.assert_not_called()

    def test_mode_override_is_current_memory_only(self):
        config = {"endpoint": "https://api.deepseek.com/chat/completions",
                  "model": "deepseek-flash", "api_key": "fixture-not-real"}
        credentials = {"access_token": "fixture-not-real"}
        actual, sources = entry.direct_credentials(lambda: (config, credentials))
        self.assertEqual(actual["extraction_mode"], "direct")
        self.assertIs(actual, config)
        self.assertIs(sources, credentials)

    def test_unsupported_configuration_clears_sensitive_memory(self):
        config = {"endpoint": "https://example.invalid/chat/completions",
                  "model": "other", "api_key": "fixture-not-real"}
        credentials = {"access_token": "fixture-not-real"}
        with self.assertRaises(ContractError):
            entry.direct_credentials(lambda: (config, credentials))
        self.assertEqual(config, {})
        self.assertEqual(credentials, {})
