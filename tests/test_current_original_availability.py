"""Current-original contracts over a stable, explicitly synthetic two-page PDF.

Only the iFinD HTTP/download transport is simulated. Parsing and source/date
checks use real implementations; no old result directory or supplier call.
"""
import copy
import hashlib
from pathlib import Path
import unittest
import tempfile
from unittest.mock import patch

from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.contracts import ContractError, fingerprint, validate_evidence
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import (EvidenceCollector, IFIND_REPORT_FIELDS, _record_current_original, candidate_evidence,
    current_original_reference, validate_current_snapshot)
from researchhelper.reasoning import evidence_contract
from researchhelper.output_schema import material_extraction_output_schema, decode_material_extraction_output
from researchhelper.source_reader import binding, read, validate_read_specs

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / 'tests/fixtures/synthetic-current-quarterly.pdf'
OBSERVATION_QUOTE = '2026 年6 ⽉30 ⽇'
SENT_QUOTE = '2026 年 7 ⽉21 ⽇'


class CurrentOriginalAvailabilityTests(unittest.TestCase):
    def setUp(self):
        payload = ORIGINAL.read_bytes()
        self.digest = hashlib.sha256(payload).hexdigest()
        guard = patch('socket.socket.connect', side_effect=AssertionError('No network'))
        guard.start(); self.addCleanup(guard.stop)
        directory = tempfile.TemporaryDirectory(prefix='rh-current-original-')
        self.addCleanup(directory.cleanup)
        self.agent = ResearchAgent(ResearchArchive(directory.name), ROOT, offline=True, require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task = self.agent.create('核对合成基金季度原件和期末日期', entry='ETF', objects=['510300.SH'],
            intent='解释变化', research_mode='current', questions=[dict(id='q1',question='原件记载何种期间',kind='fact')])
        self.task['synthetic'] = True; self.task['messages'][0]['synthetic'] = True
        params = dict(codes='510300.SH',functionpara={},beginrDate='2026-07-01',
            endrDate='2026-07-31',outputpara=','.join(field+':Y' for field in IFIND_REPORT_FIELDS))
        row = dict(reportDate='2026-07-21',thscode='510300.SH',secName='合成测试基金',ctime=None,
            reportTitle='合成测试基金2026年第2季度报告',pdfURL='https://static.cninfo.com.cn/finalpage/synthetic-quarterly.pdf',seq='synthetic_1')
        collector = EvidenceCollector(offline=False)
        def catalogue(task, request, timeout, event):
            event.update(transport_isolated=True,network_request_started=True,network_worker_reaped=True)
            return dict(errorcode=0,tables=[copy.deepcopy(row)])
        def original(task, url, timeout, event, source_id=None):
            event.update(transport_isolated=True,network_request_started=True,network_worker_reaped=True)
            return payload
        with patch.object(collector,'_http_json',side_effect=catalogue),patch.object(collector,'_download_original',side_effect=original):
            catalog = collector.external(self.task,'ifind','report_query',params,dict(access_token='FAKE_TEST_ACCESS'))
            self.task['source_records'].append(catalog)
            self.record = collector.external(self.task,'ifind','filing_original',dict(catalog_id=catalog['id'],source_id='synthetic_1'),{})
            self.task['source_records'].append(self.record)
        self.event = self.task['ledger']['dispatches'][self.record['acquisition']['dispatch_index']]
        self.assertEqual(len(self.record['snapshot']['material']['parts']),2)
        self.assertEqual(self.task['ledger']['used']['model_calls'],0)

    def tearDown(self):
        self.assertEqual(hashlib.sha256(ORIGINAL.read_bytes()).hexdigest(), self.digest)

    def spec(self):
        part = self.record['snapshot']['material']['parts'][0]
        return dict(id='quarterly_original', artifact_id=self.record['id'], object='510300.SH',
            record_locator=dict(part=part['locator'], period_part=part['locator'], period_quote=OBSERVATION_QUOTE),
            text='2026 年第 2 季度报告', period='2026-06-30', nature='已实现事实', unit='不适用',
            published_at=None, availability=current_original_reference(self.task, self.record),
            publication=dict(at=None, basis_ref=None, precision='未知', timezone='未知', certainty='未知',
                basis='首次公开时间未知，当前版本另由实际下载账本核对'), question_ids=['q1'],
            review=dict(question_ids=['q1'], allowed_natures=['已实现事实'], conditions=['只核对本原件及明确报告日期'],
                reason='原件、主体、页段和观测日期对应', temporal_use='current_version'))

    def candidate(self, spec=None):
        return candidate_evidence(self.task, self.record, spec or self.spec())

    def test_download_reference_preserves_unknown_first_publication(self):
        ref = current_original_reference(self.task, self.record)
        self.assertEqual(ref['kind'], 'current_original')
        self.assertFalse(ref['historical_availability_approved'])
        self.assertFalse(ref['event_causality_approved'])
        self.assertIsNone(self.record['directory']['published_at'])
        self.assertFalse(self.task['evidence'])

    def test_real_bytes_can_pass_current_source_and_separate_applicability(self):
        spec = self.spec(); item = self.candidate(spec)
        self.assertEqual(item['source_check'], '通过')
        self.assertEqual(item['applicability_check'], '待核验')
        item = ResearchAnalyzer().assess(self.task, item, spec['review'], self.task['source_records'])
        validate_evidence(item, self.task)
        self.assertEqual(item['applicability_review']['temporal_use'], 'current_version')
        self.assertTrue(any('事件因果' in text for text in item['applicability_review']['conditions']))

    def test_unknown_time_is_not_rewritten_as_download_time(self):
        spec = self.spec(); spec['published_at'] = self.record['obtained_at']
        with self.assertRaises(ContractError): self.candidate(spec)

    def test_original_import_without_actual_receipt_has_no_exception(self):
        spec = self.spec()
        self.record.pop('acquisition')
        self.assertIsNone(current_original_reference(self.task, self.record))
        with self.assertRaises(ContractError): validate_current_snapshot(spec, self.task)

    def test_historical_task_cannot_use_current_original(self):
        spec = self.spec(); self.task['research_mode'] = 'historical'
        self.assertIsNone(current_original_reference(self.task, self.record))
        with self.assertRaises(ContractError): self.candidate(spec)

    def test_causal_or_historical_review_scope_is_rejected(self):
        for use in ('historical_availability', 'event_causality', None):
            spec = self.spec(); item = self.candidate(spec); spec['review']['temporal_use'] = use
            with self.subTest(use=use), self.assertRaises(ContractError):
                ResearchAnalyzer().assess(self.task, item, spec['review'], self.task['source_records'])

    def test_cross_task_or_version_receipt_is_rejected(self):
        for key, value in [('task_id', 'foreign'), ('research_version', 99)]:
            task = copy.deepcopy(self.task); record = next(r for r in task['source_records'] if r['id'] == self.record['id'])
            record['acquisition'][key] = value
            with self.subTest(key=key): self.assertIsNone(current_original_reference(task, record))

    def test_cancelled_or_unreaped_transport_cannot_mint_availability(self):
        for field, value in [('network_worker_reaped', False), ('network_request_started', False),
                             ('transport_isolated', False), ('status', '已取消')]:
            with self.subTest(field=field):
                task = copy.deepcopy(self.task); record = next(r for r in task['source_records'] if r['id'] == self.record['id'])
                record.pop('acquisition'); event = task['ledger']['dispatches'][self.record['acquisition']['dispatch_index']]
                event[field] = value; _record_current_original(task, record, event)
                self.assertNotIn('acquisition', record)

    def test_changed_snapshot_or_original_hash_invalidates_receipt(self):
        for field in ('hash', 'obtained_at'):
            task = copy.deepcopy(self.task); record = next(r for r in task['source_records'] if r['id'] == self.record['id'])
            record[field] = 'changed'
            with self.subTest(field=field): self.assertIsNone(current_original_reference(task, record))

    def test_changed_bytes_cannot_be_approved_by_rehashing_snapshot(self):
        self.record['snapshot']['material']['original_base64'] = 'dGFtcGVyZWQ='
        self.record['hash'] = fingerprint(self.record['snapshot'])
        self.assertIsNone(current_original_reference(self.task, self.record))

    def test_explicit_observation_date_and_literal_anchor_are_required(self):
        for changes in (dict(period_part='未读页'), dict(period_quote='2026年6月30日'), dict(period_quote='')):
            spec = self.spec(); spec['record_locator'].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ContractError): self.candidate(spec)

    def test_mismatched_observation_date_is_rejected(self):
        spec = self.spec(); spec['period'] = '2026-07-01'
        with self.assertRaises(ContractError): self.candidate(spec)

    def test_report_sent_date_is_not_observation_date(self):
        spec = self.spec(); spec['period'] = '2026-07-21'
        spec['record_locator']['period_quote'] = SENT_QUOTE
        with self.assertRaises(ContractError): self.candidate(spec)

    def test_current_version_does_not_ignore_explicit_superseding_filing(self):
        spec = self.spec(); item = self.candidate(spec)
        replacement = dict(task_id=self.task['id'], research_version=self.task['version'],
            directory=dict(relation=dict(target=self.record['id'], kind='替代')))
        with self.assertRaises(ContractError):
            ResearchAnalyzer().assess(self.task, item, spec['review'], [*self.task['source_records'], replacement])

    def test_missing_time_does_not_allow_numeric_or_unit_fabrication(self):
        spec = self.spec(); spec.update(raw_value=900, value=900, raw_token='2', raw_unit='元', unit='元', conversion_factor=1)
        with self.assertRaises(ContractError): self.candidate(spec)

    def test_tools_expose_actual_reference_without_automatic_financial_approval(self):
        before = fingerprint(self.task)
        sources = evidence_contract(self.task)['sources']
        source = next(s for s in sources if s['artifact_id'] == self.record['id'])
        self.assertEqual(source['current_availability_reference'], self.spec()['availability'])
        self.assertFalse(source['current_original_rule']['source_or_financial_approval'])
        self.assertEqual(fingerprint(self.task), before)

    def reading(self):
        part = self.record['snapshot']['material']['parts'][0]
        return read(self.task, [dict(artifact_id=self.record['id'], snapshot_hash=self.record['hash'],
            queries=[], parts=[dict(part=part['locator'], start=0, end=len(part['text']))],
            reason='核对原件标题及明确报告日期', question_ids=['q1'])], binding(self.task), [self.record['id']])

    def test_current_original_source_reading_does_not_require_fake_publication(self):
        bundle = self.reading()
        validate_read_specs(self.task, [self.spec()], bundle)
        self.assertTrue(bundle['coverage'][0]['omitted_characters'] > 0)

    def test_unread_observation_anchor_is_rejected(self):
        bundle = self.reading(); spec = self.spec()
        spec['record_locator']['period_part'] = '第2页'
        with self.assertRaises(ContractError): validate_read_specs(self.task, [spec], bundle)

    def test_structured_model_container_has_explicit_current_original_variant(self):
        bundle = self.reading()
        schema = material_extraction_output_schema(self.task, bundle['views'])
        self.assertIsNotNone(schema)
        spec = self.spec()
        spec.pop('published_at'); spec.pop('publication'); spec['publication_mode'] = 'unknown_current_original'
        spec['review']['evidence_need_texts'] = []
        decoded = decode_material_extraction_output(dict(version=1, evidence_specs=[spec]), schema)
        self.assertEqual(decoded['evidence_specs'][0]['availability'], spec['availability'])
        self.assertIsNone(decoded['evidence_specs'][0]['published_at'])

    def test_structured_container_cannot_approve_event_causality(self):
        bundle = self.reading(); schema = material_extraction_output_schema(self.task, bundle['views'])
        spec = self.spec(); spec['review'].update(evidence_need_texts=[], temporal_use='event_causality')
        spec.pop('published_at'); spec.pop('publication'); spec['publication_mode'] = 'unknown_current_original'
        with self.assertRaises(ValueError):
            decode_material_extraction_output(dict(version=1, evidence_specs=[spec]), schema)


if __name__ == '__main__': unittest.main()
