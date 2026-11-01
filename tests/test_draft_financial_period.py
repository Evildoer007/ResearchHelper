"""Financial request periods, response dates and local arithmetic applicability."""
from copy import deepcopy
from pathlib import Path
import tempfile,unittest
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
    record=dict(id='source-'+str(len(task['source_records'])+1),kind='api',source='ifind',operation=operation,
        task_id=task['id'],research_version=task['version'],status='成功待核验',parameters=params,snapshot=snapshot)
    record['hash']=fingerprint(snapshot);record['request_hash']=fingerprint({key:record[key] for key in ('source','operation','parameters')})
    task['source_records'].append(record);return record


def rehash(record):
    record['hash']=fingerprint(record['snapshot'])
    record['request_hash']=fingerprint({key:record[key] for key in ('source','operation','parameters')})


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


if __name__=='__main__':unittest.main()
