"""Actual retained bytes and hostile lineage variants; never resume a study."""
import copy
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.knowledge import ResearchKnowledge
from researchhelper.evidence import (candidate_code_references, candidate_evidence,
    direct_code_reference, validate_provenance, CANDIDATE_CODE_LIMITATION)
from researchhelper.output_schema import api_extraction_output_schema, decode_api_extraction_output, _matches
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import evidence_contract, extractable_source_views
from researchhelper.reports import compose

from tests.market_observation_fixtures import (INDEX_CAPTURES, append_index_member_replay,
    append_simulated_ifind_response, close_price_proposals, read_captures)


class CandidateCodeObservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = INDEX_CAPTURES.read_bytes()
        cls.samples = read_captures(cls.original, grouped=True)

    @classmethod
    def tearDownClass(cls):
        if INDEX_CAPTURES.read_bytes() != cls.original:
            raise AssertionError('Retained index capture was changed')

    def setUp(self):
        with tempfile.TemporaryDirectory(prefix='rh-retained-index-') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            try:
                self.task = agent.create('核对上证50在2026年10月16日的成分名单及样本当日收盘价。',
                    entry='指数', research_mode='current')
            finally:
                agent.close_runtime()
        self.quote = append_index_member_replay(self.task, self.samples)
        self.spec = close_price_proposals(self.task, self.quote)[0]
        references = candidate_code_references(self.task, self.quote)
        self.assertEqual(len(references), 1)
        self.reference = references[0]
        self.spec['candidate_code_reference'] = self.reference

    def candidate(self):
        return candidate_evidence(self.task, self.quote, self.spec)

    def test_raw_quote_keeps_value_unit_unknown_publication_and_no_name_mapping(self):
        item = self.candidate()
        self.assertEqual((item['raw_value'], item['value'], item['raw_unit'], item['unit']), (1329, 1329, '元', '元'))
        self.assertIsNone(item['published_at'])
        self.assertEqual(item['object'], '600519.SH')
        self.assertEqual(item['identity']['mode'], 'candidate_provider_observation')
        self.assertNotIn('valid_from', item['identity'])
        self.assertEqual(item['source_receipt']['version'], 4)
        self.assertEqual(item['applicability_check'], '待核验')
        self.assertIsNone(direct_code_reference(self.task, self.quote))

    def test_normal_applicability_and_restore_recheck_both_origins(self):
        item = ResearchAnalyzer().assess(self.task, self.candidate(), self.spec['review'], self.task['source_records'])
        self.assertIn(CANDIDATE_CODE_LIMITATION, item['applicability_review']['conditions'])
        self.task['evidence'].append(item)
        validate_provenance(item, self.task)
        with tempfile.TemporaryDirectory() as folder:
            archive = ResearchArchive(folder)
            self.task['revision'] = 0
            archive.save(self.task)
            restored = archive.get(self.task['id'])
            validate_provenance(restored['evidence'][0], restored)
            restored['source_records'][0]['snapshot']['errorcode'] = -1
            with self.assertRaises(ContractError):
                validate_provenance(restored['evidence'][0], restored)

    def test_old_boolean_unknown_or_historical_policy_has_no_reference(self):
        for value in (None, True, 0, 2):
            self.task['candidate_code_observation_version'] = value
            with self.subTest(value=value):
                self.assertEqual(candidate_code_references(self.task, self.quote), [])
        self.task['candidate_code_observation_version'] = 1
        self.task['research_mode'] = 'historical'
        self.assertEqual(candidate_code_references(self.task, self.quote), [])

    def test_missing_selection_does_not_repair_a_mapping(self):
        self.spec.pop('candidate_code_reference')
        with self.assertRaises(ContractError):
            self.candidate()

    def test_identity_formats_cannot_be_mixed(self):
        for key in ('identity', 'identity_basis_reference', 'direct_code_reference'):
            self.spec[key] = None
            with self.subTest(key=key), self.assertRaises(ContractError):
                self.candidate()
            self.spec.pop(key)

    def test_wrong_code_name_or_truncated_code_cannot_borrow_a_parent(self):
        for value in ('600030.SH', '贵州茅台', '600519'):
            self.spec['object'] = value
            with self.subTest(value=value), self.assertRaises(ContractError):
                self.candidate()

    def test_parent_task_version_receipt_and_hash_are_all_required(self):
        original = copy.deepcopy(self.task['source_records'][0])
        for changes in (dict(task_id='foreign'), dict(research_version=True), dict(research_version=2),
                        dict(acquisition=None), dict(hash='0'*64)):
            self.task['source_records'][0] = dict(original, **changes)
            with self.subTest(changes=changes):
                self.assertEqual(candidate_code_references(self.task, self.quote), [])

    def test_quote_task_version_receipt_and_hash_are_all_required(self):
        original = copy.deepcopy(self.quote)
        for changes in (dict(task_id='foreign'), dict(research_version=True), dict(research_version=2),
                        dict(acquisition=None), dict(hash='0'*64)):
            self.quote.clear(); self.quote.update(copy.deepcopy(dict(original, **changes)))
            with self.subTest(changes=changes):
                self.assertEqual(candidate_code_references(self.task, self.quote), [])

    def test_changed_request_response_or_detached_parent_field_is_not_authority(self):
        for change in ('query', 'response', 'detached'):
            task = copy.deepcopy(self.task)
            parent = task['source_records'][0]
            if change == 'query':
                parent['parameters']['functionpara']['date'] = '20260929'
            elif change == 'response':
                parent['snapshot']['tables'][0]['table']['p03291_f002'] = []
            else:
                parent['snapshot']['tables'][0]['table']['p03291_f002'] = ['000001.SZ']*50
                parent['index_contract']['members'] = [dict(code='600519.SH')]
            with self.subTest(change=change):
                self.assertEqual(candidate_code_references(task, task['source_records'][1]), [])

    def test_no_parent_duplicate_parent_or_future_parent_is_not_a_prior_discovery(self):
        original = copy.deepcopy(self.task)
        for kind in ('missing', 'duplicate', 'future'):
            task = copy.deepcopy(original)
            parent, quote = task['source_records']
            if kind == 'missing':
                task['source_records'] = [quote]
            elif kind == 'duplicate':
                task['source_records'].append(copy.deepcopy(parent))
            else:
                events = task['ledger']['dispatches']
                a, b = parent['acquisition']['dispatch_index'], quote['acquisition']['dispatch_index']
                events[a], events[b] = events[b], events[a]
                parent['acquisition']['dispatch_index'], quote['acquisition']['dispatch_index'] = b, a
            with self.subTest(kind=kind):
                self.assertEqual(candidate_code_references(task, quote), [])

    def test_reference_fields_and_unknown_keys_cannot_be_changed(self):
        for key, value in (('version', True), ('research_version', True), ('artifact_id', 'other'),
                           ('request_hash', '0'*64), ('declaration_hash', '0'*64), ('extra', 'x')):
            self.spec['candidate_code_reference'] = dict(self.reference, **{key: value})
            with self.subTest(key=key), self.assertRaises(ContractError):
                self.candidate()

    def test_source_scope_changes_invalidate_but_plan_growth_is_not_user_code_declaration(self):
        self.task['calculation_specs'].append(dict(id='fixture', method='observed_price_return_v1'))
        self.assertEqual(candidate_code_references(self.task, self.quote), [self.reference])
        self.task['objects'].append('600030.SH')
        self.assertEqual(candidate_code_references(self.task, self.quote), [])

    def test_future_or_forecast_claim_cannot_reuse_a_price_observation(self):
        self.spec['nature'] = '管理层展望'
        with self.assertRaises(ContractError):
            self.candidate()

    def test_literal_schema_exposes_reference_and_decodes_without_fake_identity_periods(self):
        contract = evidence_contract(self.task)
        source = next(row for row in contract['sources'] if row['artifact_id'] == self.quote['id'])
        self.assertEqual(source['candidate_code_references'], [self.reference])
        schema = api_extraction_output_schema(self.task, extractable_source_views(self.task))
        self.assertIsNotNone(schema)
        spec = {key: copy.deepcopy(self.spec[key]) for key in ('id', 'artifact_id', 'object', 'record_locator',
            'nature', 'question_ids', 'review', 'availability', 'api_value_reference', 'candidate_code_reference')}
        spec['review'] = {key: spec['review'][key] for key in ('question_ids', 'allowed_natures', 'conditions', 'reason')}
        spec['review']['evidence_need_texts'] = []
        spec['publication_mode'] = 'unknown_current_snapshot'
        wire = dict(version=1, evidence_specs=[spec])
        self.assertTrue(_matches(wire, schema))
        decoded = decode_api_extraction_output(wire, schema)['evidence_specs'][0]
        self.assertNotIn('identity', decoded)
        self.assertEqual(decoded['candidate_code_reference'], self.reference)

    def test_new_policy_is_frozen_in_input_and_report_but_not_synthetic_cases(self):
        with tempfile.TemporaryDirectory() as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            task = agent.create('核对实际指数名单及后续行情', entry='指数')
            task['knowledge'] = ResearchKnowledge().read(task['entry'], task['question'], context=task)
            task['gaps'] = ['离线契约夹具尚未执行取数，不是研究或报告验收。']
            self.assertEqual(task['candidate_code_observation_version'], 1)
            self.assertEqual(compose(task)['candidate_code_observation_version'], 1)
            old = dict(task); old.pop('candidate_code_observation_version')
            self.assertNotEqual(input_fingerprint(old), input_fingerprint(task))
            self.assertNotIn('candidate_code_observation_version', agent.create_case('mechanism'))
            self.assertNotIn('candidate_code_observation_version', agent.create('核对原文', intent='原件内容核对'))

    def discovery_fixture(self, agent, *, name_conflict=False):
        # Explicit local wire fixture; transport flags here do not represent
        # real provider dispatches, industry membership or online acceptance.
        task = agent.create('研究消费行业，选择实际候选核对收盘价', entry='行业', research_mode='current')
        payload = dict(errorcode=0, tables=[dict(table={
            '股票代码': ['600519.SH', '600519.SH'] if name_conflict else ['600519.SH'],
            '股票简称': ['贵州茅台', '另一名称'] if name_conflict else ['贵州茅台']})])
        append_simulated_ifind_response(task, 'smart_stock_picking',
            dict(searchstring='消费行业股票', searchtype='stock'), payload)
        quote = append_simulated_ifind_response(task, 'cmd_history_quotation',
            self.quote['parameters'], self.quote['snapshot'])
        return task, quote

    def test_discovery_rows_use_the_same_explicit_observation_not_user_declaration(self):
        with tempfile.TemporaryDirectory() as folder, patch('socket.socket.connect', side_effect=AssertionError('Offline fixture')):
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            task, quote = self.discovery_fixture(agent)
            references = candidate_code_references(task, quote)
            self.assertEqual(len(references), 1)
            self.assertEqual(references[0]['discovery']['operation'], 'smart_stock_picking')
            self.assertEqual(references[0]['discovery']['row_locator'], dict(table=0, row=0))
            self.assertIsNone(direct_code_reference(task, quote))
            self.assertEqual(task['objects'], [])
            self.assertEqual(task['evidence'], [])

    def test_conflicting_discovery_names_do_not_authorize_a_code_observation(self):
        with tempfile.TemporaryDirectory() as folder, patch('socket.socket.connect', side_effect=AssertionError('Offline fixture')):
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            task, quote = self.discovery_fixture(agent, name_conflict=True)
            self.assertEqual(candidate_code_references(task, quote), [])

    def test_boolean_nested_row_cannot_equal_an_actual_integer_location(self):
        from researchhelper.evidence import expand_candidate_code_reference
        with tempfile.TemporaryDirectory() as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            task, quote = self.discovery_fixture(agent)
            reference = candidate_code_references(task, quote)[0]
            self.assertEqual(reference['discovery']['row_locator']['row'], 0)
            spec = dict(candidate_code_reference=reference, object=reference['object'])
            self.assertEqual(expand_candidate_code_reference(task, quote, spec)['identity']['provider_code'], '600519.SH')
            forged = copy.deepcopy(spec)
            forged['candidate_code_reference']['discovery']['row_locator']['row'] = False
            with self.assertRaises(ContractError):
                expand_candidate_code_reference(task, quote, forged)
