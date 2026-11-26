"""Current updates compare frozen public statements without another model call."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from tests import test_draft_first_workflow as financial
from core.session import Session
from researchhelper import draft_workflow
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, canonical, fingerprint
from researchhelper.draft_version import (compare_observations, comparison_enabled, original_comparison_basis,
    report_comparison_basis)
from researchhelper.planning import input_fingerprint, model_conversation_context
from researchhelper.evidence import current_query_reference, snapshot_record
from researchhelper.engine import ResearchAgent
from researchhelper.reports import build_version_changes, validate_version_changes


CONCLUSIONS = [
    '现金回收与盈利需分别观察，单期变化不足以认定持续改善。',
    '当前资料仍未证明回款持续改善，维持有条件的经营判断。',
    '本版继续保留经营判断的前提，不将文字改写视作逻辑兑现。']


def proposal(version=1):
    first = dict(title='现金兑现约束盈利质量',
        argument=dict(claim='盈利兑现仍取决于现金回收。',
                      evidence='保留本次取得的数据，分别核对收入、利润与经营现金流。',
                      reasoning='经营规模变化不能替代现金兑现分析。', condition='经营周期保持可比。'),
        implication='根据后续经营信息复核持续性。', risk='结算节奏可能影响单期表现。',
        workpaper_notes=['INTERNAL_FIXTURE_NOTE_NOT_FOR_UPDATE_CONTEXT'])
    second = dict(title='经营规模需要利润支持', analysis='收入增长并不必然代表利润改善。',
                  implication='需区分经营规模和实际盈利。', risk='单期业务结构可能变化。')
    logics = [first, second] if version == 1 else [second, dict(first, title='现金回收仍需观察')]
    return dict(title='经营与现金兑现', conclusion=CONCLUSIONS[min(version, 3)-1],
                logics=logics, risks=['现有资料不足以确认具体业务驱动。'])


class DraftReportComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):financial.DraftFirstWorkflowTests.setUpClass()

    @classmethod
    def tearDownClass(cls):financial.DraftFirstWorkflowTests.tearDownClass()

    def setUp(self):
        self.control = financial.DraftFirstWorkflowTests();self.control.setUp()
        self.addCleanup(self.control.doCleanups);self.addCleanup(self.control.agent.close_runtime)
        self.agent = self.control.agent;self.messages=[]
        reason = self.control.model.reason
        def with_prose(task, messages, **options):
            value = reason(task, messages, **options)
            self.messages.append(dict(version=task['version'], phase=options['phase'],
                                      payload=json.loads(messages[-1]['content'])))
            return json.dumps(proposal(task['version']), ensure_ascii=False) if options['phase']=='draft_report' else value
        self.control.model.reason = with_prose

    def first(self):return self.control.run_task()

    def test_projection_retains_exact_public_statements_and_not_private_notes(self):
        task, saved = self.first();before=deepcopy(saved)
        basis = report_comparison_basis(saved['content'])
        self.assertEqual(basis['conclusion'],CONCLUSIONS[0])
        self.assertEqual(basis['task_id'],task['id']);self.assertEqual(basis['research_version'],1)
        self.assertEqual(basis['logics'][0]['claim'],'盈利兑现仍取决于现金回收。')
        self.assertEqual(basis['logics'][1]['claim'],'')
        self.assertEqual(basis['logics'][1]['implication'],'需区分经营规模和实际盈利。')
        self.assertNotIn('INTERNAL_FIXTURE',json.dumps(basis))
        self.assertNotIn('analysis',basis['logics'][1])
        self.assertEqual(basis['basis_hash'],fingerprint({k:v for k,v in basis.items() if k!='basis_hash'}))
        self.assertEqual(self.agent.archive.report(task['id'],1),before)

    def test_new_version_compares_both_conclusions_and_each_versions_own_logic_order(self):
        first,saved=self.first();done=self.agent.update(first['id']);self.assertTrue(done.get('delivery'),done.get('error'))
        delta=done['changes']['draft_report_comparison']
        self.assertTrue(delta['baseline_available']);self.assertEqual(delta['version'],1)
        self.assertEqual(delta['prior']['conclusion'],CONCLUSIONS[0]);self.assertEqual(delta['current']['conclusion'],CONCLUSIONS[1])
        self.assertEqual([row['title'] for row in delta['prior']['logics']],['现金兑现约束盈利质量','经营规模需要利润支持'])
        self.assertEqual([row['title'] for row in delta['current']['logics']],['经营规模需要利润支持','现金回收仍需观察'])
        self.assertNotIn('matched',delta);self.assertNotIn('judgment',json.dumps(delta))
        self.assertEqual(done['theses'],[]);self.assertEqual(done['tracking'],[])
        self.assertEqual(self.agent.archive.report(first['id'],1),saved)
        validate_version_changes(done,saved)
        self.assertEqual(done['ledger']['used']['model_calls'],2)

    def test_only_writer_receives_the_original_conclusion(self):
        first,_=self.first();done=self.agent.update(first['id']);self.assertTrue(done.get('delivery'))
        planning, writing = [row['payload'] for row in self.messages if row['version'] == 2]
        self.assertNotIn('previous_report', planning['active_update'])
        self.assertNotIn(CONCLUSIONS[0], canonical(planning))
        self.assertNotIn('original_report_conditions', planning['active_update'])
        self.assertEqual(planning['active_update']['prior_version'], 1)
        previous = writing['active_update']['previous_report']
        self.assertEqual(previous['conclusion'], CONCLUSIONS[0])
        self.assertEqual(previous['research_version'], 1)
        self.assertNotIn('INTERNAL_FIXTURE', json.dumps(previous))
        self.assertIn('不是本版事实', writing['active_update']['report_rule'])
        self.assertEqual(self.control.model.calls,['draft_plan','draft_report','draft_plan','draft_report'])

    def test_followup_can_retrieve_a_cited_metric_without_reusing_the_old_observation(self):
        reason = self.control.model.reason

        def cite_revenue(task, messages, **options):
            response = reason(task, messages, **options)
            if options['phase'] != 'draft_report':
                return response
            payload = json.loads(messages[-1]['content'])
            revenue = next(row for row in payload['data']['observations'] if row['field'] == 'ths_revenue_stock')
            report = json.loads(response)
            report['logics'][0]['data_refs'] = [revenue['rows'][0][revenue['columns'].index('id')]]
            return json.dumps(report, ensure_ascii=False)

        self.control.model.reason = cite_revenue
        parent, original = self.first()
        for query, fields in (
            ('用本期数据核对逻辑1的依据。', [dict(subject='002594.SZ', field='ths_revenue_stock')]),
            ('改看600519.SH，参考第1轮逻辑1。', []),
        ):
            with self.subTest(query=query):
                child = self.agent.follow_up(parent['id'], query, start=False)
                context = model_conversation_context(child, include_prior_findings=False)
                self.assertEqual(context['focus'].get('observed_metrics', []), fields)
                self.assertNotIn('logics', context['focus'])
                self.assertNotIn('previous_answer', context)
                self.assertFalse(child['source_records'] or child['evidence'])
                self.assertEqual(self.agent.archive.report(parent['id'], 1), original)

    def test_compared_logics_pass_their_own_fields_to_acquisition(self):
        reason = self.control.model.reason
        groups = [('ths_revenue_stock', 'ths_np_stock'),
                  ('ths_np_stock', 'ths_ncf_from_oa_stock')]

        def cite_fields(task, messages, **options):
            response = reason(task, messages, **options)
            if options['phase'] != 'draft_report':
                return response
            rows = json.loads(messages[-1]['content'])['data']['observations']
            references = {row['field']: row['rows'][0][row['columns'].index('id')] for row in rows}
            report = json.loads(response)
            for logic, fields in zip(report['logics'], groups):
                logic['data_refs'] = [references[field] for field in fields]
            return json.dumps(report, ensure_ascii=False)

        self.control.model.reason = cite_fields
        parent, original = self.first()
        task_id = parent['id']
        next_round = self.agent.follow_up(task_id, '再核对002594.SZ的经营。', start=False)
        following = self.agent.start(next_round['id'])
        self.assertTrue(following.get('delivery'), following.get('error'))
        self.agent.close_runtime()
        reopened = ResearchAgent(ResearchArchive(self.agent.archive.root), ROOT, offline=True,
                                 draft_first=True, require_argument_support=True)
        self.addCleanup(reopened.close_runtime)
        child = reopened.follow_up(following['id'], '用本期数据核对第1轮的逻辑1、逻辑2的依据。', start=False)
        planning = model_conversation_context(child, include_prior_findings=False)['focus']
        self.assertEqual(planning['logic_indices'], [1, 2])
        self.assertEqual(planning['source']['delivery_digest'], original['digest'])
        self.assertNotIn('logics', planning)
        self.assertEqual({(row['logic_index'], row['subject'], row['field'])
                          for row in planning.get('observed_metrics', [])},
                         {(index, '002594.SZ', field) for index, fields in enumerate(groups, 1)
                          for field in fields})
        self.assertFalse(child['source_records'] or child['evidence'] or child['calculations'])
        self.assertEqual(child['ledger']['used']['model_calls'], 0)
        self.assertEqual(reopened.archive.report(task_id, 1), original)

        comparison = '对照第1轮的第1条逻辑与第2轮的第2条逻辑。'
        compared = reopened.follow_up(following['id'], comparison, start=False)
        references = model_conversation_context(compared, include_prior_findings=False)['focus']['references']
        self.assertEqual([row['source']['task_id'] for row in references], [task_id, following['id']])
        self.assertEqual([{metric['field'] for metric in row['observed_metrics']} for row in references],
                         list(map(set, groups)))

        historical = reopened.follow_up(following['id'], comparison + '研究截止日期：2026-06-30。', start=False)
        expected = dict(status='historical_reference_only',
                        references=[dict(round=1, logic_indices=[1]), dict(round=2, logic_indices=[2])])
        for include_prior_findings in (False, True):
            self.assertEqual(model_conversation_context(historical, include_prior_findings=include_prior_findings)['focus'],
                             expected)

        switched = reopened.follow_up(following['id'], comparison + '改看600519.SH。', start=False)
        foreign = model_conversation_context(switched, include_prior_findings=False)['focus']
        self.assertNotIn('observed_metrics', canonical(foreign))

    def test_third_version_uses_version_two_not_the_copied_version_one_parent(self):
        first,original=self.first();second=self.agent.update(first['id']);second_saved=self.agent.archive.report(first['id'],2)
        third=self.agent.update(first['id']);self.assertTrue(third.get('delivery'),third.get('error'))
        self.assertEqual(third['update_from']['version'],2)
        self.assertEqual(third['changes']['draft_report_comparison']['prior']['conclusion'],CONCLUSIONS[1])
        self.assertEqual(third['changes']['draft_report_comparison']['current']['conclusion'],CONCLUSIONS[2])
        self.assertEqual(third['input_hash'],input_fingerprint(third))
        self.assertEqual(self.agent.archive.report(first['id'],1),original)
        self.assertEqual(self.agent.archive.report(first['id'],2),second_saved)
        self.assertEqual(third['ledger']['used']['model_calls'],2)

    def test_changed_or_foreign_baseline_is_rejected_before_any_new_model_or_data_call(self):
        first,saved=self.first();prepared=self.agent.prepare_update(first['id']);used=len(self.control.model.calls)
        prepared['update_from']['report_comparison_basis']['conclusion']='篡改原结论'
        self.agent.archive.save(prepared);result=self.agent.start(first['id'])
        self.assertFalse(result.get('delivery'));self.assertEqual(len(self.control.model.calls),used)
        self.assertEqual(result['ledger']['used']['model_calls'],0);self.assertEqual(result['ledger']['used']['data_requests'],0)
        self.assertEqual(self.agent.archive.report(first['id'],1),saved)

    def test_rehashed_foreign_baseline_cannot_pass_task_and_version_binding(self):
        first,_=self.first();prepared=self.agent.prepare_update(first['id'])
        for key,value in [('task_id','foreign'),('research_version',0),('research_version',True)]:
            altered=deepcopy(prepared);basis=altered['update_from']['report_comparison_basis'];basis[key]=value
            basis['basis_hash']=fingerprint({k:v for k,v in basis.items() if k!='basis_hash'})
            altered['input_hash']=input_fingerprint(altered)
            with self.subTest(key=key,value=value),self.assertRaises(ContractError):original_comparison_basis(altered)

    def test_changed_saved_comparison_is_rejected_and_original_report_stays_frozen(self):
        first,saved=self.first();done=self.agent.update(first['id']);altered=deepcopy(done)
        altered['changes']['draft_report_comparison']['prior']['conclusion']='事后改写'
        with self.assertRaises(ContractError):validate_version_changes(altered,saved)
        altered=deepcopy(done);altered['update_from']['report_comparison_basis']['conclusion']='伪造基准'
        basis=altered['update_from']['report_comparison_basis'];basis['basis_hash']=fingerprint({k:v for k,v in basis.items() if k!='basis_hash'});altered['input_hash']=input_fingerprint(altered)
        with self.assertRaisesRegex(ContractError,'冻结报告'):build_version_changes(altered,saved)
        self.assertEqual(self.agent.archive.report(first['id'],1),saved)

    def test_cancel_and_resume_keep_original_conclusion_and_reuse_the_paid_plan(self):
        first,saved=self.first();checkpoint=draft_workflow._checkpoint;stopped=[]
        def persist_then_cancel(engine,task):
            checkpoint(engine,task)
            if task['version']==2 and not stopped and any(row['operation']=='basic_data_service' and row.get('retained_from_version',2)==2 for row in task['source_records']):
                stopped.append(True);self.agent.cancel(task['id'])
        with patch.object(draft_workflow,'_checkpoint',side_effect=persist_then_cancel):cancelled=self.agent.update(first['id'])
        self.assertEqual(cancelled['status'],'已取消');self.assertEqual(cancelled['ledger']['used']['model_calls'],1)
        done=self.agent.start(first['id']);self.assertTrue(done.get('delivery'),done.get('error'))
        self.assertEqual(done['ledger']['used']['model_calls'],2);self.assertEqual(done['ledger']['used']['data_requests'],3)
        self.assertEqual(done['changes']['draft_report_comparison']['prior']['conclusion'],CONCLUSIONS[0])
        self.assertEqual(self.control.model.calls,['draft_plan','draft_report','draft_plan','draft_report'])
        self.assertEqual(self.agent.archive.report(first['id'],1),saved)

    def test_unavailable_refresh_keeps_explicitly_retained_values_and_still_compares(self):
        first,saved=self.first();self.control.failed_financial_period='20250630'
        done=self.agent.update(first['id']);self.assertTrue(done.get('delivery'),done.get('error'))
        records={row['id']:row for row in done['source_records']}
        retained=[fact for fact in done['draft_report']['facts'] if records[fact['artifact_id']].get('retained_from_version')==1]
        self.assertEqual(len(retained),4)
        self.assertTrue(all(financial.period(records[fact['artifact_id']]['parameters'])=='20250630' for fact in retained))
        self.assertTrue(all(not current_query_reference(done,records[fact['artifact_id']]) for fact in retained))
        self.assertEqual(done['changes']['draft_observations']['counts']['retained'],4)
        self.assertEqual(done['changes']['draft_observations']['counts']['refreshed'],4)
        self.assertEqual(done['changes']['draft_report_comparison']['prior']['conclusion'],CONCLUSIONS[0])
        self.assertEqual(done['ledger']['used']['model_calls'],2)
        self.assertEqual(self.agent.archive.report(first['id'],1),saved)

    def test_new_comparison_checks_legacy_values_at_their_original_cutoff(self):
        from researchhelper.draft_sources import SCOPE_VERSION, VERSION, observations

        params = dict(codes='600519.SH',indicators='close',startdate='2026-10-29',enddate='2026-11-02',
                      functionpara=dict(Currency='RMB',CPS='1',Fill='Omit'))
        snapshot = dict(errorcode=0,tables=[dict(thscode='600519.SH',time=['2026-10-28','2026-10-29','2026-11-02'],
                                                table=dict(close=[94765,42,98765]))])
        request = dict(source='ifind',operation='cmd_history_quotation',parameters=params)
        record = dict(request,id='legacy-quote',kind='api',task_id='legacy-task',research_version=1,
                      snapshot=snapshot,status='成功待核验',hash=fingerprint(snapshot),request_hash=fingerprint(request))
        old = dict(id='legacy-task',version=1,cutoff='2026-10-30T18:00:00+08:00',source_records=[record],
                   draft_source_reader_version=20)
        facts = observations(old)
        self.assertEqual([r['raw_value'] for r in facts],[94765,42,98765])
        prior = dict(task_id=old['id'],research_version=1,cutoff=old['cutoff'],source_records=[record],draft=dict(facts=facts))
        before = fingerprint(prior)
        new = dict(old,version=2,cutoff='2026-11-03T18:00:00+08:00',source_records=[],draft_report=dict(facts=[]),
                   draft_source_reader_version=VERSION,draft_quote_scope_version=SCOPE_VERSION)
        result = compare_observations(prior,new)
        self.assertEqual(result['counts']['unmatched'],2)
        self.assertEqual([(r['period'],r['cells'][2]) for r in result['rows']],[('2026-10-29','42元')])
        self.assertEqual(fingerprint(prior),before)

    def sparse_financial_refresh(self):
        self.control.responses = deepcopy(self.control.responses)
        response = self.control.responses['basic_data_service:20260630']
        columns = response['tables'][0]['table']
        expected = {field: cell[0] for field, cell in columns.items()}
        expected['ths_revenue_stock'] += 1000001
        expected['ths_ncf_from_oa_stock'] = None
        response['tables'][0]['table'] = {field: [expected[field]] for field in
            ('ths_revenue_stock', 'ths_ncf_from_oa_stock')}
        self.control.failed_financial_period = '20250630'
        return expected

    def current_financial_values(self, task):
        rows = [row for row in task['draft_report']['facts'] if row['period']=='20260630']
        self.assertEqual(len(rows),len({row['field'] for row in rows}))
        return {row['field']:row['raw_value'] for row in rows}

    def without_new_sources(self, ident):
        updated = self.agent.update(ident,host_result=dict(data_requests=[],draft_report=None))
        self.assertTrue(updated.get('delivery'),updated.get('error'))
        self.assertEqual(updated['ledger']['used']['model_calls'],0)
        self.assertEqual(updated['ledger']['used']['data_requests'],0)
        return updated

    def test_partial_refresh_retains_omitted_cells_and_never_restores_the_previous_value_for_null(self):
        first, original = self.first()
        expected = self.sparse_financial_refresh()
        second = self.agent.update(first['id'])
        self.assertTrue(second.get('delivery'),second.get('error'))
        self.assertEqual(self.current_financial_values(second),expected)
        self.assertFalse(any(row['period']=='20260630' and row['method'] in ('raw_margin_v1','raw_percent_v1','raw_multiple_v1')
                             for row in second['draft_report']['calculations']))
        self.assertTrue(any(row['period']=='20250630' and row['method']=='raw_margin_v1'
                            for row in second['draft_report']['calculations']))
        previous = self.agent.archive.report(first['id'],2)
        third = self.without_new_sources(first['id'])
        self.assertEqual(self.current_financial_values(third),expected)
        self.assertEqual(self.agent.archive.report(first['id'],1),original)
        self.assertEqual(self.agent.archive.report(first['id'],2),previous)

    def test_older_updates_recover_verified_origins_from_the_original_versions(self):
        with patch('researchhelper.draft_sources.VERSION',21):
            first, frozen = self.first()
            expected = self.sparse_financial_refresh()
            second = self.agent.update(first['id'])
            self.assertTrue(second.get('delivery'),second.get('error'))
            self.assertEqual(len(self.current_financial_values(second)),2)
            third = self.without_new_sources(first['id'])
            self.assertNotIn('financial_refreshes',third['update_from'])
        updated = self.without_new_sources(first['id'])
        self.assertEqual(self.current_financial_values(updated),expected)
        self.assertEqual({row['version'] for row in updated['update_from']['financial_refreshes'].values()},{1,2})
        self.assertEqual(self.agent.archive.report(first['id'],1),frozen)

    def test_snapshot_import_cannot_replace_a_verified_retained_cell(self):
        first, _ = self.first()
        task = self.agent.prepare_update(first['id'])
        policy = self.control.transport(task,'ifind','get_limit_policy',{},None)
        task['source_records'].append(policy)
        source = next(row for row in first['source_records'] if row['operation']=='basic_data_service'
                      and financial.period(row['parameters'])=='20260630')
        payload = deepcopy(source['snapshot'])
        payload['tables'][0]['table']['ths_revenue_stock'][0] += 1000001
        imported = snapshot_record(task,'ifind','basic_data_service',source['parameters'],payload)
        task['source_records'].append(imported)
        self.agent.archive.save(task)
        updated = self.agent.start(first['id'],dict(data_requests=[],draft_report=None))
        self.assertEqual(self.current_financial_values(updated),self.current_financial_values(first))
        self.assertTrue(any(row['id']==imported['id'] and row['snapshot']==payload for row in updated['source_records']))

    def test_conflicting_same_version_cells_do_not_choose_a_value_or_hide_other_statement_scopes(self):
        first, _ = self.first()
        task = self.agent.prepare_update(first['id'])
        self.control.responses = deepcopy(self.control.responses)
        task['source_records'].append(self.control.transport(task,'ifind','get_limit_policy',{},None))
        source = next(row for row in first['source_records'] if row['operation']=='basic_data_service'
                      and financial.period(row['parameters'])=='20260630')
        full = source['parameters']
        task['source_records'].append(self.control.transport(task,'ifind','basic_data_service',full,None))
        revenue = 'ths_revenue_stock'
        selected = [row for row in full['indipara'] if row['indicator']==revenue]
        raw = deepcopy(source['snapshot'])
        amount = raw['tables'][0]['table'][revenue][0]
        raw['tables'][0]['table'] = {revenue:[amount+1000001]}
        raw['inputParams']['params'] = [row for row in raw['inputParams']['params'] if row['function']==revenue]
        key = 'basic_data_service:20260630'
        self.control.responses[key] = raw
        task['source_records'].append(self.control.transport(task,'ifind','basic_data_service',dict(full,indipara=selected),None))
        parent = deepcopy(raw)
        next(row for row in parent['inputParams']['params'][0]['params'] if row['name']=='FL')['value'] = '2'
        self.control.responses[key] = parent
        parent_params = dict(full,indipara=[dict(indicator=revenue,indiparams=['20260630','2'])])
        parent_source = self.control.transport(task,'ifind','basic_data_service',parent_params,None)
        task['source_records'].append(parent_source)
        self.agent.archive.save(task)
        second = self.agent.start(first['id'],dict(data_requests=[],draft_report=None))
        third = self.without_new_sources(first['id'])
        for updated in (second,third):
            values = self.current_financial_values(updated)
            self.assertEqual(values[revenue],amount+1000001)
            matched = [row for row in updated['draft_report']['facts'] if row['field']==revenue and row['period']=='20260630']
            self.assertEqual([row['artifact_id'] for row in matched],[parent_source['id']])
            self.assertTrue(any('同版财务原值存在冲突' in issue['reason'] for issue in updated['draft_source_issues']))

    def test_missing_period_in_both_versions_does_not_block_comparison_or_delivery(self):
        self.control.failed_financial_period='20250630';first,saved=self.first()
        self.assertEqual(len(first['draft_report']['facts']),4)
        done=self.agent.update(first['id']);self.assertTrue(done.get('delivery'),done.get('error'))
        self.assertEqual(len(done['draft_report']['facts']),4)
        self.assertEqual(done['changes']['draft_report_comparison']['prior']['conclusion'],CONCLUSIONS[0])
        self.assertEqual(done['ledger']['used']['model_calls'],2)
        self.assertEqual(self.agent.archive.report(first['id'],1),saved)

    def test_legacy_or_unknown_policy_does_not_add_comparison_context_or_mutate_task(self):
        first,_=self.first();prepared=self.agent.prepare_update(first['id'])
        for version in (None,True,2,'1'):
            legacy=deepcopy(prepared)
            if version is None:legacy.pop('draft_comparison_version')
            else:legacy['draft_comparison_version']=version
            legacy['input_hash']=input_fingerprint(legacy);before=deepcopy(legacy)
            payload=json.loads(draft_workflow._writing_messages(legacy,[])[-1]['content'])
            with self.subTest(version=version):
                self.assertFalse(comparison_enabled(legacy));self.assertNotIn('previous_report',payload['active_update'])
                self.assertEqual(legacy,before)

    def test_skill_and_app_share_previous_report_without_starting_a_second_model(self):
        with tempfile.TemporaryDirectory(prefix='rh-comparison-skill-') as directory:
            session=Session(directory)
            try:
                task=session.create('解释现金流与盈利的关系。',research_mode='current')
                first=session.draft(task['id'],report=proposal(),data_requests=[])
                saved=session.agent.archive.report(task['id'],1);prepared=session.update(task['id'])
                host=session.tools(task['id'],scope='first_report')['first_report']
                app=json.loads(draft_workflow._writing_messages(prepared,[])[-1]['content'])
                self.assertEqual(host['active_update'],app['active_update'])
                self.assertEqual(host['active_update']['previous_report']['conclusion'],CONCLUSIONS[0])
                done=session.draft(task['id'],report=proposal(2),data_requests=[])
                self.assertTrue(done.get('delivery'));self.assertEqual(done['ledger']['used']['model_calls'],0)
                self.assertEqual(session.agent.archive.report(task['id'],1),saved)
            finally:session.agent.close_runtime()


if __name__=='__main__':unittest.main()
