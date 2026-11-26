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
from researchhelper.contracts import ContractError, charge, now
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

    def test_name_only_continuation_requeries_the_user_literal_after_reopen(self):
        original = self.agent.create('核对港股腾讯控股的挂牌代码。', research_mode='current')
        self.task = original
        first = self.acquire('secname', '腾讯控股', IDENTITY_CANDIDATE_PROFILE)
        first = self.agent.start(first['id'], dict(data_requests=[], draft_report=dict(
            title='挂牌代码核对', conclusion='列示实际查询到的市场候选。')))
        frozen = self.agent.archive.report(first['id'], 1)
        self.assertEqual(identity_candidates(first)[0]['codes'], ['0700.HK', 'TCTZF.PQ'])
        self.assertTrue(first.get('delivery'))

        reopened = ResearchAgent(ResearchArchive(self.folder/'current'), ROOT, offline=False,
            require_argument_support=True, draft_first=True)
        self.addCleanup(reopened.close_runtime)
        reopened.collector.external = self.transport
        neutral = reopened.follow_up(first['id'], '先解释一下证券简称的口径。', start=False)
        self.assertEqual(neutral['objects'], [])
        child = reopened.follow_up(neutral['id'], '它在2026年10月12日的收盘价呢？', start=False)
        self.assertEqual(child['objects'], ['腾讯控股'])
        self.assertEqual(child['source_records'], [])
        self.assertEqual(available_security_codes(child), [])
        params = identity_query_parameters('secname','腾讯控股',IDENTITY_CANDIDATE_PROFILE)
        self.assertEqual(validate_source_request(child,'ifind','get_thscode',params),params)
        for invalid in ('腾讯', '宁德时代'):
            with self.assertRaises(ContractError):
                validate_source_request(child,'ifind','get_thscode',
                                        identity_query_parameters('secname',invalid,IDENTITY_CANDIDATE_PROFILE))
        short = reopened.follow_up(neutral['id'], '收盘价呢？', start=False)
        self.assertEqual(short['objects'], ['腾讯控股'])
        need = dict(id='child_name', source='ifind', operation='get_thscode', parameters=params,
            question_ids=['q1'], selection_reason='按本轮沿用的用户原名重新查询。')
        resolved = reopened.start(child['id'], dict(data_requests=[need], draft_report=None), defer_delivery=True)
        self.assertEqual(available_security_codes(resolved), ['0700.HK'])
        self.assertEqual(resolved['ledger']['used']['data_requests'], 1)
        self.assertEqual(resolved['source_records'][0]['task_id'], child['id'])
        self.assertEqual(reopened.archive.report(first['id'], 1), frozen)
        self.assertNotEqual(first['source_records'][0]['id'], resolved['source_records'][0]['id'])

        switched = reopened.follow_up(child['id'], '改看宁德时代的风险。', start=False)
        self.assertEqual(switched['objects'], [])
        with self.assertRaises(ContractError):
            validate_source_request(switched,'ifind','get_thscode',params)

    def test_name_comparison_uses_user_order_and_does_not_guess_a_market(self):
        task = self.agent.create('比较腾讯控股和宁德时代的行情。', research_mode='current')
        for name, value in [('宁德时代','300750.SZ,3750.HK'), ('腾讯控股','0700.HK,TCTZF.PQ')]:
            params = identity_query_parameters('secname',name,IDENTITY_CANDIDATE_PROFILE)
            payload = dict(errorcode=0,inputParams=dict(mode='secname'),
                tables=[dict(secname=name,table=dict(thscode=[value]))])
            record = snapshot_record(task,'ifind','get_thscode',params,payload)
            event = dict(kind='data_requests', source='ifind', operation='get_thscode', status='成功',
                fingerprint=record['request_hash'], batch=task['batch'], time=now(), transport_isolated=True,
                network_request_started=True, network_worker_reaped=True)
            task['ledger']['dispatches'].append(event)
            task['source_records'].append(_record_current_acquisition(task,record,event))
        self.agent.archive.save(task)
        ambiguous = self.agent.follow_up(task['id'],'它的行情呢？',start=False)
        self.assertEqual(ambiguous['objects'], [])
        child = self.agent.follow_up(ambiguous['id'],'只看后者的收盘价。',start=False)
        self.assertEqual(child['objects'], ['宁德时代'])
        self.assertEqual(available_security_codes(child), [])
        self.assertNotIn('腾讯控股',json.dumps(child['conversation_context'],ensure_ascii=False))
        self.assertTrue(child['conversation_context']['comparison_requests'])


if __name__ == '__main__':
    unittest.main()
