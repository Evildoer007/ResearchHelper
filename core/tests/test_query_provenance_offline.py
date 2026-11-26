"""Synthetic acquisition bindings only; no real provider or model is called."""

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lxml import html

from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, METHOD_REGISTRY, charge, fingerprint, missing_evidence_requirements
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import (candidate_evidence, current_snapshot_reference, direct_code_reference,
                                     snapshot_record, _record_current_acquisition)
from researchhelper.planning import normalize_questions, question_specs, validate_question_revision
from researchhelper.provenance import (QUERY_ANSWER, bind_query_references, query_candidates,
    query_projection, query_reference)
from researchhelper.reasoning import analysis_contract, analysis_numeric_review, validate_analysis_output
from researchhelper.reports import compose

ROOT = Path(__file__).resolve().parents[2]
TRACE_NEED = "核对本次查询来源、操作、参数与响应身份"
FACT_NEED = "核对该基金对应交易日的实际收盘原值"


class QueryProvenanceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='query-lineage-', dir=ROOT / 'result/acceptance')
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True)
        guard = patch('socket.socket.connect', side_effect=AssertionError('No network'))
        guard.start(); self.addCleanup(guard.stop)
        for clock in ('researchhelper.engine.now', 'researchhelper.evidence.now'):
            fixed = patch(clock, return_value='2026-10-01T18:00:00+08:00')
            fixed.start()
            self.addCleanup(fixed.stop)

    def acquired(self, pure=False):
        question = {'id': 'q1', 'question': '核对实际价格和本次查询留痕', 'kind': 'fact',
            'evidence_needs': [TRACE_NEED] if pure else [FACT_NEED, TRACE_NEED],
            'evidence_need_scopes': {TRACE_NEED: 'query_provenance'}}
        task = self.agent.create(question['question'], entry='ETF', objects=['510300.SH'],
            research_mode='current', questions=[question])
        params = {'codes': '510300.SH', 'indicators': 'close', 'startdate': '2026-09-30',
                  'enddate': '2026-09-30', 'functionpara': {'CPS': '1', 'Currency': 'RMB', 'Fill': 'Omit'}}
        record = snapshot_record(task, 'ifind', 'cmd_history_quotation', params,
            {'errorcode': 0, 'tables': [{'thscode': '510300.SH', 'time': ['2026-09-30'], 'table': {'close': [4.5]}}]})
        # Synthetic transport fixture, not a real-world acquisition claim.
        charge(task['ledger'], 'data_requests')
        event = {'kind': 'data_requests', 'fingerprint': record['request_hash'], 'batch': task['batch'],
            'status': '成功', 'transport_isolated': True, 'network_request_started': True, 'network_worker_reaped': True}
        task['ledger']['dispatches'].append(event)
        _record_current_acquisition(task, record, event)
        record['question_ids'] = ['q1']
        task['source_records'].append(record)
        self.agent.archive.save(task)
        return task, record

    def reference(self, task, record):
        return dict(query_reference(task, record), evidence_need_ids=[fingerprint(TRACE_NEED)])

    def price(self, task, record):
        identity = direct_code_reference(task, record)
        self.assertIsNotNone(identity)
        return {'id': 'price', 'artifact_id': record['id'], 'object': '510300.SH',
            'record_locator': {'table': 0, 'row': 0, 'field': 'close'}, 'period': '2026-09-30',
            'nature': '已实现事实', 'unit': '元', 'raw_unit': '元', 'raw_value': 4.5, 'value': 4.5, 'conversion_factor': 1,
            'published_at': None, 'publication': {'at': None, 'basis_ref': None, 'certainty': '未知',
                'precision': '未知', 'timezone': '未知', 'basis': '首次公开未知，当前取数另有记录'},
            'availability': current_snapshot_reference(task, record), 'question_ids': ['q1'],
            'direct_code_reference': identity,
            'review': {'question_ids': ['q1'], 'allowed_natures': ['已实现事实'],
                'conditions': ['当前响应的收盘原值，不是净值或历史当时版本'], 'reason': '同对象原字段原值与日期一致',
                'evidence_need_ids': {'q1': [fingerprint(FACT_NEED)]}}}

    def test_mixed_answer_preserves_price_and_separate_query_receipt_in_export(self):
        task, record = self.acquired()
        done = self.agent.start(task['id'], {'evidence_specs': [self.price(task, record)],
            'answers': {'q1': {'answer': '本次响应记录该交易日收盘原值4.5元，查询范围见留痕。',
                'refs': ['price'], 'calculation_refs': [], 'provenance_refs': [self.reference(task, record)]}}})
        self.assertEqual(done['status'], '已完成', done.get('gaps'))
        question = done['questions'][0]
        self.assertFalse(question['provenance_integrity']['financial_approval'])
        self.assertEqual(question['provenance_records'][0]['parameters'], record['parameters'])
        self.assertEqual(question['provenance_records'][0]['response_fields'], ['close'])
        self.assertEqual(len(done['evidence']), 1)
        self.assertEqual(done['ledger']['used']['data_requests'], 1)
        self.assertEqual(done['ledger']['used']['model_calls'], 0)
        report = self.agent.export(done['id'])
        page = html.document_fromstring(report['html'])
        link, = page.xpath("//main//a[@href='#query-q1']")
        self.assertEqual(link.text_content(), '查询口径')
        detail = page.get_element_by_id('query-q1').text_content()
        self.assertIn('functionpara.CPS', detail)
        self.assertIn(record['request_hash'], detail)
        self.assertNotIn('provenance_records', report['html'])
        reopened = ResearchAgent(self.agent.archive, ROOT, offline=True)
        self.assertEqual(reopened.export(done['id'])['digest'], report['digest'])

    def test_query_alone_cannot_cover_missing_financial_price(self):
        task, record = self.acquired()
        refs = [self.reference(task, record)]
        bound = bind_query_references(task, task['questions'][0], refs)
        self.assertEqual(missing_evidence_requirements(task['questions'][0], [], bound['covered_need_ids']),
                         [{'id': fingerprint(FACT_NEED), 'text': FACT_NEED}])
        done = self.agent.start(task['id'], {'answers': {'q1': {'answer': '价格已经核验。', 'refs': [], 'provenance_refs': refs}}})
        self.assertNotEqual(done['status'], '已完成')
        self.assertEqual(done['questions'][0]['status'], '未回答')

    def test_query_only_answer_is_saved_without_fake_financial_evidence(self):
        task, record = self.acquired(pure=True)
        done = self.agent.start(task['id'], {'answers': {'q1': {'answer': QUERY_ANSWER, 'refs': [],
            'calculation_refs': [], 'provenance_refs': [self.reference(task, record)]}}})
        self.assertEqual(done['status'], '已完成', done.get('gaps'))
        self.assertFalse(done['evidence']); self.assertFalse(done['calculations']); self.assertFalse(done['theses'])
        self.assertEqual(compose(done)['questions'][0]['answer'], QUERY_ANSWER)

    def test_query_only_cannot_generate_arbitrary_financial_claim(self):
        task, record = self.acquired(pure=True)
        done = self.agent.start(task['id'], {'answers': {'q1': {'answer': '这只基金值得买入。', 'refs': [],
            'provenance_refs': [self.reference(task, record)]}}})
        self.assertEqual(done['questions'][0]['status'], '未回答')

    def test_retained_facts_cannot_be_reclassified_as_query_needs(self):
        task, _ = self.acquired()
        proposed = question_specs(task)
        proposed[0]['evidence_need_scopes'][FACT_NEED] = 'query_provenance'
        with self.assertRaises(ContractError):
            validate_question_revision(task, proposed)
        self.assertEqual(set(normalize_questions([{'question': '普通问题', 'kind': 'fact'}], '普通问题', '综合研究')[0]),
            {'id', 'question', 'kind', 'required', 'group', 'dependencies', 'evidence_needs', 'method_needs'})

    def test_explicit_scopes_reject_unknown_needs_and_wrong_question_types(self):
        for changes in ({'kind': 'mechanism'}, {'kind': 'comparison'}, {'kind': 'investment'},
                        {'evidence_need_scopes': {'未登记': 'query_provenance'}},
                        {'evidence_need_scopes': {TRACE_NEED: 'financial'}}, {'evidence_need_scopes': {}},
                        {'method_needs': ['observed_price_return_v1']}):
            with self.subTest(changes=changes), self.assertRaises(ContractError):
                normalize_questions([dict(question='核对查询', kind='fact', evidence_needs=[TRACE_NEED],
                    evidence_need_scopes={TRACE_NEED: 'query_provenance'}) | changes], '核对查询', '综合研究')

    def test_source_parameter_snapshot_time_and_cross_task_tampering_is_rejected(self):
        task, record = self.acquired()
        reference = self.reference(task, record)
        for change in ('parameters', 'snapshot', 'time', 'task', 'version', 'transport', 'integer'):
            edited = copy.deepcopy(task); original = edited['source_records'][0]
            if change == 'parameters': original['parameters']['functionpara']['CPS'] = '2'
            elif change == 'snapshot': original['snapshot']['tables'][0]['table']['close'][0] = 9
            elif change == 'time': original['obtained_at'] = '2026-01-01T00:00:00+08:00'
            elif change == 'task': original['task_id'] = 'other-task'
            elif change == 'version': original['research_version'] = 2
            elif change == 'transport': edited['ledger']['dispatches'][0]['network_worker_reaped'] = False
            else: original['research_version'] = True
            with self.subTest(change=change), self.assertRaises(ContractError):
                bind_query_references(edited, edited['questions'][0], [reference])

    def test_imported_or_historical_snapshot_does_not_mint_query_reference(self):
        task, record = self.acquired()
        imported = snapshot_record(task, record['source'], record['operation'], record['parameters'], record['snapshot'])
        task['source_records'] = [imported]
        self.assertEqual(query_candidates(task), [])
        with self.assertRaises(ContractError): query_projection(task, imported)
        task, record = self.acquired()
        task['research_mode'] = 'historical'
        with self.assertRaises(ContractError): query_reference(task, record)

    def test_reference_cannot_cover_untyped_needs_or_repeat_sources(self):
        task, record = self.acquired(); question = task['questions'][0]
        ref = self.reference(task, record)
        for refs in ([dict(ref, evidence_need_ids=[fingerprint(FACT_NEED)])], [ref, ref],
                     [dict(ref, version=True)], [dict(ref, projection_hash='wrong')]):
            with self.subTest(refs=refs), self.assertRaises(ContractError):
                bind_query_references(task, question, refs)
        source = copy.deepcopy(record); source['question_ids'] = ['other']
        task['source_records'] = [source]
        with self.assertRaises(ContractError): bind_query_references(task, question, [ref])

    def test_financial_excerpt_does_not_self_approve_query_lineage(self):
        task, record = self.acquired(); spec = self.price(task, record)
        spec['review']['evidence_need_ids']['q1'].append(fingerprint(TRACE_NEED))
        item = candidate_evidence(task, record, spec)
        ResearchAnalyzer().assess(task, item, spec['review'], task['source_records'])
        self.assertEqual(missing_evidence_requirements(task['questions'][0], [item]),
                         [{'id': fingerprint(TRACE_NEED), 'text': TRACE_NEED}])

    def test_analysis_contract_and_cache_do_not_accept_mutated_references(self):
        task, record = self.acquired(); question = task['questions'][0]
        spec = self.price(task, record); item = candidate_evidence(task, record, spec)
        ResearchAnalyzer().assess(task, item, spec['review'], task['source_records'])
        task['evidence'] = [item]
        contract = analysis_contract(task)['by_question']['q1']['query_provenance']
        self.assertFalse(contract['financial_approval']); self.assertFalse(contract['historical_availability_approval'])
        self.assertEqual(contract['requirements'][0]['id'], fingerprint(TRACE_NEED))
        answer = {'answer': '本次响应记录收盘价4.5元。', 'refs': ['price'], 'calculation_refs': [],
                  'provenance_refs': [self.reference(task, record)]}
        self.assertTrue(analysis_numeric_review(task, {'answers': {'q1': answer}, 'theses': []})['accepted'])
        answer['provenance_refs'][0]['request_hash'] = 'wrong'
        self.assertFalse(analysis_numeric_review(task, {'answers': {'q1': answer}, 'theses': []})['accepted'])
        with self.assertRaises(ContractError):
            validate_analysis_output(None, task, {'answers': {'q1': dict(answer, provenance_refs='wrong')}, 'theses': []}, 0)

    def test_report_revalidates_retained_query_projection_and_receipt(self):
        task, record = self.acquired(pure=True)
        done = self.agent.start(task['id'], {'answers': {'q1': {'answer': QUERY_ANSWER, 'refs': [],
            'provenance_refs': [self.reference(task, record)]}}})
        for field in ('provenance_records', 'provenance_integrity', 'provenance_refs'):
            changed = copy.deepcopy(done)
            if field == 'provenance_records': changed['questions'][0][field][0]['parameters']['codes'] = 'other'
            elif field == 'provenance_integrity': changed['questions'][0][field]['financial_approval'] = True
            else: changed['questions'][0][field] = []
            with self.subTest(field=field), self.assertRaises(ContractError): compose(changed)

    def test_method_scope_projects_registered_limits_without_causing_approval(self):
        task, record = self.acquired(); spec = self.price(task, record)
        item = candidate_evidence(task, record, spec)
        ResearchAnalyzer().assess(task, item, spec['review'], task['source_records'])
        task['evidence'] = [item]
        # Only exercise the metadata projection here, not an execution receipt.
        task['calculations'] = [{'id': 'calc', 'method': 'observed_price_return_v1',
                                 'input_refs': ['price'], 'question_ids': ['q1']}]
        scope = analysis_contract(task)['by_question']['q1']['calculations'][0]['method_scope']
        self.assertEqual(scope['limitations'], METHOD_REGISTRY['observed_price_return_v1']['limitations'])
        self.assertFalse(scope['scope_is_causal_or_investment_approval'])
        self.assertIn('不是总收益', scope['limitations'])
