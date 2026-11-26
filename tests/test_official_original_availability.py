"""Registered official downloads share current-version provenance, not approval.

All transport bytes and completion flags below are explicit offline fixtures.
These tests do not establish real source, model or financial quality.
"""
import copy
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from tests.test_official_release_channel import CATALOG
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.evidence import (EvidenceCollector, candidate_evidence, current_original_reference,
    _record_current_original, validate_current_snapshot)
from researchhelper.official_sources import release_choices
from researchhelper.reasoning import evidence_contract
from researchhelper.source_reader import read, binding, validate_read_specs
from researchhelper.output_schema import material_extraction_output_schema, decode_material_extraction_output

BODY = ('<html><body><h1>离线合成统计材料</h1><p>数据观测日期：2026年9月30日。</p>'
        '<p>合成样本指标为12.5%，仅用于离线测试。</p></body></html>').encode()


class OfficialOriginalAvailabilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='official-current-', dir=ROOT/'result/acceptance')
        self.addCleanup(temporary.cleanup)
        self.agent = ResearchAgent(ResearchArchive(temporary.name), ROOT, offline=True)
        self.addCleanup(self.agent.close_runtime)
        self.task = self.create_task()
        self.collector = EvidenceCollector(offline=False)
        guard = patch('socket.socket.connect', side_effect=AssertionError('No external network'))
        guard.start(); self.addCleanup(guard.stop)
        def returned(task, url, started, timeout, event, **kwargs):
            event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True)
            return CATALOG if url.endswith('/zxfbhjd/') else BODY
        with patch('researchhelper.source_transport.official_html', side_effect=returned):
            self.catalogue = self.collector.external(self.task, 'official', 'release_catalog', dict(channel='nbs_release', page='0'), {})
            self.task['source_records'].append(self.catalogue)
            selected = release_choices(self.task)[0]
            self.original = self.collector.external(self.task, 'official', 'release_original',
                {key:selected[key] for key in ('catalog_id','release_id')}, {})
            self.task['source_records'].append(self.original)

    def create_task(self, **options):
        return self.agent.create('核查合成样本指标', entry='宏观', research_mode='current',
                                 objects=['合成样本指标'], **options)

    def test_local_html_retry_preserves_the_existing_official_source_and_spending(self):
        task = self.agent.archive.save(self.task)
        original_id = self.agent.archive.retain_original(task, 'material', self.original['name'], BODY)
        before = copy.deepcopy(task)
        with patch('socket.socket.connect', side_effect=AssertionError('No external request')):
            result = self.agent.retry_original(task['id'], original_id)
        self.assertEqual(result, before)
        self.assertEqual(result['ledger'], before['ledger'])
        retained = next(row for row in result['source_records'] if row['id'] == self.original['id'])
        self.assertEqual(current_original_reference(result, retained), current_original_reference(before, self.original))
        self.assertEqual(result['materials'], [])

    def spec(self):
        parts = self.original['snapshot']['parts']
        observed = next(p for p in parts if '数据观测日期' in p['text'])
        value = next(p for p in parts if '12.5%' in p['text'])
        return dict(id='official_value', artifact_id=self.original['id'], object='合成样本指标',
            text=value['text'], record_locator=dict(part=value['locator'], period_part=observed['locator'], period_quote=observed['text']),
            period='2026-09-30', nature='已实现事实', unit='%', raw_unit='%', raw_token='12.5',
            value=12.5, raw_value=12.5, conversion_factor=1, published_at=None,
            availability=current_original_reference(self.task, self.original),
            publication=dict(at=None,basis_ref=None,precision='未知',timezone='未知',certainty='未知',
                basis='首发未知，仅核验本次取得版本'), question_ids=['q1'])

    def test_current_reference_binds_snapshot_and_original_bytes_separately(self):
        reference = current_original_reference(self.task, self.original)
        self.assertIsNotNone(reference)
        self.assertEqual(reference['snapshot_hash'], self.original['snapshot_hash'])
        self.assertEqual(reference['original_hash'], self.original['hash'])
        self.assertFalse(reference['historical_availability_approved'])
        self.assertFalse(reference['event_causality_approved'])
        self.assertEqual(self.original['acquisition']['version'], 3)
        self.assertEqual(self.task['ledger']['used']['data_requests'], 2)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 0)

    def test_numeric_source_then_separate_current_applicability(self):
        item = candidate_evidence(self.task, self.original, self.spec())
        self.assertEqual(item['source_check'], '通过')
        self.assertEqual(item['applicability_check'], '待核验')
        item = ResearchAnalyzer().assess(self.task, item, dict(question_ids=['q1'], allowed_natures=['已实现事实'],
            conditions=['仅为合成原件的明确观测日与本次版本'], reason='隔离的适用性流程检查',
            temporal_use='current_version'), self.task['source_records'])
        self.assertEqual(item['applicability_check'], '通过')
        self.assertIsNone(item['published_at'])

    def test_model_context_offers_actual_reference_not_a_financial_approval(self):
        source = next(item for item in evidence_contract(self.task)['sources'] if item['artifact_id']==self.original['id'])
        self.assertEqual(source['current_availability_reference'], current_original_reference(self.task,self.original))
        self.assertFalse(source['current_original_rule']['source_or_financial_approval'])

    def test_directory_never_mints_original_availability(self):
        self.assertNotIn('acquisition', self.catalogue)
        self.assertIsNone(current_original_reference(self.task, self.catalogue))

    def test_import_without_download_receipt_is_not_eligible(self):
        self.original.pop('acquisition', None)
        self.assertIsNone(current_original_reference(self.task, self.original))

    def test_historical_and_document_modes_never_gain_current_exception(self):
        for field,value in (('research_mode','historical'), ('intent','原件内容核对')):
            task = dict(self.task, **{field:value})
            self.assertIsNone(current_original_reference(task, self.original))

    def test_cross_task_or_version_receipt_is_rejected(self):
        for field,value in (('task_id','foreign'),('research_version',99)):
            original = copy.deepcopy(self.original)
            original.get('acquisition', {})[field] = value
            self.assertIsNone(current_original_reference(self.task, original))

    def test_unreaped_failed_or_wrong_source_transport_is_not_eligible(self):
        for field,value in (('network_worker_reaped',False),('status','失败'),('source','ifind'),('operation','release_catalog')):
            task = copy.deepcopy(self.task)
            task['ledger']['dispatches'][-1][field] = value
            self.assertIsNone(current_original_reference(task,self.original))

    def test_changed_original_or_catalogue_does_not_reuse_receipt(self):
        original = copy.deepcopy(self.original)
        original['snapshot']['parts'][0]['text'] += '变更'
        original['snapshot_hash'] = fingerprint(original['snapshot'])
        self.assertIsNone(current_original_reference(self.task,original))
        task = copy.deepcopy(self.task)
        task['source_records'][0]['snapshot']['entries'][0]['title'] += '变更'
        task['source_records'][0]['hash'] = fingerprint(task['source_records'][0]['snapshot'])
        self.assertIsNone(current_original_reference(task,self.original))

    def test_first_publication_or_fake_observation_day_cannot_be_filled(self):
        for updates in (dict(published_at=self.original['obtained_at']), dict(period='2026-10-01')):
            with self.subTest(updates=updates), self.assertRaises(ContractError):
                candidate_evidence(self.task,self.original,dict(self.spec(),**updates))

    def test_historical_or_causal_use_is_not_approved(self):
        item = candidate_evidence(self.task,self.original,self.spec())
        for use in ('historical_availability','event_causality'):
            with self.subTest(use=use), self.assertRaises(ContractError):
                ResearchAnalyzer().assess(self.task,copy.deepcopy(item),dict(question_ids=['q1'],
                    allowed_natures=['已实现事实'],conditions=['合成边界'],reason='不得越界',temporal_use=use),
                    self.task['source_records'])

    def test_wrapper_file_hash_cannot_diverge_from_retained_original(self):
        original = copy.deepcopy(self.original)
        original['hash'] = 'f'*64
        self.assertIsNone(current_original_reference(self.task,original))

    def test_unknown_source_event_cannot_mint_a_new_receipt(self):
        original = copy.deepcopy(self.original)
        original.pop('acquisition')
        event = copy.deepcopy(self.task['ledger']['dispatches'][-1])
        event['source'] = 'unregistered'
        self.task['ledger']['dispatches'].append(event)
        _record_current_original(self.task,original,event)
        self.assertNotIn('acquisition',original)

    def test_actual_reading_and_structured_submission_keep_unknown_first_publication(self):
        requests = [dict(artifact_id=self.original['id'],snapshot_hash=self.original['snapshot_hash'],
            queries=[],parts=[dict(part=p['locator'],start=0,end=len(p['text'])) for p in self.original['snapshot']['parts']],
            reason='读取合成指标原值及观测日',question_ids=['q1'])]
        bundle = read(self.task,requests,binding(self.task),[self.original['id']])
        spec = self.spec()
        validate_read_specs(self.task,[spec],bundle)
        schema = material_extraction_output_schema(self.task,bundle['views'])
        self.assertIsNotNone(schema)
        spec.pop('published_at');spec.pop('publication')
        spec['publication_mode'] = 'unknown_current_original'
        spec['review'] = dict(question_ids=['q1'],allowed_natures=['已实现事实'],conditions=['合成资料的当前版本'],
            reason='读取及结构提交检查',evidence_need_texts=[],temporal_use='current_version')
        decoded = decode_material_extraction_output(dict(version=1,evidence_specs=[spec]),schema)
        candidate = candidate_evidence(self.task,self.original,decoded['evidence_specs'][0])
        self.assertEqual(candidate['value'],12.5)
        self.assertIsNone(candidate['published_at'])
        self.assertEqual(candidate['applicability_check'],'待核验')

    def test_archive_restore_preserves_binding_and_spent_budget_without_new_approval(self):
        before = current_original_reference(self.task,self.original)
        self.agent.archive.save(self.task)
        restored = self.agent.archive.get(self.task['id'])
        original = next(r for r in restored['source_records'] if r['id']==self.original['id'])
        self.assertEqual(current_original_reference(restored,original),before)
        self.assertEqual(restored['ledger']['used']['data_requests'],2)
        self.assertFalse(restored['evidence'] or restored['calculations'] or restored['theses'])
