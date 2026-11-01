"""Retained actual 46-row catalogue and isolated large-index negative cases."""
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from researchhelper.contracts import fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import filing_candidate_choices,data_request_contract,snapshot_record

ROOT=Path(__file__).resolve().parents[1]
ORIGINAL=ROOT/'result/acceptance/default-app-investment-resume-vtrz1pya/task.json'


class FilingChoiceCoverageTests(unittest.TestCase):
    def setUp(self):
        self.before=hashlib.sha256(ORIGINAL.read_bytes()).hexdigest()
        self.task=json.loads(ORIGINAL.read_text())
        self.agent=ResearchAgent.__new__(ResearchAgent)
        self.agent.offline=False
        guard=patch('socket.socket.connect',side_effect=AssertionError('No network calls'))
        guard.start();self.addCleanup(guard.stop)

    def tearDown(self):
        self.assertEqual(hashlib.sha256(ORIGINAL.read_bytes()).hexdigest(),self.before)

    def test_actual_periodic_filings_beyond_old_prefix_are_offered(self):
        contract=data_request_contract(self.task)
        tool=next(item for item in contract['tools'] if item['operation']=='filing_original')
        titles=[item['title'] for item in tool['selection_details']]
        self.assertTrue(any('2026年第2季度报告' in title for title in titles))
        self.assertTrue(any('2026年中期报告' in title for title in titles))
        self.assertTrue(any('招募说明书2026年第1号' in title for title in titles))
        self.assertEqual(len(tool['available_selections']),46)
        self.assertFalse(tool['candidate_coverage']['truncated'])

    def test_exact_duplicate_catalogues_are_aliases_not_independent_documents(self):
        index=filing_candidate_choices(self.task)
        self.assertEqual(len(index['selections']),46)
        self.assertTrue(all(len(item['catalog_aliases'])==2 for item in index['details']))
        self.assertEqual(index['coverage']['returned_catalog_rows'],138)
        self.assertEqual(index['coverage']['catalog_completeness'],'未证实')

    def test_no_automatic_selection_approval_or_timezone_repair(self):
        before=fingerprint(self.task)
        index=filing_candidate_choices(self.task)
        self.assertFalse(index['automatic_selection'])
        self.assertFalse(index['automatic_download'])
        for item in index['details']:
            self.assertFalse(item['title_is_body_evidence'])
            self.assertFalse(item['financial_approval'])
            self.assertIsNone(item['published_at'])
        self.assertEqual(fingerprint(self.task),before)

    def test_app_supplement_and_skill_tools_share_unattempted_real_choices(self):
        task=copy.deepcopy(self.task);task['source_attempts']={}
        selected={'q3','q4','q5'}
        app=self.agent._supplement_filing_candidates(task,selected)
        shared=filing_candidate_choices(task,exclude_attempted=True)['selections']
        self.assertEqual([{key:item[key] for key in ('catalog_id','source_id')} for item in app],shared)
        self.assertTrue(all(set(item['question_ids']).issubset(selected) for item in app))

    def test_alias_attempt_cannot_become_new_download_selection(self):
        task=copy.deepcopy(self.task);task['source_attempts']={}
        record=next(item for item in task['source_records'] if item.get('operation')=='report_query')
        params={'catalog_id':record['id'],'source_id':record['catalog_entries'][-1]['source_id']}
        task['source_attempts'][fingerprint(dict(source='ifind',operation='filing_original',parameters=params))]={'status':'失败或中断'}
        index=filing_candidate_choices(task,exclude_attempted=True)
        self.assertNotIn(params['source_id'],[item['source_id'] for item in index['selections']])
        self.assertFalse(index['coverage']['truncated'])

    def test_cross_task_or_version_record_never_creates_choices(self):
        for changes in ({'task_id':'foreign'},{'research_version':99}):
            task=copy.deepcopy(self.task)
            for record in task['source_records']:record.update(changes)
            with self.subTest(changes=changes):
                self.assertEqual(filing_candidate_choices(task)['selections'],[])

    def test_tampered_original_fields_rejected(self):
        task=copy.deepcopy(self.task)
        for record in task['source_records']:
            for entry in record.get('catalog_entries',[]):entry['title']='unsupported replacement title'
        self.assertEqual(filing_candidate_choices(task)['selections'],[])

    def test_offline_or_unrelated_questions_do_not_offer_app_downloads(self):
        self.agent.offline=True
        self.assertEqual(self.agent._supplement_filing_candidates(self.task,{'q3'}),[])
        self.agent.offline=False
        self.assertEqual(self.agent._supplement_filing_candidates(self.task,{'foreign-question'}),[])

    def large_task(self,count):
        task=copy.deepcopy(self.task);task['source_records']=[];task['source_attempts']={}
        params=dict(codes='510300.SH',functionpara={},beginrDate='2026-06-01',endrDate='2026-10-14',
            outputpara='reportDate:Y,thscode:Y,secName:Y,ctime:Y,reportTitle:Y,pdfURL:Y,seq:Y')
        response=dict(errorcode=0,tables=[dict(thscode='510300.SH',secName='合成测试基金',
            reportDate='2026-07-21',ctime='2026-07-21T09:00:00+08:00',reportTitle='合成公告'+str(i),
            pdfURL='https://www.sse.com.cn/fixture_'+str(i)+'.pdf',seq='fixture_'+str(i)) for i in range(count)])
        record=snapshot_record(task,'ifind','report_query',params,response)
        record['question_ids']=['q3'];task['source_records']=[record]
        return task

    def test_choice_capacity_and_truncation_are_explicit(self):
        index=filing_candidate_choices(self.large_task(70))
        self.assertEqual(len(index['selections']),64)
        self.assertEqual(index['coverage']['inspected_rows'],70)
        self.assertTrue(index['coverage']['truncated'])

    def test_scan_capacity_does_not_claim_uninspected_rows_were_checked(self):
        index=filing_candidate_choices(self.large_task(260))
        self.assertEqual(index['coverage']['returned_catalog_rows'],260)
        self.assertEqual(index['coverage']['inspected_rows'],256)
        self.assertTrue(index['coverage']['truncated'])

    def test_question_choices_merge_only_explicit_eligible_alias_bindings(self):
        task=copy.deepcopy(self.task);task['source_attempts']={}
        records=[item for item in task['source_records'] if item.get('operation')=='report_query']
        task['source_records']=records[:2]
        records[0]['question_ids']=['q3'];records[1]['question_ids']=['q4']
        candidates=self.agent._supplement_filing_candidates(task,{'q3','q4','q5'})
        self.assertTrue(all(item['question_ids']==['q3','q4'] for item in candidates))


if __name__=='__main__':unittest.main()
