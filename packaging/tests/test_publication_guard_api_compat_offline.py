"""Retain raw API checks without granting unregistered publication approval.

Only fabricated local payloads are used. Extraction checks deliberately return
no approval receipt; publication rejection is exercised through the real gate.
Legacy TestCase classes remain inside imported modules, outside discovery.
"""
import copy
import io
import json
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.evidence import (FIELD_REGISTRY, _source_extract, candidate_evidence,
    evidence_binding, snapshot_record, validate_calculation_context_basis, validate_provenance)
from researchhelper.reports import compose
from tests import test_evidence_chain_offline as chain
from tests import test_market_background_sources_offline as market
from tests import test_calculation_context_sources_offline as context_fixture


class PublicationGuardAPICompatibilityTests(unittest.TestCase):
    def setUp(self):
        network = patch('socket.socket.connect', side_effect=AssertionError('External network forbidden'))
        network.start()
        self.addCleanup(network.stop)
        self.fixture = chain.EvidenceChainTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def extraction_only(self, task, artifact, specification):
        """Exercise registered extraction, with neither source nor review approval."""
        item = copy.deepcopy(specification)
        item.pop('review', None)
        definition = FIELD_REGISTRY[artifact['source'] + '.' + artifact['operation']]
        field = specification['record_locator']['field']
        item.update(artifact=copy.deepcopy(artifact), snapshot=copy.deepcopy(artifact['snapshot']),
            snapshot_hash=artifact.get('snapshot_hash', artifact['hash']), source=artifact['source'],
            task_id=task['id'], research_version=task['version'], source_check='待核验', applicability_check='待核验',
            field_definition={'registry': artifact['source'] + '.' + artifact['operation'],
                'version': definition['version'], 'field': field,
                'definition': copy.deepcopy(definition['fields'][field]), 'documentation': definition['documentation']})
        before = fingerprint(item)
        _source_extract(item, task)
        self.assertEqual(fingerprint(item), before)
        self.assertNotIn('source_receipt', item)
        self.assertNotIn('applicability_receipt', item)
        self.assertEqual(item['source_check'], '待核验')
        self.assertEqual(item['applicability_check'], '待核验')
        return item

    def reject_publication(self, task, artifact, spec, pattern='首次公开时间缺少原始来源定位'):
        before = fingerprint(task)
        with patch('researchhelper.evidence.verification_receipt') as receipt:
            with self.assertRaisesRegex(ContractError, pattern):
                candidate_evidence(task, artifact, spec)
            receipt.assert_not_called()
        self.assertEqual(fingerprint(task), before)

    def retained_rejection(self, spec, artifact):
        done = self.fixture.run_spec(spec)
        self.assertEqual(done['status'], '部分完成', done.get('error'))
        self.assertEqual(done['evidence'], [])
        self.assertEqual(done['calculations'], [])
        self.assertEqual(done['source_records'][-1], artifact)
        self.assertIn('首次公开时间缺少原始来源定位', done['rejected_evidence'][0]['rejection'])
        self.assertEqual(done['ledger']['used']['data_requests'], 0)
        self.assertEqual(done['ledger']['used']['model_calls'], 0)
        self.assertFalse(done['synthetic'])
        return done

    def test_tinyshare_raw_cell_retained_without_publication_receipts(self):
        artifact = self.fixture.snapshot()
        spec = self.fixture.spec(artifact)
        task = self.fixture.agent.archive.get(self.fixture.task['id'])
        item = self.extraction_only(task, artifact, spec)
        self.assertEqual(item['raw_value'], 10)
        self.assertEqual((item['raw_unit'], item['unit'], item['conversion_factor']), ('元', '元', 1))
        self.assertEqual(item['identity']['provider_code'], artifact['snapshot']['data'][0]['ts_code'])
        self.assertEqual(item['period'], '2026-07-19')
        self.assertEqual(item['artifact']['parameters'], chain.PARAMS)
        self.reject_publication(task, artifact, spec)
        self.retained_rejection(spec, artifact)

    def test_ifind_raw_table_currency_identity_retained_without_approval(self):
        raw = {'errorcode': 0, 'tables': [{'thscode': '600000.SH', 'time': ['2026-07-19'], 'table': {'close': [10]}}]}
        artifact = self.fixture.snapshot('ifind', raw)
        spec = self.fixture.spec(artifact, record_locator={'table': 0, 'row': 0, 'field': 'close'})
        task = self.fixture.agent.archive.get(self.fixture.task['id'])
        item = self.extraction_only(task, artifact, spec)
        self.assertEqual(item['artifact']['snapshot'], raw)
        self.assertEqual(item['artifact']['parameters'], chain.IFIND_PARAMS)
        self.assertEqual(item['field_definition']['registry'], 'ifind.cmd_history_quotation')
        self.assertEqual((item['raw_value'], item['value'], item['raw_unit'], item['unit']), (10, 10, '元', '元'))
        self.assertEqual(item['identity']['provider_code'], raw['tables'][0]['thscode'])
        self.reject_publication(task, artifact, spec)
        self.retained_rejection(spec, artifact)

    def test_thousand_yuan_conversion_is_extraction_only(self):
        artifact = self.fixture.snapshot()
        spec = self.fixture.spec(artifact, record_locator={'row': 0, 'field': 'amount'},
            raw_value=2, raw_unit='千元', value=2000, conversion_factor=1000)
        task = self.fixture.agent.archive.get(self.fixture.task['id'])
        item = self.extraction_only(task, artifact, spec)
        self.assertEqual((item['raw_value'], item['value'], item['raw_unit'], item['unit'], item['conversion_factor']),
                         (2, 2000, '千元', '元', 1000))
        for changes in ({'raw_value': 3}, {'value': 2}, {'raw_unit': '元'}, {'conversion_factor': True}):
            with self.subTest(changes=changes), self.assertRaises(ContractError):
                _source_extract(dict(item, **changes), task)
        self.reject_publication(task, artifact, spec)
        self.retained_rejection(spec, artifact)

    def test_ifind_mock_dispatch_once_retains_checkpoint_without_fallback(self):
        fixture = self.fixture
        raw = {'errorcode': 0, 'tables': [{'thscode': '600000.SH', 'time': ['2026-07-19'], 'table': {'close': [10]}}]}
        fixture.agent.offline = fixture.agent.collector.offline = False
        fixture.agent.source_credentials = {'ifind': {'access_token': 'independent-fake-token'}}
        need = {'id': 'daily_close', 'operation': 'cmd_history_quotation', 'parameters': chain.IFIND_PARAMS, 'question_ids': ['q1']}
        spec = fixture.spec({'id': 'pending'}, record_locator={'table': 0, 'row': 0, 'field': 'close'})
        spec.pop('artifact_id')
        spec['request_id'] = 'daily_close'
        with patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps(raw).encode())) as transport, \
                patch.object(fixture.agent.collector, '_require_tinyshare', side_effect=AssertionError('Silent fallback forbidden')) as fallback:
            done = fixture.agent.start(fixture.task['id'], {'data_requests': [need], 'evidence_specs': [spec],
                'answers': {'q1': {'answer': '10元', 'refs': [spec['id']]}}})
        self.assertEqual(transport.call_count, 1)
        fallback.assert_not_called()
        request = transport.call_args.args[0]
        self.assertEqual(json.loads(request.data), chain.IFIND_PARAMS)
        self.assertEqual(done['status'], '部分完成', done.get('error'))
        self.assertEqual(done['evidence'], [])
        self.assertEqual(done['calculations'], [])
        self.assertEqual(done['ledger']['used']['data_requests'], 1)
        self.assertEqual(done['ledger']['used']['model_calls'], 0)
        self.assertEqual(len(done['ledger']['dispatches']), 1)
        self.assertEqual(len(done['source_records']), 1)
        record = done['source_records'][0]
        self.assertEqual(record['source'], 'ifind')
        self.assertEqual(record['snapshot'], raw)
        self.assertEqual(record['parameters'], chain.IFIND_PARAMS)
        self.assertEqual(record['data_need_ids'], ['daily_close'])
        self.assertEqual(record['hash'], fingerprint(raw))
        extracted = self.extraction_only(done, record, dict(spec, artifact_id=record['id']))
        self.assertEqual((extracted['raw_value'], extracted['value']), (10, 10))
        self.assertEqual(fixture.agent.archive.get(done['id']), done)
        self.assertEqual(fixture.agent.archive.report(done['id'], 1)['content']['source_records'], done['source_records'])
        self.assertNotIn('independent-fake-token', json.dumps(done))
        self.assertIn('首次公开时间缺少原始来源定位', done['rejected_evidence'][0]['rejection'])

    def test_api_context_exact_cell_request_and_binding_without_approval(self):
        fixture = context_fixture.CalculationContextSourcesTests()
        fixture.setUp()
        params = {'codes': '600000.SH', 'indicators': 'close', 'startdate': '2026-09-29', 'enddate': '2026-09-30',
                  'functionpara': {'Currency': 'RMB', 'CPS': '1', 'Fill': 'Omit'}}
        record = snapshot_record(fixture.task, 'ifind', 'cmd_history_quotation', params,
            {'errorcode': 0, 'tables': [{'thscode': '600000.SH', 'time': ['2026-09-30'], 'table': {'close': [10]}}]})
        fixture.task['source_records'].append(record)
        context = {'security_id': '示例公司', 'currency': 'CNY', 'price_basis': 'unadjusted', 'price_kind': 'close',
                   'observation_date': '2026-09-30', 'window_start': '2026-09-29', 'window_end': '2026-09-30'}
        spec = {'id': 'price', 'object': '示例公司', 'record_locator': {'table': 0, 'row': 0, 'field': 'close'},
            'identity': {'provider_code': '600000.SH', 'object': '示例公司', 'basis': '合成测试身份记录',
                         'valid_from': '2026-01-01', 'valid_to': '2026-12-31'},
            'published_at': '2026-09-30', 'period': '2026-09-30', 'nature': '已实现事实',
            'raw_value': 10, 'value': 10, 'raw_unit': '元', 'unit': '元', 'conversion_factor': 1,
            'calculation_context': context, 'calculation_context_basis': {key: {'kind': 'api',
                'artifact_id': record['id'], 'snapshot_hash': record['hash'], 'field': key} for key in context}}
        item = self.extraction_only(fixture.task, record, spec)
        fixture.check(item)
        before = evidence_binding(item)
        changed = copy.deepcopy(item)
        changed['calculation_context']['window_end'] = '2026-10-01'
        self.assertNotEqual(evidence_binding(changed), before)
        with self.assertRaises(ContractError):
            fixture.check(changed)
        with self.assertRaises(ContractError):
            validate_provenance(changed, fixture.task)
        bad = copy.deepcopy(item)
        bad['calculation_context_basis']['currency']['field'] = 'price_basis'
        with self.assertRaises(ContractError):
            validate_calculation_context_basis(bad, fixture.task['source_records'])
        self.reject_publication(fixture.task, record, spec)
        self.assertEqual(record['parameters'], params)
        self.assertEqual(record['snapshot']['tables'][0]['table']['close'], [10])

    def test_legal_filing_evidence_tampered_conditions_fail_report_recheck(self):
        record, spec = self.fixture.filing()
        spec['publication']['basis_ref'] = {'kind': 'directory', 'artifact_id': record['id'],
            'snapshot_hash': record['hash'], 'field': 'published_at'}
        done = self.fixture.run_spec(spec)
        self.assertEqual(done['status'], '已完成', done.get('error'))
        self.assertEqual(done['evidence'][0]['source_receipt']['component'], 'EvidenceCollector')
        self.assertEqual(done['evidence'][0]['applicability_receipt']['component'], 'ResearchAnalyzer')
        frozen = self.fixture.agent.archive.report(done['id'], 1)
        changed = copy.deepcopy(done)
        changed['evidence'][0]['applicability_review']['conditions'] = ['改写条件']
        with self.assertRaises(ContractError):
            compose(changed)
        self.assertEqual(self.fixture.agent.archive.get(done['id']), done)
        self.assertEqual(self.fixture.agent.archive.report(done['id'], 1), frozen)
        self.assertEqual(done['ledger']['used']['model_calls'], 0)
        self.assertEqual(done['ledger']['used']['data_requests'], 0)

    def market_fixture(self):
        fixture = market.MarketBackgroundSourceTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_all_market_field_units_survive_with_candidate_cross_original_rejection(self):
        fixture = self.market_fixture()
        fields = FIELD_REGISTRY['tinyshare.daily_basic']['fields']
        self.assertEqual(len(fields), 15)
        for field, definition in fields.items():
            with self.subTest(field=field):
                artifact = fixture.snapshot(field, 3.125)
                spec = fixture.spec(artifact, field, 3.125)
                item = self.extraction_only(fixture.task, artifact, spec)
                self.assertEqual(item['raw_value'], 3.125)
                self.assertEqual(item['raw_unit'], definition['raw_unit'])
                self.assertEqual(item['value'], 3.125 * definition['factor'])
                self.assertEqual(item['unit'], definition['unit'])
                self.assertEqual(item['field_definition']['registry'], 'tinyshare.daily_basic')
                self.assertEqual(item['artifact']['parameters'], artifact['parameters'])
                self.assertEqual(item['period'], '2026-09-30')
                self.assertEqual(item['identity']['provider_code'], artifact['snapshot']['data'][0]['ts_code'])
                self.reject_publication(fixture.task, artifact, spec, '不对应本条证据原件')

    def test_missing_pe_and_negative_pb_retained_without_publication_approval(self):
        fixture = self.market_fixture()
        for empty in (None, '', 'nan'):
            with self.subTest(empty=empty):
                artifact = fixture.snapshot('pe', empty)
                original = copy.deepcopy(artifact)
                spec = fixture.spec(artifact, 'pe', 0)
                with self.assertRaises(ContractError):
                    self.extraction_only(fixture.task, artifact, spec)
                with patch('researchhelper.evidence.verification_receipt') as receipt:
                    with self.assertRaises(ContractError):
                        candidate_evidence(fixture.task, artifact, spec)
                    receipt.assert_not_called()
                self.assertEqual(artifact, original)
                self.assertNotEqual(artifact['snapshot']['data'][0]['pe'], 0)
        artifact = fixture.snapshot('pb', -1.5)
        spec = fixture.spec(artifact, 'pb', -1.5)
        item = self.extraction_only(fixture.task, artifact, spec)
        self.assertEqual(item['raw_value'], -1.5)
        self.assertEqual(item['value'], -1.5)
        self.assertNotIn('recommendation', item)
        self.reject_publication(fixture.task, artifact, spec, '不对应本条证据原件')


PREFIX = __name__ + '.PublicationGuardAPICompatibilityTests.'
REPLACED_EXPECTATIONS = {
    'tests.test_evidence_chain_offline.EvidenceChainTests.test_tinyshare_original_cell_and_two_distinct_receipts': PREFIX + 'test_tinyshare_raw_cell_retained_without_publication_receipts',
    'tests.test_evidence_chain_offline.EvidenceChainTests.test_ifind_original_table_and_currency_definition': PREFIX + 'test_ifind_raw_table_currency_identity_retained_without_approval',
    'tests.test_evidence_chain_offline.EvidenceChainTests.test_thousand_yuan_conversion_preserves_raw_value': PREFIX + 'test_thousand_yuan_conversion_is_extraction_only',
    'tests.test_evidence_chain_offline.EvidenceChainTests.test_mocked_ifind_dispatch_checkpoint_and_no_silent_fallback': PREFIX + 'test_ifind_mock_dispatch_once_retains_checkpoint_without_fallback',
    'tests.test_calculation_context_sources_offline.CalculationContextSourcesTests.test_api_derivation_uses_exact_cell_identity_and_request': PREFIX + 'test_api_context_exact_cell_request_and_binding_without_approval',
    'tests.test_evidence_chain_offline.EvidenceChainTests.test_tampered_application_conditions_fail_report_recheck': PREFIX + 'test_legal_filing_evidence_tampered_conditions_fail_report_recheck',
    'tests.test_publication_relationship_offline.PublicationSourceBoundaryTests.test_registered_field_units_survive_but_borrowed_publication_is_rejected': PREFIX + 'test_all_market_field_units_survive_with_candidate_cross_original_rejection',
    'tests.test_publication_relationship_offline.PublicationSourceBoundaryTests.test_missing_pe_and_negative_pb_preserved_without_false_availability': PREFIX + 'test_missing_pe_and_negative_pb_retained_without_publication_approval',
}
ADAPTATION_NOTES = {
    original: {'replacement': replacement, 'old_test_modified': False,
        'coverage': {
            'test_tinyshare_raw_cell_retained_without_publication_receipts': 'Exact raw cell, identity, request, units, zero usage and retained source; missing publication rejected without issuing either approval receipt.',
            'test_ifind_raw_table_currency_identity_retained_without_approval': 'iFinD table/row/field, RMB/unadjusted/Omit request, registered units and identity retained; ordinary research remains incomplete.',
            'test_thousand_yuan_conversion_is_extraction_only': '2 thousand yuan remains raw 2 and extracted 2000 yuan; incorrect value/unit/factor rejected; extraction does not grant research approval.',
            'test_ifind_mock_dispatch_once_retains_checkpoint_without_fallback': 'One real mocked transport dispatch, one ledger use, exact request/source checkpoint/hash/data_need_id, no tinyshare fallback or credential leak; no publication approval.',
            'test_api_context_exact_cell_request_and_binding_without_approval': 'Exact API context derivation, fingerprint, changed-window and wrong-field rejection retained separately from publication refusal.',
            'test_legal_filing_evidence_tampered_conditions_fail_report_recheck': 'Same-original filing directory gives lawful publication anchor; both receipts exist before review-condition tampering; compose rejects and archive remains unchanged.',
            'test_all_market_field_units_survive_with_candidate_cross_original_rejection': 'All 15 registered fields retain raw/converted units, values, identity, request and period; borrowed publication rejected at candidate stage with zero approval receipts.',
            'test_missing_pe_and_negative_pb_retained_without_publication_approval': 'None/empty/nan PE never promoted to zero; negative PB remains -1.5 and not a recommendation; borrowed publication rejected before receipt issuance.',
        }[replacement.rsplit('.', 1)[1]]}
    for original, replacement in REPLACED_EXPECTATIONS.items()
}
