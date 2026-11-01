"""A malformed quotation subset must not contaminate or block the first report."""
import copy
from pathlib import Path
import tempfile
import unittest
from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.contracts import fingerprint
from researchhelper.draft_sources import observations,source_context,writing_context,source_issues

POLICY='draft_quote_scope_version'

class DraftQuotationScopeTests(unittest.TestCase):
    def setUp(self):
        self.task=dict(id='quote-scope',version=1,input_hash='scope',objects=['600519.SH'],entry='个股',
            question='核对600519.SH行情',cutoff='2026-10-31T00:00:00+08:00',source_records=[],materials=[],**{POLICY:1})
        self.params=dict(codes='600519.SH',indicators='open,close',startdate='2026-10-29',enddate='2026-10-30',functionpara=dict(Currency='RMB',CPS='1',Fill='Omit'))
    def record(self,table=None,**changes):
        table=table or dict(thscode='600519.SH',time=['2026-10-29','2026-10-30'],table=dict(open=[100,None],close=[101,102]))
        payload=dict(errorcode=0,tables=[copy.deepcopy(table)])
        r=dict(id='quote',task_id=self.task['id'],research_version=1,input_hash=self.task['input_hash'],kind='api',source='ifind',
            operation='cmd_history_quotation',status='成功待核验',parameters=copy.deepcopy(self.params),snapshot=payload)
        r.update(changes);r['hash']=fingerprint(r['snapshot']);r['request_hash']=fingerprint({k:r[k] for k in ('source','operation','parameters')})
        self.task['source_records'].append(r);return r
    def test_new_app_and_skill_tasks_enable_the_same_bound_policy(self):
        with tempfile.TemporaryDirectory() as folder:
            agent=ResearchAgent(ResearchArchive(folder),ROOT,offline=True,require_argument_support=True,draft_first=True)
            try:
                task=agent.create('研究600519.SH',cutoff=self.task['cutoff'])
                self.assertEqual(task.get(POLICY),2)
                from researchhelper.planning import input_fingerprint
                old=copy.deepcopy(task);old.pop(POLICY)
                self.assertNotEqual(input_fingerprint(old),task['input_hash'])
            finally:agent.close_runtime()
    def test_valid_zero_and_missing_cells_survive_without_new_approval(self):
        self.record(dict(thscode='600519.SH',time=['2026-10-29','2026-10-30'],table=dict(open=[0,None],close=[101,102])))
        rows=observations(self.task)
        self.assertEqual([r['raw_value'] for r in rows],[0,None,101,102])
        self.assertFalse(source_issues(self.task))
    def test_foreign_code_is_not_given_to_the_writer_or_planner(self):
        self.record(dict(thscode='600036.SH',time=['2026-10-29'],table=dict(close=[98765])))
        self.assertEqual(observations(self.task),[])
        self.assertNotIn('98765',str(source_context(self.task)))
        self.assertNotIn('98765',str(writing_context(self.task,[])))
        self.assertTrue(source_issues(self.task))
    def test_window_and_future_rows_are_filtered_individually(self):
        self.record(dict(thscode='600519.SH',time=['2026-10-28','2026-10-29','2026-10-30','2026-11-02'],table=dict(close=[98765,101,102,99999])))
        before=copy.deepcopy(self.task);rows=observations(self.task)
        self.assertEqual([r['raw_value'] for r in rows],[101,102])
        self.assertEqual([r['locator']['row'] for r in rows],[1,2])
        self.assertNotIn('98765',str(source_context(self.task)));self.assertNotIn('99999',str(writing_context(self.task,rows)))
        self.assertEqual(self.task,before)
    def test_duplicate_dates_leave_unambiguous_dates_usable(self):
        self.record(dict(thscode='600519.SH',time=['2026-10-29','2026-10-29','2026-10-30'],table=dict(close=[98765,99999,102])))
        self.assertEqual([r['raw_value'] for r in observations(self.task)],[102])
    def test_unrequested_numeric_columns_are_not_analysis_inputs(self):
        self.record(dict(thscode='600519.SH',time=['2026-10-29'],table=dict(close=[101],target_price=[99999])))
        rows=observations(self.task)
        self.assertEqual([r['field'] for r in rows],['close'])
        self.assertNotIn('99999',str(source_context(self.task)))
        self.assertNotIn('99999',str(writing_context(self.task,rows)))
    def test_misaligned_column_does_not_discard_an_aligned_column(self):
        self.record(dict(thscode='600519.SH',time=['2026-10-29','2026-10-30'],table=dict(open=[98765],close=[101,102])))
        self.assertEqual([r['raw_value'] for r in observations(self.task)],[101,102])
    def test_missing_explicit_security_does_not_borrow_requested_code(self):
        self.record(dict(time=['2026-10-29'],table=dict(close=[98765])))
        self.assertEqual(observations(self.task),[])
    def test_explicit_per_row_codes_keep_only_the_requested_row(self):
        self.record(dict(time=['2026-10-29','2026-10-30'],table=dict(thscode=['600519.SH','600036.SH'],close=[101,98765])))
        self.assertEqual([r['code'] for r in observations(self.task)],['600519.SH'])
    def test_distinct_objects_can_have_the_same_date(self):
        self.params['codes']='600519.SH,600036.SH'
        self.record(dict(time=['2026-10-30','2026-10-30'],
            table=dict(thscode=['600519.SH','600036.SH'],close=[101,102])))
        self.assertEqual([r['raw_value'] for r in observations(self.task)],[101,102])
    def test_malformed_security_cell_is_excluded_without_exception(self):
        self.record(dict(time=['2026-10-29','2026-10-30'],
            table=dict(thscode=[['600519.SH'],'600519.SH'],close=[98765,102])))
        self.assertEqual([r['raw_value'] for r in observations(self.task)],[102])
    def test_task_cutoff_also_applies_within_a_longer_requested_window(self):
        self.task['cutoff']='2026-10-29T23:59:00+08:00'
        self.record()
        self.assertEqual([r['raw_value'] for r in observations(self.task)],[100,101])
    def test_writer_rejects_previously_read_out_of_scope_rows(self):
        record=self.record(dict(thscode='600519.SH',time=['2026-10-29','2026-11-02'],table=dict(close=[101,98765])))
        legacy=copy.deepcopy(self.task);legacy.pop(POLICY)
        stale=observations(legacy)
        self.assertEqual(len(stale),2)
        context=writing_context(self.task,stale)
        self.assertNotIn('98765',str(context))
        self.assertFalse(context['other_data'])
    def test_partial_quote_still_keeps_interpretation_guidance(self):
        from researchhelper.draft_analysis import interpretation_context
        self.record(dict(thscode='600519.SH',time=['2026-10-29','2026-11-02'],table=dict(close=[101,98765])))
        self.assertTrue(source_issues(self.task)[0]['partial'])
        rules=interpretation_context(self.task,observations(self.task))['rules']
        self.assertIn('close_scope',[r['id'] for r in rules])
    def test_partial_source_reaches_a_saved_report_and_survives_reopen(self):
        from lxml import html
        with tempfile.TemporaryDirectory() as folder:
            archive=ResearchArchive(folder)
            agent=ResearchAgent(archive,ROOT,offline=True,require_argument_support=True,draft_first=True)
            try:
                self.task=agent.create('核对600519.SH已取得的收盘观测',objects=['600519.SH'],cutoff=self.task['cutoff'])
                self.record(dict(thscode='600519.SH',time=['2026-10-29','2026-10-30','2026-11-02'],
                    table=dict(close=[101,102,98765],target_price=[99999,99999,99999])))
                originals=copy.deepcopy(self.task['source_records'])
                archive.save(self.task)
                report=dict(title='收盘观测',conclusion='只比较已取得的收盘读数。',
                    logics=[dict(title='收盘路径',analysis='使用同一标的的两次观测，不推断未来收益。',data_refs=['D1','D2'])])
                done=agent.start(self.task['id'],dict(draft_report=report,data_requests=[]))
                self.assertTrue(done.get('delivery'),done.get('error'))
                self.assertEqual([r['raw_value'] for r in done['draft_report']['facts']],[101,102])
                self.assertEqual(done['source_records'],originals)
                self.assertEqual(done['ledger']['used']['model_calls'],0)
                self.assertEqual(done['ledger']['used']['data_requests'],0)
                saved=archive.report(done['id'],done['version'])
                main=html.document_fromstring(saved['html']).xpath('//main')[0].text_content()
                self.assertNotIn('98765',main);self.assertNotIn('99999',main)
                self.assertEqual(saved['content'][POLICY],2)
                ident=done['id'];digest=saved['digest']
            finally:agent.close_runtime()
            reopened=ResearchArchive(folder)
            self.assertEqual(reopened.report(ident,1)['digest'],digest)
            self.assertEqual(reopened.get(ident)['source_records'],originals)
    def test_legacy_task_keeps_frozen_reading_rules(self):
        self.task.pop(POLICY);self.record(dict(thscode='600036.SH',time=['2026-11-02'],table=dict(close=[98765])))
        self.assertEqual([r['raw_value'] for r in observations(self.task)],[98765])

if __name__=='__main__':unittest.main()
