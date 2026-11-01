"""Exact byte parsing can be reused; approvals and mutable results cannot."""
import copy
import socket
import unittest
from unittest.mock import patch

from researchhelper import official_sources as sources
from researchhelper.contracts import fingerprint

PAYLOAD = ('<a href="/sj/zxfb/202609/t20260930_1234.html">九月发布</a>'
           '<a href="/sj/zxfb/202610/t20261031_1235.html">十月发布</a>').encode()


class OfficialCatalogueParseCacheTests(unittest.TestCase):
    def setUp(self):
        sources._cached_release_links.cache_clear()
        self.addCleanup(sources._cached_release_links.cache_clear)
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline parsing only'))
        guard.start()
        self.addCleanup(guard.stop)

    def test_same_bytes_reuse_syntax_but_cutoff_is_rechecked(self):
        with patch.object(sources.ReleaseLinks, 'feed', autospec=True,
                          side_effect=sources.ReleaseLinks.feed) as parse:
            early = sources.parse_catalogue(PAYLOAD, '2026-10-01')
            late = sources.parse_catalogue(PAYLOAD, '2026-11-01')
        self.assertEqual(parse.call_count, 1)
        self.assertEqual(len(early['entries']), 1)
        self.assertEqual(len(late['entries']), 2)
        self.assertEqual(early['coverage']['skipped_future'], 1)
        self.assertEqual(late['coverage']['skipped_future'], 0)

    def test_changed_bytes_do_not_reuse_previous_links_or_hash(self):
        first = sources.parse_catalogue(PAYLOAD, '2026-11-01')
        second = sources.parse_catalogue(PAYLOAD.replace('九月'.encode(), '新版九月'.encode()), '2026-11-01')
        self.assertNotEqual(first['original_sha256'], second['original_sha256'])
        self.assertNotEqual(first['entries'][0]['title'], second['entries'][0]['title'])
        self.assertEqual(sources._cached_release_links.cache_info().misses, 2)

    def test_returned_rows_and_coverage_are_not_shared_mutable_cache_entries(self):
        original = sources.parse_catalogue(PAYLOAD, '2026-11-01')
        changed = copy.deepcopy(original)
        original['entries'][0]['title'] = 'tampered'
        original['coverage']['completeness'] = 'tampered'
        self.assertEqual(sources.parse_catalogue(PAYLOAD, '2026-11-01'), changed)

    def test_registered_channels_keep_separate_syntax_and_permissions(self):
        payload = PAYLOAD + b'<a href="https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm">Statement</a>'
        nbs = sources.parse_catalogue(payload, '2026-11-01')
        fed = sources.parse_catalogue(payload, '2026-11-01', channel='fed_fomc')
        self.assertEqual(len(nbs['entries']), 2)
        self.assertEqual(len(fed['entries']), 1)
        self.assertTrue(fed['entries'][0]['id'].startswith('fed_fomc_'))
        self.assertEqual(sources._cached_release_links.cache_info().misses, 2)

    def test_cache_does_not_accept_changed_snapshot_or_task_identity(self):
        snapshot = sources.parse_catalogue(PAYLOAD, '2026-10-17')
        parameters = dict(channel='nbs_release', page='0')
        record = dict(id='retained_catalogue', task_id='cache_fixture', research_version=1,
            source='official', operation='release_catalog', status='成功待核验', parameters=parameters,
            snapshot=snapshot, hash=fingerprint(snapshot),
            request_hash=fingerprint(dict(source='official', operation='release_catalog', parameters=parameters)))
        task = dict(id='cache_fixture', version=1, cutoff='2026-10-17', official_channels_version=1,
                    source_records=[record])
        self.assertEqual(len(sources.release_choices(task)), 1)
        for mutation in ('snapshot', 'task'):
            altered = copy.deepcopy(task)
            if mutation == 'snapshot':
                altered['source_records'][0]['snapshot']['entries'][0]['title'] = 'tampered'
            else:
                altered['id'] = 'other_task'
            with self.subTest(mutation=mutation):
                self.assertEqual(sources.release_choices(altered), [])

    def test_cache_is_bounded_and_larger_payloads_are_not_retained(self):
        for index in range(6):
            sources.parse_catalogue(PAYLOAD + str(index).encode(), '2026-11-01')
        self.assertEqual(sources._cached_release_links.cache_info().currsize, 4)
        before = sources._cached_release_links.cache_info()
        with patch.object(sources, 'MAX_BYTES', len(PAYLOAD) - 1):
            sources.parse_catalogue(PAYLOAD, '2026-11-01')
        self.assertEqual(sources._cached_release_links.cache_info(), before)
