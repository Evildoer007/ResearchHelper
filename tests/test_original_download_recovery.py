"""Recover saved original bytes after a parser failure, without another send."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import ROOT
from tests.test_current_original_availability import ORIGINAL, OBSERVATION_QUOTE
from tests.test_official_release_channel import CATALOG
from tests.test_official_original_availability import BODY
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import EvidenceCollector, IFIND_REPORT_FIELDS, candidate_evidence, current_original_reference
from researchhelper.official_sources import release_choices


class OriginalDownloadRecoveryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='download-recovery-', dir=ROOT / 'result/acceptance')
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(Path(folder.name)), ROOT, offline=True)
        self.addCleanup(self.agent.close_runtime)
        guard = patch('socket.socket.connect', side_effect=AssertionError('No external request'))
        guard.start()
        self.addCleanup(guard.stop)
        self.collector = EvidenceCollector(offline=False)
        self.collector.original_keeper = self.agent.archive.retain_original

    def failed_ifind(self, *, limited=False, success=False, parse_check=None):
        task = self.agent.create('核对合成基金季度原件和期末日期', entry='ETF', objects=['510300.SH'],
            intent='解释变化', research_mode='current', questions=[dict(id='q1', question='原件记载何种期间', kind='fact')])
        task['synthetic'] = True
        task['messages'][0]['synthetic'] = True
        params = dict(codes='510300.SH', functionpara={}, beginrDate='2026-07-01', endrDate='2026-07-31',
                      outputpara=','.join(field+':Y' for field in IFIND_REPORT_FIELDS))
        row = dict(reportDate='2026-07-21', thscode='510300.SH', secName='合成测试基金', ctime=None,
            reportTitle='合成测试基金2026年第2季度报告',
            pdfURL='https://static.cninfo.com.cn/finalpage/synthetic-quarterly.pdf', seq='synthetic_1')

        def catalogue(task, request, timeout, event):
            event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True)
            return dict(errorcode=0, tables=[copy.deepcopy(row)])

        def original(task, url, timeout, event, source_id=None):
            event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True)
            return ORIGINAL.read_bytes()

        with patch.object(self.collector, '_http_json', side_effect=catalogue):
            catalog = self.collector.external(task, 'ifind', 'report_query', params, dict(access_token='FAKE_TEST_ACCESS'))
        task['source_records'].append(catalog)
        selected = dict(catalog_id=catalog['id'], source_id='synthetic_1')
        with patch.object(self.collector, '_download_original', side_effect=original):
            if success:
                record = self.collector.external(task, 'ifind', 'filing_original', selected, {})
                task['source_records'].append(record)
            elif limited:
                with patch('researchhelper.evidence.extract_material', side_effect=ContractError('Synthetic parse limitation')):
                    record = self.collector.external(task, 'ifind', 'filing_original', selected, {})
                self.assertEqual(record['status'], '解析受限')
                task['source_records'].append(record)
            else:
                def parser_exit(*args):
                    if parse_check is not None:
                        parse_check(args[0])
                    raise RuntimeError('Synthetic parser exit')
                with patch.object(self.collector, '_original_record', side_effect=parser_exit):
                    with self.assertRaises(ContractError):
                        self.collector.external(task, 'ifind', 'filing_original', selected, {})
        self.agent.archive.save(task)
        original_id = self.agent.archive.retained_originals(task)[0]['id']
        self.assertEqual(task['ledger']['used']['data_requests'], 2)
        return task, original_id

    def recovered(self, task, original_id):
        result = self.agent.retry_original(task['id'], original_id)
        record = result['source_records'][-1]
        self.assertEqual(record['status'], '成功待核验')
        self.assertEqual(result['ledger'], task['ledger'])
        self.assertEqual(record.get('operation'), 'filing_original')
        reference = current_original_reference(result, record)
        self.assertIsNotNone(reference)
        self.assertFalse(reference['historical_availability_approved'])
        self.assertFalse(reference['event_causality_approved'])
        return result, record, reference

    def test_download_checkpoint_is_visible_before_the_parser_is_called(self):
        self.collector.journal = lambda task: self.agent.archive.save(task)
        seen = []

        def inspect_checkpoint(task):
            fresh = ResearchArchive(self.agent.archive.root).get(task['id'])
            receipt = fresh['ledger']['dispatches'][-1].get('download_receipt')
            self.assertIsNotNone(receipt)
            raw, payload = self.agent.archive.original(fresh, receipt['original_id'])
            self.assertEqual(raw['sha256'], receipt['original_hash'])
            self.assertEqual(payload, ORIGINAL.read_bytes())
            seen.append(receipt['original_id'])

        _, original_id = self.failed_ifind(parse_check=inspect_checkpoint)
        self.assertEqual(seen, [original_id])

    def test_normal_parse_keeps_both_download_and_current_availability(self):
        task, _ = self.failed_ifind(success=True)
        record = task['source_records'][-1]
        receipt = task['ledger']['dispatches'][-1]['download_receipt']
        self.assertEqual(record['download_receipt'], receipt)
        self.assertEqual(record['acquisition']['obtained_at'], receipt['obtained_at'])
        reference = current_original_reference(task, record)
        self.assertIsNotNone(reference)
        self.assertEqual(reference['obtained_at'], receipt['obtained_at'])

    def test_ifind_parser_exit_recovers_the_saved_download_without_a_new_send(self):
        task, original_id = self.failed_ifind()
        result, record, reference = self.recovered(task, original_id)
        self.assertEqual(len(result['source_records']), 2)
        self.assertEqual(reference['obtained_at'], task['ledger']['dispatches'][-1]['download_receipt']['obtained_at'])
        part = record['snapshot']['material']['parts'][0]
        spec = dict(id='recovered_original', artifact_id=record['id'], object='510300.SH',
            text='2026 年第 2 季度报告', period='2026-06-30', nature='已实现事实', unit='不适用', published_at=None,
            record_locator=dict(part=part['locator'], period_part=part['locator'], period_quote=OBSERVATION_QUOTE),
            availability=reference, publication=dict(at=None, basis_ref=None, precision='未知', timezone='未知',
                certainty='未知', basis='首次公开时间未知，仅核对当前取得原件'), question_ids=['q1'])
        evidence = candidate_evidence(result, record, spec)
        self.assertEqual(evidence['source_check'], '通过')
        self.assertEqual(evidence['applicability_check'], '待核验')

    def test_limited_parse_record_does_not_keep_its_failure_status_after_recovery(self):
        task, original_id = self.failed_ifind(limited=True)
        previous = copy.deepcopy(task['source_records'][-1])
        result, record, _ = self.recovered(task, original_id)
        self.assertEqual(result['source_records'][-2], previous)
        self.assertEqual(record['stages']['parse'], '已解析')
        self.assertEqual(record['stages']['source_check'], '待核验')

    def test_download_receipt_survives_archive_reload_before_retry(self):
        task, original_id = self.failed_ifind()
        self.agent.close_runtime()
        self.agent = ResearchAgent(ResearchArchive(self.agent.archive.root), ROOT, offline=True)
        self.addCleanup(self.agent.close_runtime)
        loaded = self.agent.archive.get(task['id'])
        self.assertEqual(loaded['ledger'], task['ledger'])
        self.recovered(loaded, original_id)

    def test_missing_download_receipt_does_not_turn_retained_bytes_into_current_evidence(self):
        task, original_id = self.failed_ifind()
        task['ledger']['dispatches'][-1].pop('download_receipt', None)
        self.agent.archive.save(task)
        result = self.agent.retry_original(task['id'], original_id)
        self.assertEqual(result['ledger'], task['ledger'])
        self.assertIsNone(current_original_reference(result, result['source_records'][-1]))

    def test_corrupted_receipt_or_transport_flags_cannot_create_current_evidence(self):
        cases = [('original_hash', '0'*64), ('byte_count', True), ('dispatch_index', True),
                 ('research_version', 2), ('original_id', 'rh_original_foreign'),
                 ('source', []), ('parameters', {}), ('obtained_at', '2999-01-01T00:00:00+00:00')]
        for field, value in cases:
            with self.subTest(field=field):
                task, original_id = self.failed_ifind()
                task['ledger']['dispatches'][-1]['download_receipt'][field] = value
                self.agent.archive.save(task)
                result = self.agent.retry_original(task['id'], original_id)
                self.assertIsNone(current_original_reference(result, result['source_records'][-1]))
                self.assertEqual(result['ledger'], task['ledger'])
        for flag in ('network_request_started', 'network_worker_reaped', 'transport_isolated'):
            with self.subTest(flag=flag):
                task, original_id = self.failed_ifind()
                task['ledger']['dispatches'][-1][flag] = False
                self.agent.archive.save(task)
                result = self.agent.retry_original(task['id'], original_id)
                self.assertIsNone(current_original_reference(result, result['source_records'][-1]))

    def test_recovery_reuses_original_spending_across_execution_batches(self):
        task, original_id = self.failed_ifind()
        task['batch'] += 1
        self.agent.archive.save(task)
        self.recovered(task, original_id)

    def test_current_original_reference_remains_unavailable_in_historical_mode(self):
        task, original_id = self.failed_ifind()
        task['research_mode'] = 'historical'
        self.agent.archive.save(task)
        result = self.agent.retry_original(task['id'], original_id)
        self.assertEqual(result['source_records'][-1].get('operation'), 'filing_original')
        self.assertIsNone(current_original_reference(result, result['source_records'][-1]))
        self.assertEqual(result['ledger'], task['ledger'])

    def test_recovered_source_restores_only_current_exact_request_purposes(self):
        task, original_id = self.failed_ifind()
        receipt = task['ledger']['dispatches'][-1]['download_receipt']
        need = dict(id='original_for_q1', source='ifind', operation='filing_original',
                    parameters=receipt['parameters'], question_ids=['q1'], selection_reason='核对季度报告的原文和日期')
        binding = dict(task_id=task['id'], research_version=task['version'], input_hash=task['input_hash'])
        task['draft_source_attempts'] = [dict(binding=binding, encoded=need, status='未取得')]
        self.agent.archive.save(task)
        _, record, _ = self.recovered(task, original_id)
        self.assertEqual(record['data_need_ids'], ['original_for_q1'])
        self.assertEqual(record['question_ids'], ['q1'])
        self.assertEqual(record['request_purposes'], [dict(data_need_id='original_for_q1', question_ids=['q1'],
                                                        selection_reason=need['selection_reason'])])

    def test_old_or_other_request_purposes_are_not_attached_to_the_recovered_source(self):
        task, original_id = self.failed_ifind()
        receipt = task['ledger']['dispatches'][-1]['download_receipt']
        need = dict(id='unrelated', source='ifind', operation='filing_original', parameters=receipt['parameters'],
                    question_ids=['q1'], selection_reason='不应关联')
        task['draft_source_attempts'] = [dict(binding=dict(task_id=task['id'], research_version=2,
            input_hash=task['input_hash']), encoded=need, status='未取得')]
        self.agent.archive.save(task)
        _, record, _ = self.recovered(task, original_id)
        self.assertFalse(record.get('request_purposes'))
        self.assertFalse(record.get('data_need_ids'))

    def failed_official(self):
        task = self.agent.create('核查合成样本指标', entry='宏观', intent='解释变化', research_mode='current',
            objects=['合成样本指标'], questions=[dict(id='q1', question='原件记载何种指标', kind='fact')])

        task['synthetic'] = True
        task['messages'][0]['synthetic'] = True

        def returned(task, url, started, timeout, event, **kwargs):
            event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True)
            return CATALOG

        with patch('researchhelper.source_transport.official_html', side_effect=returned):
            catalog = self.collector.external(task, 'official', 'release_catalog', dict(channel='nbs_release', page='0'), {})
        task['source_records'].append(catalog)
        choice = release_choices(task)[0]
        params = {key:choice[key] for key in ('catalog_id', 'release_id')}

        def original(task, url, started, timeout, event, **kwargs):
            event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True)
            return BODY

        with patch('researchhelper.source_transport.official_html', side_effect=original), \
             patch.object(self.collector, '_parse_official_material', side_effect=ContractError('Synthetic parser exit')):
            with self.assertRaises(ContractError):
                self.collector.external(task, 'official', 'release_original', params, {})
        self.agent.archive.save(task)
        raw = self.agent.archive.retained_originals(task)
        original_id = next(row['id'] for row in raw if row['name'].endswith('.html') and row['name'] != 'nbs-catalogue-page-0.html')
        self.assertEqual(task['ledger']['dispatches'][-1]['status'], '原件已获取，研究未完成')
        return task, original_id

    def test_parsed_official_catalogue_is_not_reported_as_a_pending_material(self):
        task, original_id = self.failed_official()
        catalogue = task['source_records'][0]
        before = copy.deepcopy(task)
        pending = self.agent.archive.retained_originals(task, pending_only=True)
        self.assertNotIn('nbs-catalogue-page-0.html', [row['name'] for row in pending])
        self.assertIn(original_id, [row['id'] for row in pending])
        originals = self.agent.archive.retained_originals(task)
        raw = next(row for row in originals if row['name'] == 'nbs-catalogue-page-0.html')
        self.assertEqual(raw['status'], '原始目录已归档')
        self.assertEqual(raw['artifact_id'], catalogue['id'])
        _, payload = self.agent.archive.original(task, raw['id'])
        self.assertEqual(payload, CATALOG)
        self.assertEqual(task, before)
        self.assertEqual(self.agent.archive.get(task['id']), before)
        self.assertIsNone(current_original_reference(task, catalogue))
        self.assertFalse(task['evidence'])

    def test_catalogue_capture_does_not_hide_a_separately_named_user_material(self):
        task, _ = self.failed_official()
        alias = self.agent.archive.retain_original(task, 'material', 'user-copy.html', CATALOG)
        pending = self.agent.archive.retained_originals(task, pending_only=True)
        self.assertIn(alias, [row['id'] for row in pending])

    def test_changed_catalogue_scope_or_snapshot_does_not_clear_the_raw_pending_state(self):
        cases = [('hash', 'changed'), ('request_hash', 'changed'), ('id', 'foreign'),
                 ('task_id', 'other-task'), ('research_version', 2), ('parameters', {})]
        for field, value in cases:
            task, _ = self.failed_official()
            task['source_records'][0][field] = value
            with self.subTest(field=field):
                pending = self.agent.archive.retained_originals(task, pending_only=True)
                self.assertIn('nbs-catalogue-page-0.html', [row['name'] for row in pending])

    def test_an_existing_api_capture_link_has_the_same_directory_readback(self):
        task, _ = self.failed_official()
        raw = next(row for row in self.agent.archive.retained_originals(task)
                   if row['name'] == 'nbs-catalogue-page-0.html')
        catalogue = task['source_records'][0]
        self.agent.archive.link_material_copy(task, raw['id'], catalogue['id'])
        pending = self.agent.archive.retained_originals(task, pending_only=True)
        self.assertNotIn(raw['id'], [row['id'] for row in pending])
        result = next(row for row in self.agent.archive.retained_originals(task) if row['id'] == raw['id'])
        self.assertEqual(result['status'], '原始目录已归档')

    def test_invalid_original_bytes_cannot_clear_a_catalogue_recovery_prompt(self):
        for field, value in [('original_sha256', '0'*64), ('original_base64', 'not base64')]:
            task, _ = self.failed_official()
            catalogue = task['source_records'][0]
            catalogue['snapshot'][field] = value
            catalogue['hash'] = fingerprint(catalogue['snapshot'])
            catalogue['id'] = 'rh_official_' + fingerprint([task['id'], task['version'],
                catalogue['request_hash'], catalogue['hash']])[:24]
            with self.subTest(field=field):
                pending = self.agent.archive.retained_originals(task, pending_only=True)
                self.assertIn('nbs-catalogue-page-0.html', [row['name'] for row in pending])

    def test_official_html_parser_failure_recovers_original_source_and_download_time(self):
        task, original_id = self.failed_official()
        result = self.agent.retry_original(task['id'], original_id)
        record = result['source_records'][-1]
        self.assertEqual((record.get('source'), record.get('operation')), ('official', 'release_original'))
        reference = current_original_reference(result, record)
        self.assertIsNotNone(reference)
        self.assertEqual(reference['obtained_at'], task['ledger']['dispatches'][-1]['download_receipt']['obtained_at'])
        self.assertEqual(result['ledger'], task['ledger'])
