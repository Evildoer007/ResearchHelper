"""Fixed FOMC navigation without credentials, arbitrary URLs or approval."""
import copy
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.evidence import EvidenceCollector, data_request_contract
from researchhelper.official_sources import (article_url, catalogue_url, parse_catalogue,
    release_choices, validate_request, validate_original)
from researchhelper.output_schema import _planned_request_schemas, _matches
from researchhelper.reasoning import extractable_source_views

URL = 'https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm'
CATALOG = ('<a href="/newsevents/pressreleases/monetary20260916a.htm">HTML</a>'
    '<a href="/newsevents/pressreleases/monetary20260916a.htm">HTML</a>'
    '<a href="/newsevents/pressreleases/monetary20260916a1.htm">Implementation Note</a>'
    '<a href="/newsevents/pressreleases/monetary20261209a.htm">HTML</a>'
    '<a href="https://other.invalid/monetary20260916a.htm">HTML</a>').encode()
ORIGINAL = b'<html><body><h1>FOMC fixture</h1><p>September 16, 2026</p><p>Synthetic policy statement, not actual policy evidence.</p></body></html>'


class FOMCOfficialChannelTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='fomc-channel-', dir=ROOT / 'result/acceptance')
        self.addCleanup(folder.cleanup)
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No external calls'))
        guard.start(); self.addCleanup(guard.stop)
        self.agent = ResearchAgent(ResearchArchive(Path(folder.name) / 'store'), ROOT, offline=True)
        self.task = self.agent.create('核对FOMC声明', entry='事件', cutoff='2026-10-17T00:00:00+08:00')
        self.collector = EvidenceCollector(offline=False)

    def catalogue(self):
        with patch('researchhelper.source_transport.official_html', return_value=CATALOG):
            record = self.collector.external(self.task, 'official', 'release_catalog',
                dict(channel='fed_fomc', page='0'), {})
        self.task['source_records'].append(record)
        return record

    def test_new_tasks_have_policy_channel_version(self):
        self.assertEqual(self.task.get('official_policy_channels_version'), 1)

    def test_only_pinned_fomc_statement_paths_are_accepted(self):
        self.assertEqual(article_url(URL, channel='fed_fomc'), URL)
        for bad in (URL+'?token=x', URL+'#x', URL.replace('https:', 'http:'),
                    URL.replace('www.federalreserve.gov', 'www.federalreserve.gov.evil.invalid'),
                    URL.replace('gov/', 'gov:444/'), URL.replace('a.htm', 'a1.htm'),
                    'https://user:pass@www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm'):
            with self.subTest(url=bad), self.assertRaises(ContractError):
                article_url(bad, channel='fed_fomc')

    def test_calendar_is_a_single_fixed_page_not_a_guessed_annual_url(self):
        self.assertEqual(catalogue_url('0', channel='fed_fomc'),
            'https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm')
        for page in ('1', '2026', '../0', 0):
            with self.assertRaises(ContractError):
                catalogue_url(page, channel='fed_fomc')

    def test_legacy_tasks_cannot_acquire_the_new_channel(self):
        self.task.pop('official_policy_channels_version', None)
        before = copy.deepcopy(self.task['ledger'])
        with self.assertRaises(ContractError):
            self.collector.external(self.task, 'official', 'release_catalog', dict(channel='fed_fomc', page='0'), {})
        self.assertEqual(self.task['ledger'], before)

    def test_catalogue_deduplicates_and_excludes_future_statements_and_notes(self):
        data = parse_catalogue(CATALOG, self.task['cutoff'], channel='fed_fomc')
        self.assertEqual(len(data['entries']), 1)
        self.assertEqual(data['entries'][0]['url'], URL)
        self.assertIn('2026-09-16', data['entries'][0]['title'])
        self.assertIsNone(data['entries'][0]['published_at'])
        self.assertEqual(data['coverage']['skipped_future'], 1)
        self.assertEqual(data['coverage']['completeness'], '未证实')

    def test_original_requires_current_task_catalogue_before_spending(self):
        with self.assertRaises(ContractError):
            validate_request(self.task, 'release_original', dict(catalog_id='foreign', release_id='foreign'))
        self.assertEqual(self.task['ledger']['used']['data_requests'], 0)

    def test_rehashed_catalogue_does_not_rewrite_retained_html(self):
        record = self.catalogue()
        self.assertEqual(len(release_choices(self.task)), 1)
        record['snapshot']['entries'][0]['title'] = 'forged title'
        record['hash'] = fingerprint(record['snapshot'])
        self.assertEqual(release_choices(self.task), [])

    def test_two_mocked_gets_preserve_bytes_and_approval_is_still_pending(self):
        self.catalogue()
        choice = release_choices(self.task)[0]
        parameters = {key: choice[key] for key in ('catalog_id', 'release_id')}
        with patch('researchhelper.source_transport.official_html', return_value=ORIGINAL):
            record = self.collector.external(self.task, 'official', 'release_original', parameters, {})
        self.task['source_records'].append(record)
        validate_original(self.task, record)
        self.assertEqual(self.task['ledger']['used']['data_requests'], 2)
        self.assertEqual(record['snapshot']['official_origin']['url'], URL)
        self.assertEqual(record['source_check'], '待核验')
        self.assertEqual(len(extractable_source_views(self.task)), 1)
        self.assertFalse(self.task['evidence'] or self.task['calculations'])

    def test_strict_schema_keeps_channel_and_page_correlated(self):
        schemas = _planned_request_schemas(self.task)
        request = dict(id='fomc_directory', source='official', operation='release_catalog',
            parameters=dict(channel='fed_fomc', page='0'), question_ids=['q1'], selection_reason='核对官方声明')
        self.assertTrue(any(_matches(request, schema) for schema in schemas))
        request['parameters']['page'] = '1'
        self.assertFalse(any(_matches(request, schema) for schema in schemas))

    def test_inventory_has_one_entry_per_operation_and_no_fake_permission(self):
        tools = [tool for tool in data_request_contract(self.task)['tools'] if tool['source'] == 'official']
        self.assertEqual([tool['operation'] for tool in tools], ['release_catalog', 'release_original'])
        self.assertTrue(all(tool['credential_required'] is False for tool in tools))


if __name__ == '__main__':
    unittest.main()
