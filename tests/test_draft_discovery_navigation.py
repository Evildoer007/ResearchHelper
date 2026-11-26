"""Candidate retrieval finishes its query, then follows the actual research need.

Public provider responses are read unchanged. All model and transport objects
below are explicit local substitutes; sockets are denied during execution.
"""
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT, test_object_discovery_contract as fixture
from researchhelper.contracts import fingerprint
from researchhelper.draft_sources import acquisition_coverage, observations
from researchhelper.object_discovery import retained_discovery_results
from researchhelper.planning import input_fingerprint

SOURCE = ROOT / 'result/acceptance/source-matrix-20261031/current/sources.json.gz'


def retained_inputs():
    with gzip.open(SOURCE, 'rt') as stream:
        records = json.load(stream)
    selected = [row for row in records if row['operation'] == 'smart_stock_picking']
    selected.append(next(row for row in records if row['operation'] == 'cmd_history_quotation'
                         and row['parameters']['codes'] == '600519.SH,002594.SZ'))
    selected.append(next(row for row in records if row['operation']=='get_limit_policy'))
    return [{key:deepcopy(row[key]) for key in ('operation', 'parameters', 'snapshot')} for row in selected]


def run_navigation_case(folder, resources, inputs, *, reader=None, query_only=False, failed_quote=False):
    """Portable test scenario, also run from shipped code with no repo imports."""
    from copy import deepcopy
    import json
    from types import SimpleNamespace
    from researchhelper.archive import ResearchArchive
    from researchhelper.contracts import ContractError, canonical, charge, now, fingerprint
    from researchhelper.engine import ResearchAgent
    from researchhelper.evidence import snapshot_record, _record_current_acquisition
    from researchhelper.planning import input_fingerprint
    phases, operations, contexts = [], [], []
    discovery, quotation, policy = inputs
    topic = discovery['parameters']['searchstring']
    question = ('列出'+topic+'的本次检索候选，不分析投资价值。' if query_only else
        '分别检索'+topic+'候选，并核对贵州茅台600519.SH与比亚迪002594.SZ在2026年10月29日至30日行情，分开形成研究逻辑，不视为同一行业样本。')

    def transport(task, provider, operation, params, credentials):
        assert provider == 'ifind' and not credentials
        original = next((row for row in inputs if row['operation'] == operation and row['parameters'] == params),None)
        if original is None:
            raise ContractError('本次留存资料不含该元数据响应，未发起网络请求。')
        payload = deepcopy(original['snapshot'])
        if failed_quote and operation == 'cmd_history_quotation':
            payload = dict(errorcode=-1, errmsg='Controlled unavailable price response', tables=[])
        charge(task['ledger'], 'data_requests', 1)
        record = snapshot_record(task, provider, operation, params, payload)
        event = dict(kind='data_requests', source=provider, operation=operation,
            fingerprint=record['request_hash'], batch=task['batch'], time=now(),
            status='成功' if record['status']=='成功待核验' else '失败', transport_isolated=True,
            network_request_started=True, network_worker_reaped=True, synthetic_test_transport=True)
        task['ledger']['dispatches'].append(event)
        _record_current_acquisition(task, record, event)
        operations.append(operation)
        return record

    def reply(task, messages, *, phase, delivery=False, **options):
        phases.append(phase);charge(task['ledger'], 'model_calls', 1, delivery=delivery)
        payload = json.loads(messages[-1]['content']);contexts.append(deepcopy(payload))
        if phase == 'draft_plan':
            step = phases.count('draft_plan')
            selected = discovery if step==1 else quotation if step==2 and not query_only else None
            requests = [] if selected is None else [dict(id='candidates' if step==1 else 'prices',
                source='ifind', operation=selected['operation'], parameters=deepcopy(selected['parameters']),
                question_ids=[task['questions'][0]['id']], selection_reason='按原问题分别取得候选目录或明确标的的行情。')]
            return canonical(dict(data_requests=requests))
        from researchhelper.draft_sources import observations
        refs = [row['id'] for row in observations(task)]
        return canonical(dict(title='候选范围与独立行情观察',
            conclusion='候选目录与明确标的的行情分别回答研究问题，检索返回不代表行业归属或完整样本。',
            logics=[dict(title='候选范围', analysis='目录仅说明本次检索返回的证券，业务关联需要另有依据，不从检索名称推断行业归属。'),
                dict(title='使用边界' if query_only else '独立行情观察', analysis='目录不证明行业归属或完整样本。' if query_only else '按实际取得的交易日和字段展示价格窗口；目录不作为这两个标的的行业归属依据。',data_refs=refs)],
            risks=['候选范围不保证完整，短期行情不能证明因果关系或未来回报。']))

    archive = ResearchArchive(folder)
    agent = ResearchAgent(archive, resources, offline=False, model=SimpleNamespace(reason=reply),
        source_credentials={}, require_argument_support=True, draft_first=True)
    agent.collector.external = transport
    try:
        task = agent.create(question, entry='行业' if query_only else '混合',
            objects=[] if query_only else ['600519.SH', '002594.SZ'], research_mode='current',
            limits=dict(model_calls=4, data_requests=100, seconds=600))
        task['synthetic'] = True
        if reader is not None:
            task['draft_source_reader_version'] = reader
        if query_only:
            task['questions'][0].update(kind='fact', evidence_needs=['检索返回'],
                evidence_need_scopes={'检索返回':'query_provenance'}, method_needs=[])
        task['input_hash'] = input_fingerprint(task);archive.save(task)
        result = agent.start(task['id'])
        assert result.get('delivery'), dict(error=result.get('error'),phases=phases,operations=operations,notices=result.get('draft_notices'),nodes=[(row['id'],row['status']) for row in result['nodes']])
        paper = archive.report(result['id'], result['version'])
        assert '<main' in paper['html']
        if not query_only:
            assert '策略逻辑一' in paper['html'] and '策略逻辑二' in paper['html']
        assert not result['draft_acquisition_coverage']['blocks_report']
        return dict(task=result, paper=paper, phases=phases, operations=operations, contexts=contexts)
    finally:
        agent.close_runtime()


class DraftDiscoveryCoverageTests(unittest.TestCase):
    def setUp(self):
        self.control = fixture.ObjectDiscoveryTests();self.control.setUp();self.addCleanup(self.control.doCleanups)
        self.task = self.control.task;self.task['draft_source_reader_version'] = 14
        self.task['input_hash'] = input_fingerprint(self.task)
        self.source = self.control.retained();self.source['data_need_ids'] = ['candidates']
        self.request = dict(id='candidates', source='ifind', operation='smart_stock_picking',
                            parameters=deepcopy(self.control.params), question_ids=['q1'])

    def coverage(self):
        return acquisition_coverage(self.task, [self.request])

    def test_candidate_response_completes_query_without_inventing_numbers(self):
        before = fingerprint(self.task);value = self.coverage()
        self.assertEqual(value['version'], 2)
        self.assertEqual(value['complete_request_ids'], ['candidates'])
        self.assertEqual(value['navigation_request_ids'], ['candidates'])
        self.assertEqual(value['completed_discovery_request_ids'], ['candidates'])
        self.assertFalse(value['needs_followup']);self.assertEqual(observations(self.task), [])
        self.assertEqual(fingerprint(self.task), before)
        for flag in ('research_sufficiency_approved','blocks_report','financial_approval','unit_approval'):
            self.assertFalse(value[flag])
        parsed = retained_discovery_results(self.task)[0]
        self.assertFalse(parsed['industry_membership_verified']);self.assertEqual(parsed['provider_completeness'],'未证实')

    def test_reader_13_keeps_its_original_numeric_coverage(self):
        self.task['draft_source_reader_version'] = 13
        value = self.coverage()
        self.assertEqual(value['version'], 1);self.assertNotIn('navigation_request_ids', value)
        self.assertEqual(value['complete_request_ids'], []);self.assertTrue(value['needs_followup'])

    def test_explicit_fact_query_needs_no_numeric_followup(self):
        self.task['questions'][0].update(kind='fact',evidence_needs=['检索返回'],
            evidence_need_scopes={'检索返回':'query_provenance'}, method_needs=[])
        value=self.coverage();self.assertEqual(value['complete_request_ids'],['candidates'])
        self.assertEqual(value['navigation_request_ids'],[])

    def test_mixed_or_inferential_query_still_has_downstream_work(self):
        question=self.task['questions'][0]
        for kind,needs,scopes,methods in [('inference',['目录'],{'目录':'query_provenance'},[]),
            ('fact',['目录','收入'],{'目录':'query_provenance'},[]),
            ('fact',['目录'],{'目录':'query_provenance'},['raw_change_v1'])]:
            with self.subTest(kind=kind,needs=needs,methods=methods):
                question.update(kind=kind,evidence_needs=needs,evidence_need_scopes=scopes,method_needs=methods)
                self.assertEqual(self.coverage()['navigation_request_ids'],['candidates'])

    def test_foreign_or_changed_records_do_not_complete_query(self):
        original=deepcopy(self.source)
        for change in ('task','version','hash','request','parameters','status','input'):
            with self.subTest(change=change):
                self.source.clear();self.source.update(deepcopy(original))
                if change=='task':self.source['task_id']='foreign'
                elif change=='version':self.source['research_version']+=1
                elif change=='hash':self.source['snapshot']['tables'][0]['table']['股票代码'][0]='600519.SH'
                elif change=='request':self.source['request_hash']='wrong'
                elif change=='parameters':self.source['parameters']['searchstring']='算力产业链股票';self.source['request_hash']=fingerprint(dict(source='ifind',operation='smart_stock_picking',parameters=self.source['parameters']))
                elif change=='status':self.source['status']='失败'
                else:self.source['input_hash']='foreign'
                self.assertEqual(self.coverage()['complete_request_ids'],[])

    def test_invalid_or_failed_discovery_never_becomes_completed_query(self):
        for payload in [dict(errorcode=-1,tables=[]),dict(errorcode=0,tables=[dict(table={'股票代码':['600519.SH']})]),
                        dict(errorcode=0,tables=[dict(table={'股票代码':['600519'],'股票简称':['名称']})])]:
            self.source['snapshot']=payload;self.source['hash']=fingerprint(payload)
            self.assertEqual(self.coverage()['complete_request_ids'],[])

    def test_valid_empty_result_completes_raw_query_without_candidates(self):
        self.source['snapshot']=dict(errorcode=0,tables=[]);self.source['hash']=fingerprint(self.source['snapshot'])
        self.assertEqual(self.coverage()['complete_request_ids'],['candidates'])
        self.assertEqual(observations(self.task),[])
        self.assertTrue(all(not row['candidates'] for row in retained_discovery_results(self.task)))

    def test_financial_semantic_request_keeps_numeric_coverage(self):
        from tests.test_draft_request_fields import semantic_task
        task=semantic_task('600519.SH2025年年报营业收入、归属于母公司所有者的净利润')
        task['draft_source_reader_version']=14
        source=task['source_records'][0];source['data_need_ids']=['financial']
        request=dict(id='financial',source='ifind',operation='smart_stock_picking',parameters=source['parameters'])
        value=acquisition_coverage(task,[request])
        self.assertEqual(value['complete_request_ids'],['financial'])
        self.assertEqual(value['navigation_request_ids'],[])
        self.assertEqual(value['completed_discovery_request_ids'],[])
        self.assertEqual(len(observations(task)),3)

    def test_missing_question_binding_cannot_claim_query_only_scope(self):
        self.request['question_ids']=['unknown']
        self.assertEqual(self.coverage()['navigation_request_ids'],['candidates'])


class DraftDiscoveryWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_hash=hashlib.sha256(SOURCE.read_bytes()).hexdigest();cls.inputs=retained_inputs()

    @classmethod
    def tearDownClass(cls):
        assert hashlib.sha256(SOURCE.read_bytes()).hexdigest()==cls.original_hash

    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='rh-discovery-flow-');self.folder=Path(temporary.name);self.addCleanup(temporary.cleanup)
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('Real network denied'));guard.start();self.addCleanup(guard.stop)

    def test_actual_industry_and_chain_returns_continue_to_independent_prices(self):
        for index,count in [(0,48),(1,50)]:
            with self.subTest(topic=index):
                result=run_navigation_case(self.folder/str(index),ROOT,[self.inputs[index],self.inputs[2],self.inputs[3]])
                self.assertEqual(result['phases'],['draft_plan','draft_plan','draft_report'])
                self.assertEqual(result['operations'],['smart_stock_picking','cmd_history_quotation'])
                task=result['task'];self.assertEqual(len(task['draft_report']['facts']),16)
                self.assertEqual(retained_discovery_results(task)[0]['row_count'],count)
                self.assertEqual(task['draft_acquisition_coverage']['unresolved'],[])
                self.assertEqual(task['ledger']['used']['model_calls'],3)
                self.assertEqual(task['ledger']['used']['data_requests'],2)
                self.assertEqual({fact['code'] for fact in task['draft_report']['facts']},{'600519.SH','002594.SZ'})
                self.assertEqual(next(row for row in task['source_records'] if row['operation']=='smart_stock_picking')['snapshot'],self.inputs[index]['snapshot'])

    def test_previous_reader_still_uses_previous_scheduling(self):
        result=run_navigation_case(self.folder,ROOT,[self.inputs[0],self.inputs[2],self.inputs[3]],reader=13)
        self.assertEqual(result['phases'],['draft_plan','draft_plan','draft_plan','draft_report'])
        self.assertEqual(len(result['task']['draft_report']['facts']),16)

    def test_query_only_returns_report_after_one_plan(self):
        result=run_navigation_case(self.folder,ROOT,[self.inputs[0],self.inputs[2],self.inputs[3]],query_only=True)
        self.assertEqual(result['phases'],['draft_plan','draft_report'])
        self.assertEqual(result['operations'],['smart_stock_picking'])
        self.assertEqual(result['task']['draft_report']['facts'],[])
        self.assertEqual(result['task']['draft_acquisition_coverage']['navigation_request_ids'],[])

    def test_price_failure_preserves_candidates_and_does_not_block_report(self):
        result=run_navigation_case(self.folder,ROOT,[self.inputs[0],self.inputs[2],self.inputs[3]],failed_quote=True)
        self.assertEqual(result['phases'],['draft_plan','draft_plan','draft_report'])
        self.assertEqual(result['operations'],['smart_stock_picking','cmd_history_quotation'])
        task=result['task'];self.assertEqual(task['draft_report']['facts'],[])
        self.assertEqual(retained_discovery_results(task)[0]['row_count'],48)
        self.assertIsNotNone(task['delivery']);self.assertFalse(task['draft_acquisition_coverage']['blocks_report'])
