"""Daily-series anomalies stay in the workpaper while usable data still ships."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.draft_sources import SCOPE_VERSION, observations, source_context, source_issues, writing_context
from researchhelper.engine import ResearchAgent

POLICY='draft_quote_scope_version'


class DraftSequenceScopeTests(unittest.TestCase):
    def setUp(self):
        self.task=dict(id='sequence-scope',version=1,input_hash='scope',cutoff='2026-10-30T23:00:00+08:00',
                       source_records=[],materials=[],**{POLICY:2})
        self.params=dict(codes='600519.SH',startdate='2026-10-29',enddate='2026-10-30',
                         indipara=[dict(indicator='ths_pe_ttm_stock',indiparams=['','100'])],functionpara=dict(Fill='Blank'))
        guard=patch('socket.socket.connect',side_effect=AssertionError('No network in sequence-scope tests'))
        guard.start();self.addCleanup(guard.stop)

    def source(self,*tables):
        source=dict(id='sequence',kind='api',task_id=self.task['id'],research_version=self.task['version'],
                    source='ifind',operation='date_sequence',status='成功待核验',parameters=deepcopy(self.params),
                    snapshot=dict(errorcode=0,tables=deepcopy(list(tables))))
        source['hash']=fingerprint(source['snapshot'])
        source['request_hash']=fingerprint({k:source[k] for k in ('source','operation','parameters')})
        self.task['source_records'].append(source);return source

    def test_wrong_code_future_and_unrequested_values_never_enter_model_contexts(self):
        self.source(dict(thscode='600519.SH',time=['2026-10-29','2026-11-02'],
                         table=dict(ths_pe_ttm_stock=[20,98765],not_requested=[99999,99999])),
                    dict(thscode='600036.SH',time=['2026-10-30'],table=dict(ths_pe_ttm_stock=[94765])))
        before=deepcopy(self.task);facts=observations(self.task)
        self.assertEqual([r['value'] for r in facts],[20])
        for context in (source_context(self.task),writing_context(self.task,facts)):
            self.assertFalse(any(s in str(context) for s in ('98765','99999','94765')))
        self.assertTrue(source_issues(self.task)[0]['partial']);self.assertEqual(self.task,before)

    def test_aligned_field_survives_another_misaligned_field_and_nulls_are_preserved(self):
        self.params['indipara'].append(dict(indicator='ths_pb_latest_stock',indiparams=[]))
        self.source(dict(thscode='600519.SH',time=['2026-10-29','2026-10-30'],
                         table=dict(ths_pe_ttm_stock=[0,None],ths_pb_latest_stock=[98765])))
        self.assertEqual([r['value'] for r in observations(self.task)],[0,None])

    def test_documented_date_formats_are_compared_without_rewriting_originals(self):
        self.params.update(startdate='20261029',enddate='2026/10/30')
        self.source(dict(thscode='600519.SH',time=['20261029','2026/10/30'],table=dict(ths_pe_ttm_stock=[20,21])))
        facts=observations(self.task)
        self.assertEqual([r['period'] for r in facts],['20261029','2026/10/30'])
        self.assertFalse(source_issues(self.task))

    def test_duplicate_days_in_different_formats_remain_ambiguous(self):
        self.source(dict(thscode='600519.SH',time=['20261029','2026-10-29','2026-10-30'],
                         table=dict(ths_pe_ttm_stock=[98765,99999,21])))
        self.assertEqual([r['value'] for r in observations(self.task)],[21])

    def test_separate_fields_on_the_same_dates_do_not_conflict(self):
        self.task[POLICY] = SCOPE_VERSION
        self.params['indipara'].append(dict(indicator='ths_pb_latest_stock',indiparams=[]))
        days = ['2026-10-29','2026-10-30']
        record = self.source(
            dict(thscode='600519.SH',time=days,table=dict(ths_pe_ttm_stock=[20,21])),
            dict(thscode='600519.SH',time=days,table=dict(ths_pb_latest_stock=[5,6])),
            dict(thscode='600519.SH',time=['20261029'],table=dict(ths_pe_ttm_stock=[98765])),
            dict(thscode='600519.SH',time=['2026-10-30'],table=dict(thscode=['600036.SH'],ths_pe_ttm_stock=[99999])),
        )
        facts = observations(self.task)
        self.assertEqual([(row['period'],row['raw_value'],row['locator']['table']) for row in facts],
            [('2026-10-30',21,0),('2026-10-29',5,1),('2026-10-30',6,1)])
        self.assertTrue(source_issues(self.task)[0]['partial'])
        self.assertEqual(record['hash'],fingerprint(record['snapshot']))

    def test_cutoff_applies_independently_of_the_requested_window(self):
        self.task['cutoff']='2026-10-29T23:00:00+08:00'
        self.source(dict(thscode='600519.SH',time=['2026-10-29','2026-10-30'],table=dict(ths_pe_ttm_stock=[20,98765])))
        self.assertEqual([r['value'] for r in observations(self.task)],[20])

    def test_current_writer_cannot_reuse_facts_from_the_legacy_reader(self):
        self.source(dict(thscode='600519.SH',time=['2026-10-29','2026-11-02'],table=dict(ths_pe_ttm_stock=[20,98765])))
        legacy=deepcopy(self.task);legacy[POLICY]=1
        old=observations(legacy);self.assertEqual(len(old),2)
        self.assertNotIn('98765',str(writing_context(self.task,old)))

    def test_missing_explicit_code_is_not_filled_from_the_request(self):
        self.source(dict(time=['2026-10-29'],table=dict(ths_pe_ttm_stock=[98765])))
        self.assertEqual(observations(self.task),[])

    def test_distinct_requested_codes_can_share_a_day(self):
        self.params['codes']='600519.SH,600036.SH'
        self.source(dict(time=['2026-10-29','2026-10-29'],
                         table=dict(thscode=['600519.SH','600036.SH'],ths_pe_ttm_stock=[20,21])))
        self.assertEqual([(r['code'],r['value']) for r in observations(self.task)],[('600519.SH',20),('600036.SH',21)])

    def test_unknown_policy_or_boolean_cannot_silently_select_another_reader(self):
        self.source(dict(thscode='600519.SH',time=['2026-10-29'],table=dict(ths_pe_ttm_stock=[20])))
        for value in (True,SCOPE_VERSION+1,'2'):
            with self.subTest(value=value),self.assertRaises(ContractError):
                observations(dict(self.task,**{POLICY:value}))

    def test_legacy_versions_keep_their_original_reading(self):
        self.source(dict(thscode='600036.SH',time=['2026-11-02'],table=dict(ths_pe_ttm_stock=[98765])))
        for policy in (None,1):
            legacy=deepcopy(self.task)
            if policy is None:legacy.pop(POLICY)
            else:legacy[POLICY]=policy
            with self.subTest(policy=policy):self.assertEqual([r['value'] for r in observations(legacy)],[98765])

    def test_partial_sequence_saves_both_report_profiles_and_reopens_without_new_calls(self):
        from lxml import html
        with tempfile.TemporaryDirectory() as folder:
            archive=ResearchArchive(folder)
            agent=ResearchAgent(archive,ROOT,offline=True,draft_first=True)
            try:
                self.task=agent.create('核对600519.SH已取得的PE读数',objects=['600519.SH'],cutoff=self.task['cutoff'])
                self.assertEqual(self.task[POLICY],SCOPE_VERSION)
                self.source(dict(thscode='600519.SH',time=['2026-10-29','2026-10-30','2026-11-02'],
                                 table=dict(ths_pe_ttm_stock=[20,21,98765],unexpected=[99999,99999,99999])))
                retained=deepcopy(self.task['source_records']);archive.save(self.task)
                done=agent.start(self.task['id'],dict(data_requests=[],draft_report=dict(title='估值读数观察',
                    conclusion='仅比较已取得的同标的读数，不判断估值高低。',logics=[dict(title='观察窗口',
                    analysis='同一标的的两次PE读数不同，不能据此预测收益。',data_refs=['D1','D2'])])))
                self.assertTrue(done.get('delivery'),done.get('error'))
                self.assertEqual([r['value'] for r in done['draft_report']['facts']],[20,21])
                self.assertEqual(done['source_records'],retained)
                self.assertEqual(done['ledger']['used']['model_calls'],0)
                self.assertEqual(done['ledger']['used']['data_requests'],0)
                for profile in ('standard','detailed'):
                    from researchhelper.reports import render
                    content=archive.report(done['id'],done['version'])['content']
                    visible=html.document_fromstring(render(content,ROOT,report_profile=profile)).xpath('//main')[0].text_content()
                    self.assertNotIn('98765',visible);self.assertNotIn('99999',visible)
                ident=done['id'];digest=archive.report(ident,1)['digest']
            finally:agent.close_runtime()
            reopened=ResearchArchive(folder)
            self.assertEqual(reopened.report(ident,1)['digest'],digest)
            self.assertEqual(reopened.get(ident)['source_records'],retained)


if __name__=='__main__':unittest.main()
