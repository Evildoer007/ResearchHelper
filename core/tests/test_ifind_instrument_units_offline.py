"""Independent synthetic tests for per-table iFinD units and calculation context."""
import copy
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.evidence import (
    FIELD_REGISTRY, _source_extract, api_calculation_context_contract, api_cell_candidates,
    api_value_reference, expand_api_value_reference, snapshot_record, validate_calculation_context_basis,
)
from tests import test_api_calculation_context_contract_offline as fixture
from tests.identity_fixture_support import synthetic_identity_basis


class IFinDInstrumentUnitTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.APICalculationContextContractTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.agent = self.fixture.agent
        self.task = self.agent.create('核对指数与基金的真实单位', entry='混合',
                                      objects=['000300.SH', '399300.SZ', '000300.SZ', '510300.SH'])
        self.identity_bases = {code: synthetic_identity_basis(self.task, 'ifind', code,
            '2026-09-29', '2026-09-30') for code in self.task['objects']}

    def record(self, codes=('000300.SH',)):
        parameters = {'codes': ','.join(codes), 'indicators': 'close',
                      'startdate': '2026-09-29', 'enddate': '2026-09-30',
                      'functionpara': {'Currency': 'RMB', 'CPS': '1', 'Fill': 'Omit'}}
        raw = {'errorcode': 0, 'tables': [
            {'thscode': code, 'time': ['2026-09-30'], 'table': {'close': [4000 if index == 0 else 4.432]}}
            for index, code in enumerate(codes)]}
        record = snapshot_record(self.task, 'ifind', 'cmd_history_quotation', parameters, raw)
        self.task['source_records'] = [record]
        return record

    def spec(self, record, table=0):
        code = record['snapshot']['tables'][table]['thscode']
        return {'id': 'cell_' + str(table), 'artifact_id': record['id'], 'object': code,
                'record_locator': {'table': table, 'row': 0, 'field': 'close'},
                'api_value_reference': api_value_reference(record)}

    def flat(self, record, table=0, *, currency_unit=False):
        spec = expand_api_value_reference(self.task, record, self.spec(record, table))
        code = record['snapshot']['tables'][table]['thscode']
        definition = FIELD_REGISTRY['ifind.cmd_history_quotation']
        expected = (definition.get('field_families', {}).get('index_level', definition['fields'])
                    if spec['unit'] == '点' else definition['fields'])['close']
        if currency_unit:
            expected = definition['fields']['close']
            spec.update(raw_unit='元', unit='元', conversion_factor=1)
        return dict(spec, source=record['source'], artifact=record, snapshot=record['snapshot'], snapshot_hash=record['hash'],
                    identity=copy.deepcopy(self.identity_bases[code]),
                    field_definition={'registry': 'ifind.cmd_history_quotation', 'version': definition['version'],
                                      'field': 'close', 'definition': copy.deepcopy(expected),
                                      'documentation': definition['documentation']})

    def test_index_compact_cell_is_points_not_currency(self):
        for code in ('000300.SH', '399300.SZ'):
            with self.subTest(code=code):
                record = self.record((code,))
                spec = expand_api_value_reference(self.task, record, self.spec(record))
                self.assertEqual((spec['raw_unit'], spec['unit'], spec['value']), ('点', '点', 4000))

    def test_mixed_index_stock_and_etf_use_their_own_table_units(self):
        record = self.record(('000300.SH', '000300.SZ', '510300.SH'))
        self.assertEqual([expand_api_value_reference(self.task, record, self.spec(record, table))['unit']
                          for table in range(3)], ['点', '元', '元'])

    def test_correct_flat_index_unit_passes_source_reextraction_without_approval(self):
        record = self.record()
        before = copy.deepcopy(self.task)
        item = self.flat(record)
        self.assertEqual(item['unit'], '点')
        _source_extract(item, self.task)
        self.assertEqual(self.task, before)
        self.assertEqual(self.task['evidence'], before['evidence'])
        self.assertNotIn(item['id'], {row['id'] for row in self.task['evidence']})

    def test_legacy_currency_labeled_index_is_rejected_without_repair(self):
        record = self.record()
        spec = self.flat(record, currency_unit=True)
        before = copy.deepcopy(spec)
        with self.assertRaises(ContractError):
            _source_extract(spec, self.task)
        self.assertEqual(spec, before)

    def test_index_context_does_not_invent_stock_currency_or_adjustment(self):
        record = self.record()
        contract = api_calculation_context_contract(record)
        self.assertIn('index_id', contract['row_fields'])
        self.assertNotIn('security_id', contract['row_fields'])
        self.assertEqual(contract['column_fields'], {'level_kind': {'close': 'close'}})
        self.assertNotIn('currency', contract['fixed_fields'])
        self.assertNotIn('price_basis', contract['fixed_fields'])
        self.assertTrue({'index_variant', 'index_currency', 'index_base'}.issubset(contract['unavailable_fields']))

    def test_index_api_context_validates_only_observed_row_fields(self):
        record = self.record()
        item = self.flat(record)
        values = {'index_id': '000300.SH', 'level_kind': 'close', 'observation_date': '2026-09-30',
                  'window_start': '2026-09-29', 'window_end': '2026-09-30'}
        item['task_id'], item['research_version'] = self.task['id'], self.task['version']
        item['calculation_context'] = values
        item['calculation_context_basis'] = {key: {'kind': 'api', 'artifact_id': record['id'],
                                                   'snapshot_hash': record['hash'], 'field': key} for key in values}
        validate_calculation_context_basis(item, [record])
        for key, value in [('currency', 'CNY'), ('price_basis', 'unadjusted'),
                           ('index_variant', 'price'), ('index_currency', 'CNY'), ('index_base', '1000')]:
            with self.subTest(field=key), self.assertRaises(ContractError):
                altered = copy.deepcopy(item)
                altered['calculation_context'] = {key: value}
                altered['calculation_context_basis'] = {key: {'kind': 'api', 'artifact_id': record['id'],
                                                             'snapshot_hash': record['hash'], 'field': key}}
                validate_calculation_context_basis(altered, [record])

    def test_mixed_context_cannot_use_index_fields_on_an_etf_row(self):
        record = self.record(('000300.SH', '510300.SH'))
        item = self.flat(record, 1)
        item['task_id'], item['research_version'] = self.task['id'], self.task['version']
        item['calculation_context'] = {'index_id': '510300.SH'}
        item['calculation_context_basis'] = {'index_id': {'kind': 'api', 'artifact_id': record['id'],
                                                        'snapshot_hash': record['hash'], 'field': 'index_id'}}
        with self.assertRaises(ContractError):
            validate_calculation_context_basis(item, [record])

    def test_unknown_namespace_retains_raw_response_but_not_numeric_units(self):
        record = self.record(('990001.SH',))
        before = copy.deepcopy(record)
        self.assertIsNone(api_value_reference(record))
        self.assertEqual(api_cell_candidates(record)['cells'], [])
        self.assertEqual(record, before)

    def test_mixed_unknown_table_cannot_inherit_registered_table_unit(self):
        record = self.record(('000300.SH', '990001.SH'))
        self.assertIsNotNone(api_value_reference(record))
        with self.assertRaises(ContractError):
            expand_api_value_reference(self.task, record, self.spec(record, 1))
        candidates = api_cell_candidates(record)
        self.assertEqual([cell['proposal_fields']['record_locator']['table'] for cell in candidates['cells']], [0])

    def test_changed_locator_cannot_preserve_index_unit_after_table_switch(self):
        record = self.record(('000300.SH', '510300.SH'))
        item = self.flat(record)
        item['record_locator']['table'] = 1
        with self.assertRaises(ContractError):
            _source_extract(item, self.task)

    def test_invalid_snapshot_cannot_offer_unit_context(self):
        record = self.record()
        record['snapshot']['tables'][0]['thscode'] = '510300.SH'
        self.assertIsNone(api_value_reference(record))
        self.assertEqual(api_cell_candidates(record)['cells'], [])

    def test_field_definition_version_invalidates_only_unfinished_extraction_cache(self):
        self.record()
        self.task['host_result'] = {}
        before = copy.deepcopy(self.task['ledger'])
        with patch.object(self.agent, '_reasoning_available', return_value=True), \
                patch.object(self.agent, '_save_model_checkpoint'), \
                patch('researchhelper.engine.model_object', return_value=({'evidence_specs': []}, {})) as model:
            self.agent._extract_model_sources(self.task)
            self.agent._extract_model_sources(self.task)
            self.assertEqual(model.call_count, 1)
            updated = copy.deepcopy(FIELD_REGISTRY['ifind.cmd_history_quotation'])
            updated['version'] += 1
            with patch.dict(FIELD_REGISTRY, {'ifind.cmd_history_quotation': updated}):
                self.agent._extract_model_sources(self.task)
                self.agent._extract_model_sources(self.task)
            self.assertEqual(model.call_count, 2)
        self.assertEqual(self.task['ledger'], before)
