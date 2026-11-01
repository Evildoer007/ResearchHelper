"""New macro discovery protocol, replaying retained real supplier text only."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import EvidenceCollector, data_request_contract, validate_source_request
from researchhelper.economic_sources import (available_economic_indicators, discovered_indicators,
    search_response, validate_economic_request)
from researchhelper.planning import input_fingerprint

ROOT = Path(__file__).resolve().parents[1]


class EconomicDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.raw = (ROOT/'result/acceptance/ifind-economic-discovery-20261023-v1/3-search_edb.txt').read_text()
        self.policy = json.loads((ROOT/'result/acceptance/ifind-financial-metadata-20261023/2-get_limit_policy.json').read_text())
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        resources = Path(os.environ.get('RESEARCHHELPER_IFIND_METADATA_RESOURCES', str(ROOT)))
        self.agent = ResearchAgent(ResearchArchive(directory.name), resources, offline=True, require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task = self.agent.create('查询中国国家统计局CPI当月同比的宏观原始序列。', entry='宏观',
            research_mode='current', questions=[dict(id='q1', kind='fact', question='中国CPI当月同比数据？',
                                                    evidence_needs=['CPI当月同比'], method_needs=[])])
        self.collector = EvidenceCollector(offline=False)
        guard = patch('socket.socket.connect', side_effect=AssertionError('Offline only'))
        guard.start()
        self.addCleanup(guard.stop)
        transport = patch.object(self.collector, '_http_json', side_effect=self.replay)
        transport.start()
        self.addCleanup(transport.stop)

    def replay(self, task, request, timeout, event):
        event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True,
                     synthetic_test_transport=True)
        if urlsplit(request.full_url).path.endswith('/get_limit_policy'):
            return copy.deepcopy(self.policy)
        if urlsplit(request.full_url).path.endswith('/search_edb'):
            self.assertEqual(request.get_method(), 'GET')
            self.assertIsNone(request.data)
            return dict(text=self.raw)
        raise AssertionError('Only metadata replay is permitted')

    def collect(self, operation, parameters):
        record = self.collector.external(self.task, 'ifind', operation, parameters,
                                         {'access_token': 'offline-discovery-token'})
        self.task['source_records'].append(record)
        return record

    def search(self):
        self.collect('get_limit_policy', {})
        return self.collect('search_edb', {'query': '中国居民消费价格指数同比'})

    def test_real_text_keeps_frequency_country_and_display_id_without_inference(self):
        rows = search_response(dict(text=self.raw))
        self.assertEqual(len(rows), 20)
        monthly = next(row for row in rows if row['displayId'] == 'M002826730')
        self.assertEqual((monthly['freq'], monthly['unit'], monthly['dataSource']), ('M', '%', '国家统计局'))
        self.assertEqual(next(row for row in rows if row['displayId'] == 'G020207919')['country'], '')
        self.assertIn('W006007975', {row['displayId'] for row in rows})

    def test_actual_same_task_search_unlocks_only_retained_display_ids(self):
        record = self.search()
        rows = discovered_indicators(self.task)
        self.assertEqual(len(rows), 20)
        self.assertTrue(all(row['artifact_id'] == record['id'] and not row['financial_approval'] for row in rows))
        request = dict(indicators='M002826730', startdate='2026-01-01', enddate='2026-09-30')
        validate_economic_request(self.task, 'edb_service', request)
        for identifier in ('2826730', 'M000000001'):
            with self.assertRaises(ContractError):
                validate_economic_request(self.task, 'edb_service', dict(request, indicators=identifier))

    def test_search_requires_current_account_policy_and_new_task_policy(self):
        with self.assertRaises(ContractError):
            validate_source_request(self.task, 'ifind', 'search_edb', {'query': '中国CPI'})
        self.collect('get_limit_policy', {})
        operations = {tool['operation'] for tool in data_request_contract(self.task)['tools']}
        self.assertIn('search_edb', operations)
        self.assertNotIn('edb_service', operations)
        for flag in (None, True, 2):
            self.task['economic_discovery_version'] = flag
            with self.assertRaises(ContractError):
                validate_source_request(self.task, 'ifind', 'search_edb', {'query': '中国CPI'})

    def test_tool_contract_contains_search_and_complete_unapproved_candidates(self):
        self.search()
        tools = data_request_contract(self.task)['tools']
        self.assertTrue(any(tool['operation'] == 'search_edb' for tool in tools))
        edb = next(tool for tool in tools if tool['operation'] == 'edb_service')
        self.assertEqual(len(edb['search_candidates']), 20)
        self.assertTrue(edb['raw_only'])

    def test_changed_foreign_duplicate_or_imported_record_never_unlocks_codes(self):
        record = self.search()
        original = copy.deepcopy(record)
        for modification in ({'task_id': 'foreign'}, {'hash': '0'*64}, {'acquisition': {}},
                             {'parameters': {'query': 'other query'}}):
            record.update(modification)
            self.assertEqual(discovered_indicators(self.task), [])
            record.clear()
            record.update(copy.deepcopy(original))
        self.task['source_records'].append(copy.deepcopy(record))
        self.assertEqual(discovered_indicators(self.task), [])

    def test_missing_country_is_not_completed_from_query(self):
        self.search()
        row = next(row for row in discovered_indicators(self.task) if row['displayId'] == 'G020207919')
        self.assertEqual(row['country'], '')

    def test_legacy_explicit_indicator_still_works_without_discovery(self):
        self.task.pop('economic_discovery_version')
        self.task['question'] += ' M002826730'
        validate_economic_request(self.task, 'edb_service', dict(indicators='M002826730',
            startdate='2026-01-01', enddate='2026-09-30'))
        self.assertEqual(available_economic_indicators(self.task), ['M002826730'])

    def test_forecast_metadata_does_not_allow_future_series_window(self):
        self.search()
        self.assertIn('W006478446', available_economic_indicators(self.task))
        with self.assertRaises(ContractError):
            validate_economic_request(self.task, 'edb_service', dict(indicators='W006478446',
                startdate='2026-01-01', enddate='2031-12-31'))

    def test_empty_permission_and_invalid_json_are_not_invented_candidates(self):
        self.assertEqual(search_response(dict(text='未查询到符合要求的EDB指标')), [])
        for text in ('无权限', '月度调用次数上限', 'CPI代码是1234', '```json\n{bad}\n```'):
            with self.assertRaises(ContractError):
                search_response(dict(text=text))

    def test_duplicate_display_id_invalid_dates_and_extra_blocks_are_rejected(self):
        row = search_response(dict(text=self.raw))[0]
        invalid = copy.deepcopy(row)
        invalid['startDate'] = '20260231'
        for rows in ([row, row], [invalid], [dict(row, displayId=row['id'])]):
            with self.assertRaises(ContractError):
                search_response(dict(text='```json\n'+json.dumps(rows)+'\n```'))
        with self.assertRaises(ContractError):
            search_response(dict(text=self.raw+'\n```json\n[]\n```'))

    def test_query_injection_extra_parameters_and_control_characters_rejected(self):
        self.collect('get_limit_policy', {})
        for params in ({'query': ''}, {'query': 'x'*201}, {'query': 'CPI\nsecret'},
                       {'query': 'CPI', 'endpoint': 'https://example.com'}):
            with self.assertRaises(ContractError):
                validate_source_request(self.task, 'ifind', 'search_edb', params)

    def test_discovery_version_changes_input_fingerprint(self):
        before = input_fingerprint(self.task)
        self.task.pop('economic_discovery_version')
        self.assertNotEqual(input_fingerprint(self.task), before)
