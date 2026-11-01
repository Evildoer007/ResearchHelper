"""The monitoring adaptation must not rewrite research inputs or final reads."""
import unittest

from tests.workspace_monitor_fixture import ORIGINAL, POLL, VIEW_POLL, browser_source


class WorkspaceMonitorFixtureTests(unittest.TestCase):
    def test_only_one_monitoring_line_changes(self):
        source = ORIGINAL.read_text()
        actual = browser_source(source)
        self.assertEqual(actual.replace(VIEW_POLL, POLL), source)
        self.assertNotEqual(source, actual)

    def test_final_canonical_read_and_all_original_checks_stay_whole(self):
        source = ORIGINAL.read_text()
        actual = browser_source(source)
        final = "task=await (await context.request.get(route,{timeout:15000})).json();"
        self.assertEqual(source.count(final), actual.count(final))
        self.assertGreater(source.count(final), 0)
        self.assertEqual(source.count('check('), actual.count('check('))
        self.assertEqual(source.count('assert.'), actual.count('assert.'))

    def test_no_fallback_when_the_target_is_missing_or_ambiguous(self):
        for source in ('', POLL + POLL):
            with self.assertRaises(ValueError):
                browser_source(source)
