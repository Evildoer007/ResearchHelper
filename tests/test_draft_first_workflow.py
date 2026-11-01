"""First-paper behavior through actual source contracts, renderer and SQLite.

Only provider transports are fake. No real model/data call, browser, installed
App or production Store is used. Large historical snapshots are read once and
never copied into another acceptance directory.
"""
import copy
import hashlib
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from core.session import Session
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, charge, now, fingerprint
from researchhelper.drafts import observations, normalize_report
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import snapshot_record, _record_current_acquisition, _controlled_financial_definitions
from researchhelper.reports import compose, render
from researchhelper.model import ModelService, ModelSettings

SOURCE = ROOT / 'result/acceptance/one-sentence-research-s4h1y39l/task.json'
QUESTION = '研究比亚迪002594.SZ在2026年上半年的经营表现，对照上年同期，生成报告。'

def html_main_text(value):
    from lxml import html
    return html.document_fromstring(value).xpath('//main')[0].text_content()

REPORT = dict(title='比亚迪经营观察', conclusion='收入、成本、利润与现金回收呈现不同变化，应分开判断。',
    logics=[dict(title='盈利与现金兑现', analysis='结合本期与上期原始取数，比较经营表现与现金兑现的差异。',
                 implication='保留竞争解释后，再评估经营变化能否持续。', risk='附注与业务结构仍需补充。')],
    risks=['单期变化不代表未来表现。'])


def period(parameters):
    return parameters.get('report_period') or next((item['indiparams'][0]
        for item in parameters.get('indipara', []) if item.get('indiparams')), '')


class Model:
    def __init__(self, *, malformed_plan=False, fail_report=False):
        self.calls = []
        self.malformed_plan = malformed_plan
        self.fail_report = fail_report
        self.invalid_optional = False
        self.cite_knowledge = False
        self.knowledge_contexts = []

    def reason(self, task, messages, *, phase, delivery=False, **options):
        self.calls.append(phase)
        charge(task['ledger'], 'model_calls', 1, delivery=delivery)
        if phase == 'draft_plan':
            if self.malformed_plan:
                return 'not-json'
            requests = [dict(id='financial_' + period, source='ifind', operation='basic_data_service',
                parameters=dict(codes='002594.SZ', report_period=period, statement_type='1',
                    indicators=['ths_revenue_stock', 'ths_operating_cost_stock', 'ths_np_stock', 'ths_ncf_from_oa_stock']),
                question_ids=[task['questions'][0]['id']], selection_reason='比较本期与上年同期。')
                for period in ('20260630', '20250630')]
            if self.invalid_optional:
                requests.insert(0, dict(id='optional_bad', unknown=True))
            return json.dumps(dict(data_requests=requests), ensure_ascii=False)
        if self.fail_report:
            raise ContractError('离线模拟正文失败')
        report=copy.deepcopy(REPORT)
        knowledge=json.loads(messages[-1]['content']).get('knowledge_context',{})
        self.knowledge_contexts.append(knowledge)
        if self.cite_knowledge:
            mechanism=next(row for row in knowledge['entries'] if row['type']=='机制')
            report['logics'][0]['knowledge_refs']=[{key:mechanism[key] for key in ('id','version')}]
        return json.dumps(report, ensure_ascii=False)


class DraftFirstWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
        source = json.loads(SOURCE.read_text())
        cls.responses = {row['operation'] + ':' + period(row.get('parameters', {})):
            row['snapshot'] for row in source['source_records'] if row['operation'] in ('get_limit_policy', 'basic_data_service')}

    @classmethod
    def tearDownClass(cls):
        assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == cls.original_hash

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='rh-draft-test-')
        self.addCleanup(self.directory.cleanup)
        self.network = patch.object(socket.socket, 'connect', side_effect=AssertionError('Real network denied'))
        self.network.start(); self.addCleanup(self.network.stop)
        self.model = Model()
        self.agent = ResearchAgent(ResearchArchive(self.directory.name), ROOT, offline=False,
            model=self.model, require_argument_support=True, draft_first=True)
        self.agent.collector.external = self.transport

    def transport(self, task, source, operation, params, credentials):
        charge(task['ledger'], 'data_requests', 1)
        snapshot = copy.deepcopy(self.responses[operation + ':' + period(params)])
        if operation=='basic_data_service' and period(params)==getattr(self,'failed_financial_period',None):
            snapshot['errorcode']=-1
        record = snapshot_record(task, source, operation, params, snapshot)
        # Synthetic transport receipt, just as in the existing metadata test
        # harness. It is never exported as proof of actual provider access.
        event = dict(kind='data_requests', source=source, operation=operation,
            fingerprint=record['request_hash'], batch=task['batch'], status='成功' if record['status']=='成功待核验' else '失败', time=now(),
            transport_isolated=True, network_request_started=True, network_worker_reaped=True)
        task['ledger']['dispatches'].append(event)
        if operation == 'basic_data_service':
            record.update(fixed_financial_catalog_version=1,
                fixed_financial_catalog_digest=fingerprint(_controlled_financial_definitions()))
        _record_current_acquisition(task, record, event)
        return record

    def run_task(self):
        task = self.agent.create(QUESTION, research_mode='current')
        result = self.agent.start(task['id'])
        self.assertIsNotNone(result.get('delivery'), result.get('error'))
        self.assertEqual(result['delivery'].get('kind'), 'research_draft')
        return result, self.agent.archive.report(task['id'], result['version'])

    def test_one_sentence_data_to_saved_paper_without_review_prerequisite(self):
        task, paper = self.run_task()
        self.assertEqual(self.model.calls, ['draft_plan', 'draft_report'])
        proposal = task['draft_submissions'][0]
        self.assertEqual(json.loads(proposal['payload']), REPORT)
        self.assertEqual(proposal['origin'], 'model')
        self.assertEqual(proposal['model_call_count'], 2)
        self.assertEqual(len(task['draft_report']['facts']), 8)
        self.assertEqual(task['ledger']['used']['data_requests'], 3)
        self.assertEqual(task['evidence'], [])
        self.assertEqual(task['theses'], [])
        self.assertEqual(task['status'], '部分完成')
        self.assertIn('核心结论', paper['html'])
        self.assertIn('策略逻辑一', paper['html'])
        self.assertIn('经营活动产生的现金流量净额', paper['html'])
        self.assertIn('echarts.init', paper['html'])
        self.assertNotIn('<details', paper['html'])
        self.assertNotIn('原文逐项诊断', paper['html'])
        self.assertNotIn('研究草稿已生成，独立核验', paper['html'])

    def test_locked_knowledge_reaches_the_saved_first_report_in_the_existing_two_calls(self):
        self.model.cite_knowledge=True
        task,paper=self.run_task()
        self.assertEqual(self.model.calls,['draft_plan','draft_report'])
        self.assertEqual(task['ledger']['used']['model_calls'],2)
        self.assertEqual(task['ledger']['used']['data_requests'],3)
        refs=paper['content']['draft']['logics'][0]['knowledge_refs']
        self.assertEqual(len(refs),1)
        entry=next(row for row in task['knowledge']['entries'] if row['id']==refs[0]['id'])
        self.assertEqual(refs[0]['version'],entry['version'])
        self.assertEqual(refs[0]['entry_hash'],fingerprint(entry))
        self.assertIn(entry['content'],paper['html'])
        self.assertEqual(self.model.knowledge_contexts[0],paper['content']['draft']['knowledge_context'])

    def test_missing_data_still_produces_a_report(self):
        self.agent.collector.external = lambda *args: (_ for _ in ()).throw(ContractError('离线模拟缺数'))
        task, paper = self.run_task()
        self.assertEqual(task['draft_report']['facts'], [])
        self.assertEqual(paper['content']['draft']['conclusion'], REPORT['conclusion'])
        self.assertEqual(paper['content']['report_kind'],'research_draft')
        self.assertNotIn('研究草稿',html_main_text(paper['html']))
        self.assertNotIn('离线模拟缺数', paper['html'])

    def test_failed_financial_response_keeps_originals_but_not_residual_numbers(self):
        self.failed_financial_period='20260630'
        task,paper=self.run_task()
        failed=next(row for row in task['source_records'] if row['operation']=='basic_data_service' and period(row['parameters'])=='20260630')
        self.assertTrue(failed['snapshot']['tables'])
        self.assertEqual(len(task['draft_report']['facts']),4)
        self.assertTrue(all(row['period']=='20250630' for row in task['draft_report']['facts']))
        # Independent same-period ratios remain usable; missing current data
        # must not manufacture a comparison across the two reporting periods.
        allowed={row['id'] for row in task['draft_report']['facts']}
        for calculation in task['draft_report']['calculations']:
            self.assertEqual(calculation['period'],'20250630')
            self.assertTrue(set(calculation['data_refs']) <= allowed)
            self.assertNotIn('yoy',calculation['method'])
        self.assertEqual(task['draft_source_issues'][0]['artifact_id'],failed['id'])
        self.assertEqual(next(node['status'] for node in task['nodes'] if node['id']=='evidence'),'部分成功')
        self.assertTrue(paper['html']);self.assertEqual(self.model.calls,['draft_plan','draft_report'])
        self.assertEqual(task['ledger']['used']['data_requests'],3)

    def test_bad_optional_request_does_not_discard_independent_data(self):
        self.model.invalid_optional = True
        task, _ = self.run_task()
        self.assertEqual(len(task['draft_report']['facts']), 8)
        self.assertTrue(task.get('draft_notices'))
        self.assertEqual(task['ledger']['used']['data_requests'], 3)

    def test_cancel_is_respected_without_discarding_spent_budget(self):
        original = self.model.reason
        def reason(task, messages, **options):
            value = original(task, messages, **options)
            if options['phase'] == 'draft_report':
                self.agent.cancel(task['id'])
            return value
        self.model.reason = reason
        task = self.agent.create(QUESTION, research_mode='current')
        result = self.agent.start(task['id'])
        self.assertEqual(result['status'], '已取消')
        self.assertIsNone(result['delivery'])

    def test_updated_paper_does_not_overwrite_previous_report(self):
        task, paper = self.run_task()
        updated = self.agent.prepare_update(task['id'])
        self.assertNotIn('draft_report', updated)
        result = self.agent.start(task['id'], dict(draft_report=dict(REPORT, conclusion='第二版研究初稿。'), data_requests=[]))
        self.assertIsNotNone(result['delivery'], result.get('error'))
        self.assertEqual(result['version'], 2)
        self.assertEqual(self.agent.archive.report(task['id'], 1)['html'], paper['html'])

    def test_bad_planning_format_does_not_block_data_outline(self):
        self.model.malformed_plan = True
        task, paper = self.run_task()
        self.assertEqual(self.model.calls, ['draft_plan','draft_report'])
        self.assertEqual(task['draft_report']['origin'], 'model')
        self.assertTrue(paper['content']['draft']['logics'])
        self.assertEqual(paper['content']['draft']['conclusion'],REPORT['conclusion'])

    def test_failed_model_generation_keeps_acquired_data_and_delivers(self):
        self.model.fail_report = True
        task, paper = self.run_task()
        self.assertEqual(len(task['draft_report']['facts']), 8)
        self.assertEqual(task['draft_report']['origin'], 'data_outline')
        self.assertIn('344815421000', paper['html'])

    def test_host_can_collect_then_write_with_zero_model_calls(self):
        task = self.agent.create(QUESTION, research_mode='current')
        first = self.agent.start(task['id'], dict(draft_report=None, data_requests=[]), defer_delivery=True)
        self.assertEqual(first['status'], '待开始')
        self.assertNotIn('pending_html', first)
        second = self.agent.start(task['id'], dict(draft_report=REPORT, data_requests=[]))
        self.assertIsNotNone(second['delivery'])
        self.assertEqual(self.model.calls, [])
        self.assertEqual(second['ledger']['used']['model_calls'], 0)

    def test_initial_host_acquisition_does_not_spend_verified_supplement_rounds(self):
        task = self.agent.create(QUESTION, research_mode='current')
        for index in range(4):
            result = self.agent.start(task['id'], dict(draft_report=None,
                data_requests=[dict(id='optional_' + str(index), unknown=True)]), defer_delivery=True)
            self.assertEqual(result['status'], '待开始', result.get('error'))
        result = self.agent.start(task['id'], dict(draft_report=REPORT, data_requests=[]))
        self.assertIsNotNone(result['delivery'], result.get('error'))
        self.assertEqual(result['ledger']['supplements'], 0)
        self.assertEqual(result['ledger']['used']['model_calls'], 0)

    def test_raw_numbers_keep_vendor_units_and_periods(self):
        task, _ = self.run_task()
        rows = observations(task)
        row = next(item for item in rows if item['field'] == 'ths_revenue_stock' and item['period'] == '20260630')
        self.assertEqual((row['value'], row['unit']), (344815421000, '原始单位待核对'))
        self.assertEqual(row['status'], '原始取数')

    def test_foreign_task_cells_are_not_adopted(self):
        task, _ = self.run_task()
        for row in task['source_records']:
            row['task_id'] = 'other_task'
        self.assertEqual(observations(task), [])

    def test_changed_sources_cannot_silently_overwrite_a_frozen_paper(self):
        task, _ = self.run_task()
        task['source_records'][0]['snapshot'] = {'changed': True}
        with self.assertRaises(ContractError):
            compose(task)

    def test_prose_and_report_headings_are_escaped(self):
        task, _ = self.run_task()
        task['draft_report']['title'] = '<script>bad()</script>'
        html = render(compose(task), ROOT)
        self.assertIn('&lt;script&gt;bad()', html)
        self.assertNotIn('<script>bad()', html)

    def test_plain_text_response_is_retained_as_a_draft(self):
        value = normalize_report('可读但不是JSON的初稿。', dict(question=QUESTION, objects=[]), [])
        self.assertEqual(value['conclusion'], '可读但不是JSON的初稿。')

    def test_fixture_cases_retain_their_existing_workflow(self):
        task = self.agent.create('fixture', fixture='mechanism')
        self.assertNotIn('draft_first_version', task)

    def test_report_is_restored_after_archive_reopen(self):
        task, paper = self.run_task()
        archive = ResearchArchive(self.directory.name)
        self.assertEqual(archive.report(task['id'], task['version'])['html'], paper['html'])

    def test_skill_entry_uses_host_reasoning_without_an_app_model(self):
        with tempfile.TemporaryDirectory(prefix='rh-skill-draft-') as project:
            session = Session(project)
            task = session.create(QUESTION)
            result = session.draft(task['id'], REPORT)
            self.assertIsNotNone(result['delivery'], result.get('error'))
            self.assertEqual(result['ledger']['used']['model_calls'], 0)
            self.assertEqual(session.archive.report(task['id'], result['version'])['content']['report_kind'], 'research_draft')

    def test_actual_model_transport_can_use_the_final_call_for_a_draft(self):
        task = self.agent.create(QUESTION, research_mode='current')
        settings = ModelSettings(Path(self.directory.name) / 'model-test')
        settings.save(dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash',
            api_key='SYNTHETIC_DRAFT_TEST_KEY', reasoning_effort='low'))
        service = ModelService(settings, offline=False)
        response = dict(id='rsp_draft_test', object='response', status='completed', model='deepseek-flash',
            output=[dict(type='message', role='assistant', status='completed', content=[dict(type='output_text', text=json.dumps(REPORT))])],
            usage=dict(input_tokens=3, output_tokens=3))
        with patch.object(service, '_request_bytes', return_value=json.dumps(response).encode()) as transport:
            value = service.reason(task, [dict(role='user', content='Produce JSON draft.')],
                delivery=True, phase='draft_report', delivery_margin=5)
        self.assertEqual(json.loads(value)['title'], REPORT['title'])
        transport.assert_called_once()
        wire = json.loads(transport.call_args.args[1].data)
        self.assertLessEqual(wire['max_output_tokens'], 16384)
        self.assertEqual(task['ledger']['used']['model_calls'], 1)


if __name__ == '__main__':
    unittest.main()
