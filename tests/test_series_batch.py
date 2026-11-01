"""Versioned batch requests and per-table coverage; no network or old mutations."""
import copy
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import data_request_contract, snapshot_record
from researchhelper.market_series import (batch_capacity, batch_enabled, series_settings,
    series_request_shapes, series_record_contract, series_response_coverage, validate_series_request)
from researchhelper.output_schema import acquisition_output_schema, decode_acquisition_output
from researchhelper.planning import input_fingerprint
from researchhelper.knowledge import ResearchKnowledge
from researchhelper.reports import compose, check_content

ROOT = Path(__file__).resolve().parents[1]
RETAINED = ROOT/'result/acceptance/multimarket-fixed-api-zccl8xwm/task.json.gz'


def literal(schema):
    if 'enum' in schema: return schema['enum'][0]
    if 'anyOf' in schema: return literal(schema['anyOf'][0])
    if schema.get('type') == 'object': return {k: literal(v) for k, v in schema['properties'].items()}
    if schema.get('type') == 'array': return []
    return {'string':'', 'boolean':False, 'integer':0, 'number':0, 'null':None}.get(schema.get('type'))


class SeriesBatchTests(unittest.TestCase):
    def setUp(self):
        self.original = RETAINED.read_bytes()
        self.task = json.loads(gzip.decompress(self.original))
        self.task['series_batch_version'] = 1
        self.task['input_hash'] = input_fingerprint(self.task)
        sources = [s for s in self.task['source_records'] if s['operation'] == 'date_sequence']
        self.params = copy.deepcopy(sources[0]['parameters'])
        self.params['codes'] = '510300.SH,159919.SZ'
        self.payload = dict(errorcode=0, tables=[copy.deepcopy(s['snapshot']['tables'][0]) for s in sources])
        guard = patch('socket.socket.connect', side_effect=AssertionError('No network'))
        guard.start(); self.addCleanup(guard.stop)

    def tearDown(self):
        self.assertEqual(RETAINED.read_bytes(), self.original)

    def test_actual_account_policy_enables_same_family_batch(self):
        capacity = batch_capacity(self.task)
        self.assertEqual(capacity['max_codes'], 20)
        self.assertEqual(capacity['max_cells'], 50000)
        self.assertEqual(validate_series_request(self.task, 'date_sequence', self.params),
                         ['2026-10-12', '2026-10-16'])

    def test_no_policy_or_foreign_policy_does_not_enable_batch(self):
        for foreign in (False, True):
            task = copy.deepcopy(self.task)
            if foreign:
                next(s for s in task['source_records'] if s['operation']=='get_limit_policy')['task_id'] = 'foreign'
            else:
                task['source_records'] = [s for s in task['source_records'] if s['operation']!='get_limit_policy']
            self.assertIsNone(batch_capacity(task))
            with self.assertRaises(ContractError): validate_series_request(task, 'date_sequence', self.params)

    def test_policy_units_and_function_types_are_not_guessed(self):
        for issue in ('unit', 'types', 'limit'):
            task = copy.deepcopy(self.task)
            policy = next(s for s in task['source_records'] if s['operation']=='get_limit_policy')
            limits = policy['snapshot']['request_data_limit']
            if issue == 'unit': limits['unit'] = 'unknown'
            elif issue == 'types':
                for rule in limits['rules']:
                    if 'dateSequence' in rule['data_types']: rule['data_types'] = 'dateSequence'
            else:
                for rule in limits['rules']:
                    if 'dateSequence' in rule['data_types']: rule['max_per_request'] = True
            # Synthetic detached policy mutation, not a real supplier update.
            from researchhelper.contracts import fingerprint
            value = fingerprint(policy['snapshot'])
            policy['hash'] = policy['acquisition']['snapshot_hash'] = value
            task['ledger']['dispatches'][policy['acquisition']['dispatch_index']]['response_snapshot_hash'] = value
            self.assertIsNone(batch_capacity(task), issue)

    def test_legacy_and_invalid_versions_do_not_silently_enable_batch(self):
        old = copy.deepcopy(self.task); old.pop('series_batch_version')
        with self.assertRaises(ContractError): validate_series_request(old, 'date_sequence', self.params)
        for value in (True, 2, '1'):
            with self.assertRaises(ContractError): batch_enabled(dict(series_batch_version=value))

    def test_single_requests_still_work_without_policy(self):
        task = copy.deepcopy(self.task); task['source_records'] = []
        params = dict(self.params, codes='510300.SH')
        validate_series_request(task, 'date_sequence', params)

    def test_mixed_unknown_duplicate_and_undeclared_codes_are_rejected(self):
        for codes in ('510300.SH,600519.SH', '510300.SH,0700.HK', '510300.SH,510300.SH',
                      '510300.SH,510500.SH', '510300.SH, 159919.SZ'):
            with self.subTest(codes=codes), self.assertRaises(ContractError):
                validate_series_request(self.task, 'date_sequence', dict(self.params, codes=codes))

    def test_window_and_field_count_are_bounded_before_dispatch(self):
        params = dict(self.params, startdate='2025-10-24', enddate='2026-10-24')
        with patch('researchhelper.market_series.batch_capacity', return_value=dict(max_cells=10,max_codes=20)):
            with self.assertRaises(ContractError): validate_series_request(self.task, 'date_sequence', params)
        bad = copy.deepcopy(self.params); bad['indipara'][0]['indiparams'] = ['wrong']
        with self.assertRaises(ContractError): validate_series_request(self.task, 'date_sequence', bad)

    def test_order_independent_table_coverage_keeps_actual_cells(self):
        self.payload['tables'].reverse()
        result = series_response_coverage(self.task, self.params, self.payload)
        self.assertEqual(result['numeric_cells'], 20)
        self.assertEqual(result['missing_codes'], [])
        self.assertTrue(result['raw_only'])
        self.assertFalse(result['financial_approval'])
        self.assertFalse(result['continuous_calendar_approved'])

    def test_missing_code_field_and_null_remain_explicit_not_filled(self):
        payload = copy.deepcopy(self.payload)
        first = payload['tables'][0]
        field = self.params['indipara'][0]['indicator']
        first['table'][field][0] = None
        payload['tables'] = [first]
        result = series_response_coverage(self.task, self.params, payload)
        self.assertEqual(len(result['missing_codes']), 1)
        self.assertEqual(len(result['missing_cells']), 1)
        self.assertFalse(result['absence_proved'])
        first['table'].pop(field)
        result = series_response_coverage(self.task, self.params, payload)
        self.assertIn(field, result['tables'][0]['missing_fields'])
        self.assertEqual(first['time'], self.payload['tables'][0]['time'])

    def test_foreign_duplicate_dates_unaligned_and_nonfinite_values_are_rejected(self):
        field = self.params['indipara'][0]['indicator']
        for issue in ('foreign','duplicate_code','duplicate_date','future','length','boolean','nonfinite'):
            payload = copy.deepcopy(self.payload); first = payload['tables'][0]
            if issue == 'foreign': first['thscode'] = 'FOREIGN'
            elif issue == 'duplicate_code': payload['tables'].append(copy.deepcopy(first))
            elif issue == 'duplicate_date': first['time'][1] = first['time'][0]
            elif issue == 'future': first['time'][0] = '2026-10-17'
            elif issue == 'length': first['table'][field].pop()
            elif issue == 'boolean': first['table'][field][0] = True
            else: first['table'][field][0] = float('inf')
            with self.subTest(issue=issue), self.assertRaises(ContractError):
                series_response_coverage(self.task, self.params, payload)

    def test_source_records_expose_batch_coverage_without_financial_adoption(self):
        record = snapshot_record(self.task, 'ifind', 'date_sequence', self.params, self.payload)
        self.assertEqual(record['status'], '成功待核验')
        contract = record['series_contract']
        self.assertEqual(contract['family'], 'ETF')
        self.assertEqual(contract['codes'], ['510300.SH','159919.SZ'])
        self.assertEqual(contract['response_coverage']['numeric_cells'], 20)
        self.assertFalse(contract['numeric_evidence_registered'])

    def test_invalid_response_is_recorded_without_approving_source(self):
        self.payload['tables'][0]['thscode'] = 'FOREIGN'
        record = snapshot_record(self.task, 'ifind', 'date_sequence', self.params, self.payload)
        self.assertEqual(record['status'], '返回结构异常')

    def test_model_wire_accepts_batch_and_compiles_only_registered_parameters(self):
        schema = acquisition_output_schema(self.task, dict(data_request_contract=data_request_contract(self.task)))
        request = dict(id='batch_series', source='ifind', operation='date_sequence', question_ids=['q1'],
            selection_reason='同类ETF使用同字段同窗口', parameters=copy.deepcopy(self.params))
        for item in request['parameters']['indipara']: item.pop('indiparams')
        wire = literal(schema); wire.update(data_requests=[request], stop_reason='')
        result = decode_acquisition_output(wire, schema, {})
        self.assertEqual(result['data_requests'][0]['parameters'], self.params)
        self.assertTrue(all(set(item)=={'indicator'} for item in request['parameters']['indipara']))

    def test_new_flag_is_bound_and_old_contract_shape_is_unchanged(self):
        old = copy.deepcopy(self.task); old.pop('series_batch_version')
        self.assertNotEqual(input_fingerprint(old), input_fingerprint(self.task))
        contract = series_record_contract(old, dict(self.params, codes='510300.SH'))
        self.assertEqual(contract['version'], 2)
        self.assertNotIn('codes', contract)
        self.assertFalse(any('batch_limits' in p for p in series_request_shapes(old)[0]['family_shapes']))

    def test_new_current_task_enables_policy_but_not_unreceived_account_permissions(self):
        with tempfile.TemporaryDirectory() as folder:
            agent = ResearchAgent(ResearchArchive(Path(folder)), ROOT, offline=True, require_argument_support=True)
            task = agent.create('核对两只ETF的净值', entry='ETF', objects=['510300.SH','159919.SZ'], research_mode='current')
            self.assertEqual(task['series_batch_version'], 1)
            self.assertIsNone(batch_capacity(task))
            task['knowledge'] = ResearchKnowledge().read(task['entry'], task['question'], context=task)
            task['status'] = '部分完成'
            task['gaps'] = ['仅离线契约检查，尚未取数和分析。']
            content = compose(task)
            check_content(content)
            self.assertEqual(content['series_batch_version'], 1)


if __name__ == '__main__':
    unittest.main()
