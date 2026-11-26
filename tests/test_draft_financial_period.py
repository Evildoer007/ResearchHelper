"""Financial request periods, response dates and local arithmetic applicability."""
from copy import deepcopy
from pathlib import Path
import socket,tempfile,unittest
from unittest.mock import patch
from tests import ROOT
from core.session import Session
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError,fingerprint
from researchhelper.draft_analysis import analyze,interpretation_context
from researchhelper.draft_sources import observations,writing_context,VERSION,READER_POLICY
from researchhelper.engine import ResearchAgent
from researchhelper.planning import input_fingerprint

FIELDS=('ths_revenue_stock','ths_np_stock','ths_ncf_from_oa_stock')
REPORT=dict(title='财务期间核对',conclusion='按明确报告期比较原值。',
    logics=[dict(title='可比期间变化',analysis='报告期与响应时间分开，不把日期相同当作期间可比。')])


def sample():
    task=dict(id='financial-period-fixture',version=1,input_hash='period-fixture',synthetic=True,
        cutoff='2026-10-31T23:00:00+08:00',source_records=[],materials=[],objects=['600519.SH'],
        entry='个股',intent='综合研究',question='比较两年财务',draft_source_reader_version=7)
    for period,values in (('20241231',(100,10,20)),('20251231',(120,12,18))):
        add_record(task,period,values)
    return task


def add_record(task,period,values,*,fields=FIELDS,operation='basic_data_service',periods=None):
    params=dict(codes='600519.SH',indipara=[dict(indicator=field,indiparams=[(periods or {}).get(field,period),'1']) for field in fields])
    snapshot=dict(errorcode=0,tables=[dict(thscode='600519.SH',time=['2026-10-31'],table={field:[value] for field,value in zip(fields,values)})])
    if operation == 'basic_data_service':
        snapshot['inputParams'] = dict(params=[dict(function=item['indicator'], params=[
            dict(name=name, value=value) for name, value in zip(('FDREPORT', 'FL'), item['indiparams'])])
            for item in params['indipara']])
    record=dict(id='source-'+str(len(task['source_records'])+1),kind='api',source='ifind',operation=operation,
        task_id=task['id'],research_version=task['version'],status='成功待核验',parameters=params,snapshot=snapshot)
    record['hash']=fingerprint(snapshot);record['request_hash']=fingerprint({key:record[key] for key in ('source','operation','parameters')})
    task['source_records'].append(record);return record


def rehash(record):
    record['hash']=fingerprint(record['snapshot'])
    record['request_hash']=fingerprint({key:record[key] for key in ('source','operation','parameters')})


class RetainedFinancialReadoutTests(unittest.TestCase):
    def setUp(self):
        self.task = sample()
        del self.task[READER_POLICY]
        self.task.update(research_mode='current', objects=[])
        for record in self.task['source_records']:
            del record['snapshot']['tables'][0]['time']
            rehash(record)

    def compile(self, task):
        from researchhelper.drafts import normalize_report

        facts = observations(task)
        task['draft_analysis'] = analyze(task, facts)
        return normalize_report({}, task, facts, retained_data_only=True)

    def test_legacy_financial_draft_reuses_only_bound_changes(self):
        task = self.task
        facts = observations(task)
        self.assertTrue(all('period_basis' not in row for row in facts))
        draft = self.compile(task)
        self.assertEqual(draft['title'], '600519.SH财务对照')
        self.assertIn('若两期单位一致', draft['conclusion'])
        self.assertIn('按请求合并报表口径', draft['conclusion'])
        self.assertIn('2025-12-31报告期', draft['conclusion'])
        self.assertIn('营业收入同比上升20.00%', draft['conclusion'])
        self.assertIn('经营现金流净额同比下降10.00%', draft['conclusion'])
        links = draft['quantity_links']['conclusion']['links']
        self.assertEqual(len(links), 3)
        self.assertTrue(all(link['kind'] == 'financial_yoy' and len(link['data_refs']) == 2 for link in links))
        self.assertEqual(observations(task), facts)

    def test_scope_conflicts_do_not_enter_the_legacy_summary(self):
        for problem in ('response_scope', 'wrong_period', 'different_owner', 'request_hash', 'duplicate', 'historical'):
            with self.subTest(problem=problem):
                task = deepcopy(self.task)
                current = task['source_records'][-1]
                if problem == 'response_scope':
                    current['snapshot']['inputParams']['params'][0]['params'][1]['value'] = '2'
                    rehash(current)
                elif problem == 'wrong_period':
                    current['snapshot']['tables'][0]['time'] = ['2025-06-30']
                    rehash(current)
                elif problem == 'different_owner':
                    current['task_id'] = 'foreign-task'
                elif problem == 'request_hash':
                    current['request_hash'] = 'unmatched'
                elif problem == 'duplicate':
                    task['source_records'].append(deepcopy(current))
                elif problem == 'historical':
                    task['research_mode'] = 'historical'
                draft = self.compile(task)
                self.assertNotIn('营业收入同比', draft['conclusion'])
                self.assertNotIn('quantity_links', draft)


class DraftFinancialPeriodTests(unittest.TestCase):
    def test_field_request_dates_survive_equal_response_times_and_yoy_is_computable(self):
        task=sample();before=deepcopy(task);facts=observations(task)
        self.assertEqual([row['period'] for row in facts],['20241231']*3+['20251231']*3)
        self.assertTrue(all(row['period_basis']=='请求D报告期' for row in facts))
        calculations=analyze(task,facts)['results']
        yoy=[row for row in calculations if row['method']=='raw_change_v1']
        self.assertEqual([row['display'] for row in yoy],['20.00','20.00','-10.00'])
        self.assertTrue(all(row['period']=='20251231' and not row['financial_approval'] for row in yoy))
        self.assertEqual(task,before)

    def test_each_column_uses_its_own_request_period_without_false_same_period_ratio(self):
        task=sample();task['source_records']=[]
        add_record(task,'20251231',(120,12,18),periods={'ths_np_stock':'20250630'})
        facts=observations(task);self.assertEqual([row['period'] for row in facts],['20251231','20250630','20251231'])
        self.assertFalse(any(row['method'] in ('raw_percent_v1','raw_multiple_v1') for row in analyze(task,facts)['results']))

    def test_unknown_financial_periods_keep_raw_values_and_other_arithmetic(self):
        task=sample();task['source_records']=[]
        add_record(task,'',(120,12,18))
        price=add_record(task,'',(100,),fields=('close',),operation='cmd_history_quotation')
        price['snapshot']['tables'][0].update(time=['2026-10-29','2026-10-30'],table={'close':[100,110]});rehash(price)
        facts=observations(task);financial=[row for row in facts if row['field'] in FIELDS]
        self.assertEqual(len(financial),3);self.assertTrue(all(row['period']=='' and row['period_basis']=='报告期未明确' for row in financial))
        result=analyze(task,facts)
        self.assertEqual([row['display'] for row in result['results'] if row['method']=='raw_change_v1'],['10.00'])
        self.assertFalse(any(row['method'] in ('raw_percent_v1','raw_multiple_v1') for row in result['results']))
        self.assertEqual(len(result['notes']),3)

    def test_invalid_dates_are_not_replaced_by_response_time(self):
        for period in ('20250231','20250000','2025-12-31',True):
            task=sample();task['source_records']=[];add_record(task,period,(120,12,18))
            with self.subTest(period=period):
                facts=observations(task);self.assertEqual(len(facts),3)
                self.assertTrue(all(row['period']=='' for row in facts));self.assertEqual(analyze(task,facts)['results'],[])

    def test_a_legacy_saved_task_retains_its_original_reader(self):
        task=sample();task.pop(READER_POLICY);facts=observations(task)
        self.assertEqual({row['period'] for row in facts},{'2026-10-31'})
        self.assertFalse(any('period_basis' in row for row in facts))
        self.assertEqual(analyze(task,facts)['results'],[])

    def test_balance_fields_remain_point_in_time_while_flow_fields_are_cumulative(self):
        task=sample();task['source_records']=[]
        add_record(task,'20251231',(120,20),fields=('ths_revenue_stock','ths_inventory_stock'))
        facts=observations(task);self.assertEqual([row['period_kind'] for row in facts],['cumulative','point_in_time'])
        self.assertEqual([row['period'] for row in facts],['20251231','20251231'])

    def test_other_basic_and_time_series_fields_keep_their_observed_dates(self):
        task=sample();task['source_records']=[]
        add_record(task,'20241231',(10,),fields=('unregistered_metric',))
        add_record(task,'20241231',(20,),fields=('ths_revenue_stock',),operation='date_sequence')
        facts=observations(task);self.assertEqual({row['period'] for row in facts},{'2026-10-31'})
        self.assertFalse(any('period_basis' in row for row in facts))

    def test_writer_receives_period_roles_and_the_same_raw_values(self):
        task=sample();facts=observations(task);context=writing_context(task,facts)
        series=context['observations']
        self.assertEqual({row['period'] for row in series},{'20241231','20251231'})
        self.assertTrue(all(row['period_basis']=='请求D报告期' for row in series))
        guide=interpretation_context(task,facts)
        self.assertIn('financial_period_scope',{rule['id'] for rule in guide['rules']})
        self.assertFalse(guide['delivery_blocking']);self.assertFalse(guide['financial_approval'])

    def test_reader_policy_is_adopted_by_app_and_skill_and_bound_to_input(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'result/acceptance') as folder:
            agent=ResearchAgent(ResearchArchive(folder),ROOT,offline=True,draft_first=True)
            try:
                task=agent.create('比较财务期间')
                self.assertEqual(task[READER_POLICY],VERSION)
                legacy=deepcopy(task);legacy.pop(READER_POLICY)
                self.assertNotEqual(input_fingerprint(task),input_fingerprint(legacy))
            finally:agent.close_runtime()
        with tempfile.TemporaryDirectory() as folder:
            session=Session(folder)
            try:self.assertEqual(session.create('比较财务期间')[READER_POLICY],VERSION)
            finally:session.agent.close_runtime()

    def test_new_version_adopts_reader_without_changing_the_frozen_legacy_report(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'result/acceptance') as folder:
            archive=ResearchArchive(folder);agent=ResearchAgent(archive,ROOT,offline=True,draft_first=True)
            try:
                task=agent.create('保留旧版，复核新期间');task.pop(READER_POLICY);task['input_hash']=input_fingerprint(task)
                archive.save(task);old=agent.start(task['id'],dict(data_requests=[],draft_report=REPORT))
                frozen=archive.report(old['id'],1);original=fingerprint(archive.get(old['id'],1))
                current=agent.update(old['id'],host_result=dict(data_requests=[],draft_report=REPORT))
                self.assertEqual(current[READER_POLICY],VERSION);self.assertEqual(current['version'],2)
                self.assertEqual(archive.report(old['id'],1)['html'],frozen['html'])
                self.assertEqual(fingerprint(archive.get(old['id'],1)),original)
                self.assertEqual(archive.report(old['id'],2)['content'][READER_POLICY],VERSION)
                self.assertEqual(current['ledger']['used']['model_calls'],0)
                self.assertEqual(current['ledger']['used']['data_requests'],0)
            finally:agent.close_runtime()


class FrozenReaderVersionTests(unittest.TestCase):
    """Saved metadata describes the reader actually used, never today's default."""
    def setUp(self):
        network = patch.object(socket.socket, 'connect', side_effect=AssertionError('No provider calls'))
        network.start(); self.addCleanup(network.stop)
        folder = tempfile.TemporaryDirectory(prefix='reader-version-', dir=ROOT/'result/acceptance')
        self.addCleanup(folder.cleanup)
        self.archive = ResearchArchive(folder.name)
        self.agent = ResearchAgent(self.archive, ROOT, offline=True, draft_first=True)
        self.addCleanup(self.agent.close_runtime)

    def saved(self, version):
        task = self.agent.create('合成验收：比较600519.SH两年年度收入', entry='个股', objects=['600519.SH'])
        task['synthetic'] = True
        if version is None:
            task.pop(READER_POLICY)
        else:
            task[READER_POLICY] = version
        task['input_hash'] = input_fingerprint(task)
        for period, value in (('20241231',100), ('20251231',120)):
            add_record(task, period, (value,), fields=('ths_revenue_stock',))
        self.archive.save(task)
        saved = self.agent.start(task['id'], dict(data_requests=[], draft_report=REPORT))
        return saved, self.archive.report(saved['id'], saved['version'])

    def test_all_retained_reader_versions_match_the_frozen_report(self):
        for version in (None,7,8,9):
            with self.subTest(version=version):
                saved, report = self.saved(version)
                self.assertTrue(saved.get('delivery'), saved.get('error'))
                self.assertEqual(saved.get(READER_POLICY), version)
                self.assertEqual(saved['draft_report']['source_reader_version'], version)
                self.assertEqual(report['content'].get(READER_POLICY), version)
                self.assertEqual(report['content']['draft']['source_reader_version'], version)
                self.assertEqual(len(saved['draft_report']['facts']), 2)
                expected = '元' if version==9 else '原始单位待核对'
                self.assertEqual({row['unit'] for row in saved['draft_report']['facts']}, {expected})
                self.assertEqual(saved['ledger']['used']['model_calls'], 0)
                self.assertEqual(saved['ledger']['used']['data_requests'], 0)

    def test_explicit_update_adopts_current_reader_and_preserves_the_old_version(self):
        saved, old = self.saved(8)
        task_before = fingerprint(self.archive.get(saved['id'],1))
        content_before = fingerprint(old['content'])
        updated = self.agent.update(saved['id'], host_result=dict(data_requests=[], draft_report=REPORT))
        current = self.archive.report(updated['id'],2)
        self.assertEqual(updated[READER_POLICY], VERSION)
        self.assertEqual(updated['draft_report']['source_reader_version'], VERSION)
        self.assertEqual(current['content'][READER_POLICY], VERSION)
        self.assertEqual(fingerprint(self.archive.get(saved['id'],1)), task_before)
        self.assertEqual(fingerprint(self.archive.report(saved['id'],1)['content']), content_before)
        self.assertEqual(updated['ledger']['used']['model_calls'], 0)
        self.assertEqual(updated['ledger']['used']['data_requests'], 0)


if __name__=='__main__':unittest.main()
