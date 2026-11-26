"""Retain raw API checks without granting unregistered publication approval.

Only fabricated local payloads are used. Extraction checks deliberately return
no approval receipt; publication rejection is exercised through the real gate.
Legacy TestCase classes remain inside imported modules, outside discovery.
"""
import copy
import json
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.evidence import (FIELD_REGISTRY, _source_extract, candidate_evidence,
    evidence_binding, snapshot_record, validate_calculation_context_basis, validate_provenance,
    verification_receipt, expand_direct_code_reference)
from researchhelper.reports import compose
from tests import test_evidence_chain_offline as chain
from tests import test_market_background_sources_offline as market


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
        item = copy.deepcopy(expand_direct_code_reference(task, artifact, specification))
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

    def reject_publication(self, task, artifact, spec, pattern='证据published_at缺失'):
        # Current acquisition can answer current questions without a first-release
        # date. This negative proposal explicitly omits that limited availability.
        unsupported = copy.deepcopy(spec)
        unsupported.pop('availability', None)
        before = fingerprint(task)
        with patch('researchhelper.evidence.verification_receipt', wraps=verification_receipt) as receipt:
            with self.assertRaisesRegex(ContractError, pattern):
                candidate_evidence(task, artifact, unsupported)
            self.assertFalse(any(call.args[0]['id'] == spec['id'] for call in receipt.call_args_list))
        self.assertEqual(fingerprint(task), before)

    def retained_rejection(self, spec, artifact):
        unsupported = copy.deepcopy(spec)
        unsupported.pop('availability', None)
        used = copy.deepcopy(self.fixture.task['ledger']['used'])
        done = self.fixture.run_spec(unsupported)
        self.assertEqual(done['status'], '部分完成', done.get('error'))
        self.assertEqual(done['evidence'], [])
        self.assertEqual(done['calculations'], [])
        self.assertEqual(done['source_records'][-1], artifact)
        self.assertIn('证据published_at缺失', done['rejected_evidence'][0]['rejection'])
        for kind in ('data_requests', 'model_calls'):
            self.assertEqual(done['ledger']['used'][kind], used[kind])
        self.assertFalse(done['synthetic'])
        return done

    def test_tinyshare_raw_cell_retained_without_publication_receipts(self):
        artifact = self.fixture.acquired()
        spec = self.fixture.current_spec(artifact)
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
        artifact = self.fixture.acquired('ifind')
        spec = self.fixture.current_spec(artifact)
        task = self.fixture.agent.archive.get(self.fixture.task['id'])
        item = self.extraction_only(task, artifact, spec)
        self.assertEqual(item['artifact']['snapshot'], chain.QUOTE_RESPONSE)
        self.assertEqual(item['artifact']['parameters'], chain.IFIND_PARAMS)
        self.assertEqual(item['field_definition']['registry'], 'ifind.cmd_history_quotation')
        self.assertEqual((item['raw_value'], item['value'], item['raw_unit'], item['unit']), (10, 10, '元', '元'))
        self.assertEqual(item['identity']['provider_code'], chain.QUOTE_RESPONSE['tables'][0]['thscode'])
        self.reject_publication(task, artifact, spec)
        self.retained_rejection(spec, artifact)

    def test_thousand_yuan_conversion_is_extraction_only(self):
        artifact = self.fixture.acquired()
        spec = self.fixture.current_spec(artifact, record_locator={'row': 0, 'field': 'amount'},
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
        fixture.create_current()
        fixture.agent.offline = fixture.agent.collector.offline = False
        fixture.agent.source_credentials = {'ifind': {'access_token': 'independent-fake-token'}}
        need = dict(id='daily_close', source='ifind', operation='cmd_history_quotation',
            parameters=chain.IFIND_PARAMS, question_ids=['q1'], selection_reason='核验合成收盘原值')
        def receive(task, request, timeout, event):
            self.assertEqual(request.get_header('Access_token'), 'independent-fake-token')
            self.assertEqual(json.loads(request.data), chain.IFIND_PARAMS)
            persisted = fixture.agent.archive.get(task['id'])
            self.assertEqual(persisted['ledger']['used']['data_requests'], 1)
            self.assertEqual(persisted['ledger']['dispatches'][-1]['status'], '已派发')
            chain.isolated_response(event)
            return copy.deepcopy(chain.QUOTE_RESPONSE)
        # This is a collector-boundary fixture, not proof of real wire transport.
        with patch.object(fixture.agent.collector, '_http_json', side_effect=receive) as transport, \
                patch.object(fixture.agent.collector, '_require_tinyshare', side_effect=AssertionError('Silent fallback forbidden')) as fallback:
            fixture.task = fixture.agent.start(fixture.task['id'], {'data_requests': [need]}, defer_delivery=True)
            record = fixture.task['source_records'][0]
            spec = fixture.current_spec(record)
            done = self.retained_rejection(spec, record)
        transport.assert_called_once()
        fallback.assert_not_called()
        self.assertEqual(done['ledger']['used']['data_requests'], 1)
        self.assertEqual(done['ledger']['used']['model_calls'], 0)
        self.assertEqual(len(done['ledger']['dispatches']), 1)
        self.assertEqual(len(done['source_records']), 1)
        self.assertEqual(record['source'], 'ifind')
        self.assertEqual(record['snapshot'], chain.QUOTE_RESPONSE)
        self.assertEqual(record['parameters'], chain.IFIND_PARAMS)
        self.assertEqual(record['data_need_ids'], ['daily_close'])
        self.assertEqual(record['hash'], fingerprint(chain.QUOTE_RESPONSE))
        extracted = self.extraction_only(done, record, spec)
        self.assertEqual((extracted['raw_value'], extracted['value']), (10, 10))
        self.assertEqual(fixture.agent.archive.get(done['id']), done)
        self.assertEqual(fixture.agent.archive.report(done['id'], 1)['content']['source_records'], done['source_records'])
        self.assertNotIn('independent-fake-token', json.dumps(done))

    def test_api_context_exact_cell_request_and_binding_without_approval(self):
        from tests.test_identity_period_provenance import IdentityPeriodFixture, CODE, SESSION
        from researchhelper.evidence import direct_code_reference
        fixture = IdentityPeriodFixture()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        record = fixture.artifact
        spec = copy.deepcopy(fixture.spec)
        spec.pop('identity')
        spec['direct_code_reference'] = direct_code_reference(fixture.task, record)
        context = dict(security_id=CODE, currency='CNY', price_basis='unadjusted', price_kind='close',
            observation_date=SESSION, window_start=SESSION, window_end=SESSION)
        spec.update(calculation_context=context, calculation_context_basis={key:dict(kind='api',
            artifact_id=record['id'], snapshot_hash=record['hash'], field=key) for key in context})
        item = self.extraction_only(fixture.task, record, spec)
        validate_calculation_context_basis(item, fixture.task['source_records'])
        before = evidence_binding(item)
        changed = copy.deepcopy(item)
        changed['calculation_context']['window_end'] = '2026-10-01'
        self.assertNotEqual(evidence_binding(changed), before)
        with self.assertRaises(ContractError):
            validate_calculation_context_basis(changed, fixture.task['source_records'])
        with self.assertRaises(ContractError):
            validate_provenance(changed, fixture.task)
        bad = copy.deepcopy(item)
        bad['calculation_context_basis']['currency']['field'] = 'price_basis'
        with self.assertRaises(ContractError):
            validate_calculation_context_basis(bad, fixture.task['source_records'])
        self.reject_publication(fixture.task, record, spec)
        self.assertEqual(record['parameters']['codes'], CODE)
        self.assertEqual(record['snapshot']['tables'][0]['table']['close'], [4.5])

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
                with patch('researchhelper.evidence.verification_receipt', wraps=verification_receipt) as receipt:
                    with self.assertRaises(ContractError):
                        candidate_evidence(fixture.task, artifact, spec)
                    self.assertFalse(any(call.args[0]['id'] == spec['id'] for call in receipt.call_args_list))
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
            'test_tinyshare_raw_cell_retained_without_publication_receipts': 'Exact raw cell, registered current-code identity, request and units remain unapproved during extraction. Removing current availability rejects unknown publication; no extra use is charged. Current acceptance is separately preserved by the base tests.',
            'test_ifind_raw_table_currency_identity_retained_without_approval': 'iFinD raw table, request, units and current-code identity remain exact. Only the negative copy without current availability is rejected; unknown first-publication time is not a universal current-research block.',
            'test_thousand_yuan_conversion_is_extraction_only': '2 thousand yuan remains raw 2 and extracted 2000 yuan; incorrect value/unit/factor rejected; extraction does not grant research approval.',
            'test_ifind_mock_dispatch_once_retains_checkpoint_without_fallback': 'One fabricated collector-boundary response and one ledger use preserve exact request, checkpoint, hash and need id. No Tinyshare fallback or credential leak. This is not a wire-transport or supplier-permission assertion.',
            'test_api_context_exact_cell_request_and_binding_without_approval': 'Exact API context derivation, fingerprint, changed-window and wrong-field rejection retained separately from publication refusal.',
            'test_legal_filing_evidence_tampered_conditions_fail_report_recheck': 'Same-original filing directory gives lawful publication anchor; both receipts exist before review-condition tampering; compose rejects and archive remains unchanged.',
            'test_all_market_field_units_survive_with_candidate_cross_original_rejection': 'All 15 registered fields retain raw/converted units, values, identity, request and period; borrowed publication rejected at candidate stage with zero approval receipts.',
            'test_missing_pe_and_negative_pb_retained_without_publication_approval': 'None/empty/nan PE never promoted to zero; negative PB remains -1.5 and not a recommendation; borrowed publication rejected before receipt issuance.',
        }[replacement.rsplit('.', 1)[1]]}
    for original, replacement in REPLACED_EXPECTATIONS.items()
}
