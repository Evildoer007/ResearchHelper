"""Vendor HK mappings and staged acquisition share one research scope."""
import copy
import gzip
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import charge, now
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import (snapshot_record, _record_current_acquisition, identity_query_parameters,
    identity_resolutions, identity_candidates, available_security_codes, validate_source_request, IDENTITY_CANDIDATE_PROFILE)

SOURCE = ROOT/'result/acceptance/ifind-hk-code-mapping-tzf8uskq/sources.json.gz'


class DraftIdentityScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with gzip.open(SOURCE, 'rt') as stream:
            cls.sources = json.load(stream)['source_records']

    def setUp(self):
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline identity replay'))
        guard.start(); self.addCleanup(guard.stop)
        folder = tempfile.TemporaryDirectory(prefix='rh-identity-scope-');self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.agent = ResearchAgent(ResearchArchive(self.folder/'current'), ROOT, offline=False,
            require_argument_support=True, draft_first=True)
        self.agent.collector.external = self.transport
        self.task = self.agent.create('核对腾讯控股00700.HK的实际供应商代码和日行情。',
            objects=['腾讯控股', '00700.HK'], research_mode='current')

    def transport(self, task, source, operation, params, credentials):
        self.assertEqual(operation, 'get_thscode')
        query_mode = params['functionpara']['mode']
        prototype = next(row for row in self.sources if row.get('parameters', {}).get('functionpara', {}).get('mode') == query_mode)
        payload = copy.deepcopy(prototype['snapshot'])
        if query_mode == 'seccode' and params['seccode'] == '0700':
            payload['tables'][0]['seccode'] = '0700'
        charge(task['ledger'], 'data_requests', 1)
        result = snapshot_record(task, source, operation, params, payload)
        event = dict(kind='data_requests', source=source, operation=operation, batch=task['batch'],
            fingerprint=result['request_hash'], status='成功', time=now(), transport_isolated=True,
            network_request_started=True, network_worker_reaped=True, synthetic_test_transport=True)
        task['ledger']['dispatches'].append(event); _record_current_acquisition(task, result, event)
        return result

    def acquire(self, mode, query, profile=None):
        params = identity_query_parameters(mode, query, profile) if profile else identity_query_parameters(mode, query)
        need = dict(id='mapping_' + mode + '_' + str(self.agent.archive.get(self.task['id'])['batch']),
            source='ifind', operation='get_thscode', parameters=params,
            question_ids=[self.task['questions'][0]['id']], selection_reason='离线实际响应回放。')
        result = self.agent.start(self.task['id'], dict(draft_report=None, data_requests=[need]), defer_delivery=True)
        self.assertEqual(result['status'], '待开始', result.get('error'))
        return result

    def test_exact_vendor_hk_padding_conversion_is_usable_without_guessing(self):
        task = self.acquire('seccode', '00700')
        mapped = identity_resolutions(task)[0]
        self.assertEqual((mapped['status'], mapped['codes']), ('代码映射已核验', ['0700.HK']))
        self.assertFalse(mapped['historical_identity_verified'])
        self.assertIn('0700.HK', available_security_codes(task))

    def test_new_host_requests_do_not_expire_the_original_mapping(self):
        first = self.acquire('seccode', '00700')
        second = self.acquire('secname', '腾讯控股', IDENTITY_CANDIDATE_PROFILE)
        self.assertEqual(first['input_hash'], second['input_hash'])
        self.assertTrue(identity_candidates(second))
        third = self.acquire('seccode', '0700')
        self.assertEqual(second['input_hash'], third['input_hash'])
        self.assertIn('0700.HK', available_security_codes(third))
        self.assertEqual(third['ledger']['supplements'], 0)

    def test_vendor_code_is_permitted_for_the_same_research_quote(self):
        task = self.acquire('seccode', '00700')
        params = dict(codes='0700.HK', indicators='close', startdate='2026-10-19', enddate='2026-10-26',
            functionpara=dict(Currency='RMB', CPS='1', Fill='Omit'))
        self.assertEqual(validate_source_request(task, 'ifind', 'cmd_history_quotation', params), params)

    def test_a_different_vendor_security_does_not_become_a_padding_alias(self):
        task = self.acquire('seccode', '00700')
        source = copy.deepcopy(task['source_records'][0])
        payload = copy.deepcopy(source['snapshot']);payload['tables'][0]['table']['thscode'] = ['2318.HK']
        changed = snapshot_record(task, 'ifind', 'get_thscode', source['parameters'], payload)
        task['source_records'] = [changed]
        self.assertNotEqual(identity_resolutions(task)[0]['status'], '代码映射已核验')

    def test_old_tasks_do_not_gain_new_alias_authority(self):
        old = ResearchAgent(ResearchArchive(self.folder/'legacy'), ROOT, offline=True, require_argument_support=True)
        task = old.create('核对00700.HK', objects=['00700.HK'], research_mode='current')
        task['source_records'].append(self.transport(task, 'ifind', 'get_thscode', identity_query_parameters('seccode', '00700'), {}))
        self.assertNotIn('hk_code_alias_version', task)
        self.assertNotEqual(identity_resolutions(task)[0]['status'], '代码映射已核验')


if __name__ == '__main__':
    unittest.main()
