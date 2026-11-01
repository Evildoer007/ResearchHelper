"""Outgoing request validation precedes cache aliases and durable dispatch state."""

import copy
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import EvidenceCollector, snapshot_record
from researchhelper.financial_sources import FINANCIAL_RECORD_KEYS


class HostRequestPreflightTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='request-preflight-')
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name), offline=False)
        self.task = self.agent.create('核对浦发银行600000.SH公开材料', objects=['600000.SH', '浦发银行'],
            cutoff='2026-10-02T18:00:00+08:00', questions=[
                {'id': 'q1', 'question': '材料写了什么？', 'kind': 'fact'}])
        self.task.update(status='运行中', batch=1)
        self.agent.archive.save(self.task)
        guard = patch('socket.socket.connect', side_effect=AssertionError('External network forbidden'))
        guard.start()
        self.addCleanup(guard.stop)

    def identity(self):
        params = {'secname': '浦发银行', 'functionpara': {'mode': 'secname', 'sectype': '',
            'market': '', 'tradestatus': '0', 'isexact': '1'}}
        payload = {'errorcode': 0, 'inputParams': {'mode': 'secname', 'isexact': 1},
            'tables': [{'secname': '浦发银行', 'table': {'thscode': ['600000.SH']}}]}
        return 'ifind', 'get_thscode', params, payload

    def financial(self, operation='income'):
        field = 'revenue' if operation == 'income' else 'n_cashflow_act'
        params = {'ts_code': '600000.SH', 'fields': ','.join(FINANCIAL_RECORD_KEYS) + ',' + field,
            'start_date': '20260701', 'end_date': '20260930', 'period': '20260630', 'report_type': '1'}
        payload = {'code': 0, 'data': [{'ts_code': '600000.SH', 'ann_date': '20260828',
            'f_ann_date': '20260828', 'end_date': '20260630', 'report_type': '1',
            'comp_type': '2', 'update_flag': '1', field: 123.5}]}
        return 'tinyshare', operation, params, payload

    def need(self, sample):
        source, operation, params, _ = sample
        return {'id': 'new_alias', 'source': source, 'operation': operation,
            'parameters': params, 'question_ids': ['q1'], 'selection_reason': '明确核对已登记原始记录'}

    def retain(self, sample):
        source, operation, params, payload = sample
        record = snapshot_record(self.task, source, operation, params, payload)
        record.update(data_need_ids=['original_need'], question_ids=[])
        self.task['source_records'].append(record)
        return record

    def reject_without_mutation(self, need):
        before = copy.deepcopy(self.task)
        with patch.object(self.agent, '_journal') as journal, \
                patch.object(self.agent, '_save_model_checkpoint') as checkpoint, \
                patch.object(self.agent.collector, 'external') as transport:
            with self.assertRaises(ContractError):
                self.agent._acquire_sources(self.task, [need])
        journal.assert_not_called()
        checkpoint.assert_not_called()
        transport.assert_not_called()
        self.assertEqual(self.task, before)

    def test_cached_identity_rechecks_current_question_scope(self):
        sample = self.identity()
        self.retain(sample)
        self.task.update(question='核对其他公司600001.SH公开材料', objects=['600001.SH'])
        self.reject_without_mutation(self.need(sample))

    def test_cached_income_rechecks_current_security_scope(self):
        sample = self.financial()
        self.retain(sample)
        self.task.update(question='核对其他公司600001.SH公开材料', objects=['600001.SH'])
        self.reject_without_mutation(self.need(sample))

    def test_cached_cashflow_rechecks_current_security_scope(self):
        sample = self.financial('cashflow')
        self.retain(sample)
        self.task.update(question='核对其他公司600001.SH公开材料', objects=['600001.SH'])
        self.reject_without_mutation(self.need(sample))

    def test_invalid_identity_is_rejected_before_checkpoint(self):
        sample = self.identity()
        sample[2]['secname'] = '其他公司'
        self.reject_without_mutation(self.need(sample))

    def test_invalid_financial_period_is_rejected_before_checkpoint(self):
        sample = self.financial()
        sample[2]['period'] = '20261231'
        self.reject_without_mutation(self.need(sample))

    def test_unknown_operation_cannot_reuse_a_forged_cache(self):
        sample = ('tinyshare', 'income_vip', {}, {'code': 0, 'data': []})
        source, operation, params, payload = sample
        self.task['source_records'].append({'id': 'unknown_cache', 'kind': 'api',
            'task_id': self.task['id'], 'research_version': self.task['version'], 'source': source,
            'operation': operation, 'parameters': params, 'snapshot': payload, 'hash': fingerprint(payload),
            'request_hash': fingerprint({'source': source, 'operation': operation, 'parameters': params}),
            'status': '成功待核验'})
        self.reject_without_mutation(self.need(sample))

    def test_valid_identity_and_financial_cache_remain_zero_cost(self):
        for sample in (self.identity(), self.financial(), self.financial('cashflow')):
            with self.subTest(operation=sample[1]):
                record = self.retain(sample)
                ledger = copy.deepcopy(self.task['ledger'])
                with patch.object(self.agent, '_journal') as journal, \
                        patch.object(self.agent.collector, 'external') as transport:
                    self.agent._acquire_sources(self.task, [self.need(sample)])
                transport.assert_not_called()
                journal.assert_called_once()
                self.assertEqual(record['data_need_ids'], ['original_need', 'new_alias'])
                self.assertEqual(record['question_ids'], ['q1'])
                self.assertEqual(fingerprint(record['snapshot']), record['hash'])
                self.assertEqual(self.task['ledger'], ledger)

    def test_collector_rejects_out_of_scope_identity_without_credentials(self):
        sample = self.identity()
        sample[2]['secname'] = '其他公司'
        collector = EvidenceCollector(offline=False)
        with patch.object(collector, '_resolve_ifind_access') as auth, self.assertRaises(ContractError) as caught:
            collector.external(self.task, *sample[:3], {})
        self.assertNotIn('Token', str(caught.exception))
        auth.assert_not_called()
        self.assertEqual(self.task['ledger']['used']['data_requests'], 0)


if __name__ == '__main__':
    unittest.main()
