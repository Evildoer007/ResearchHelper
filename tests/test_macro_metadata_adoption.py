"""Metadata proposals through normal gates, with retained bytes and mock transport.

This is a new private offline control, never a recovered model result, fresh
provider permission or proof that a catalogue contains every latest release.
"""
import base64
import copy
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, canonical, validate_evidence
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import EvidenceCollector, candidate_evidence
from researchhelper.official_sources import catalogue_url, release_choices
from researchhelper.planning import question_specs
from researchhelper.reasoning import evidence_contract, extractable_source_views

ROOT = Path(__file__).resolve().parents[1]


class MacroMetadataAdoptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = ROOT/'result/acceptance/one-sentence-research-8x1tzgha'
        cls.path = folder/'task.json'
        if not cls.path.exists(): cls.path = folder/'task.json.gz'
        cls.raw = cls.path.read_bytes()
        cls.retained = json.loads(gzip.decompress(cls.raw) if cls.path.suffix == '.gz' else cls.raw)
        cls.pages = {}
        for source in cls.retained['source_records']:
            if source['operation'] == 'release_catalog':
                url = catalogue_url(source['parameters']['page'])
            else:
                url = source['snapshot']['official_origin']['url']
            cls.pages[url] = base64.b64decode(source['snapshot']['original_base64'], validate=True)

    def setUp(self):
        network = patch('socket.socket.connect', side_effect=AssertionError('Offline preflight only'))
        network.start(); self.addCleanup(network.stop)
        folder = tempfile.TemporaryDirectory(prefix='macro-metadata-control-', dir=ROOT/'result/acceptance')
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True, require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task = self.agent.create(self.retained['question'], entry='宏观', research_mode='current')
        self.task = self.agent.revise_plan(self.task['id'], question_specs(self.retained),
            '离线复用留存计划以核对原件契约，不执行模型研究', self.task['input_hash'])
        self.collector = EvidenceCollector(offline=False)
        def transport(task, url, started, timeout, event, **kwargs):
            event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True)
            return self.pages[url]
        with patch('researchhelper.source_transport.official_html', side_effect=transport):
            for page in ('0', '1'):
                source = self.collector.external(self.task, 'official', 'release_catalog',
                                                dict(channel='nbs_release', page=page), {})
                self.task['source_records'].append(source)
            selected = next(c for c in release_choices(self.task)
                            if c['title'] == '2026年9月中国采购经理指数运行情况')
            self.source = self.collector.external(self.task, 'official', 'release_original',
                {key: selected[key] for key in ('catalog_id', 'release_id')}, {})
            self.task['source_records'].append(self.source)

    def tearDown(self):
        self.assertEqual(self.path.read_bytes(), self.raw)

    def proposals(self, *, link_requirements=False):
        views = extractable_source_views(self.task)
        contract = evidence_contract(self.task, views)
        source = next(s for s in contract['sources'] if s['artifact_id'] == self.source['id'])
        body = self.source['snapshot']
        need = self.task['questions'][0]['evidence_needs'][0]
        metadata = [p for p in body['parts'] if p.get('metadata_field') in ('ArticleTitle', 'PubDate')]
        self.assertEqual(len(metadata), 2)
        proposals = [dict(id='metadata_control_' + str(i), artifact_id=self.source['id'],
            object='国家统计局', nature='已实现事实', question_ids=['q1'],
            text=part['text'], unit='不适用', period='2026-09',
            observation_period=dict(frequency='month', value='2026-09'),
            record_locator=dict(part=part['locator'], period_part='标题1', period_quote='2026年9月'),
            availability=source['current_availability_reference'], published_at=None,
            publication=contract['current_publication_template'],
            review=dict(question_ids=['q1'], allowed_natures=['已实现事实'], temporal_use='current_version',
                conditions=['仅本页实际元数据，不证明首发时刻、历史可得性或全部最新期'],
                reason='只核对原页标注字段，不将网页日期提升为投资或因果判断',
                evidence_need_texts={'q1': [need]})) for i, part in enumerate(metadata, 1)]
        if not link_requirements:
            for spec in proposals: spec['review'].pop('evidence_need_texts')
        return proposals

    def test_unmapped_page_tags_can_be_retained_without_claiming_need_coverage(self):
        before = canonical(self.task)
        for spec in self.proposals():
            evidence = candidate_evidence(self.task, self.source, spec)
            ResearchAnalyzer().assess(self.task, evidence, spec['review'], self.task['source_records'])
            validate_evidence(evidence, self.task)
            self.assertEqual(evidence['text'], spec['text'])
            self.assertIsNone(evidence['published_at'])
            self.assertEqual(evidence['unit'], '不适用')
        self.assertEqual(canonical(self.task), before)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 0)
        self.assertFalse(self.task['evidence'] or self.task['theses'])

    def test_mapping_month_context_tags_to_a_point_date_need_remains_rejected(self):
        for spec in self.proposals(link_requirements=True):
            evidence = candidate_evidence(self.task, self.source, spec)
            self.assertEqual(evidence['source_check'], '通过')
            with self.assertRaises(ContractError):
                ResearchAnalyzer().assess(self.task, evidence, spec['review'], self.task['source_records'])
        self.assertFalse(self.task['evidence'] or self.task['theses'])

    def test_observation_month_is_not_a_claim_of_confirmed_first_publication(self):
        spec = copy.deepcopy(self.proposals()[1])
        spec.update(published_at='2026-09-30', publication=dict(at='2026-09-30', basis='模型声明首发时间',
            precision='日', timezone='Asia/Shanghai', certainty='已确认', basis_ref=None))
        with self.assertRaises(ContractError): candidate_evidence(self.task, self.source, spec)


if __name__ == '__main__': unittest.main()
