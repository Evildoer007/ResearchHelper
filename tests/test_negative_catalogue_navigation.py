"""A distinct classified empty search is coverage, not evidence or a refund."""
import copy
import json
import unittest
from unittest.mock import patch

from tests import ROOT
from tests import test_initial_acquisition_navigation as fixtures
from researchhelper.acquisition import negative_catalogue_searches, navigation_progress as real_progress
from researchhelper.contracts import fingerprint
from researchhelper import acquisition


class NegativeCatalogueNavigationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.InitialAcquisitionNavigationTests()
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.task,self.engine=self.fixture.task,self.fixture.engine
        self.task['catalogue_search_navigation_version']=1
        self.task['research_mode']='current'
        self.task['data_need_specs']=[]
        self.first=self.request('first','2026-06-30','2026-07-15')
        self.second=self.request('second','2026-06-30','2026-10-15')
        self.fixture.decisions=[dict(data_requests=[request],stop_reason='') for request in (self.first,self.second)]
        self.progress=patch.object(acquisition,'navigation_progress',side_effect=real_progress)
        self.progress.start();self.addCleanup(self.progress.stop)

    @staticmethod
    def request(ident,start,end):
        return dict(id=ident,source='ifind',operation='report_query',question_ids=['q1'],
                    parameters=dict(codes='510050.SH',beginrDate=start,endrDate=end,functionpara={}))

    def classified_empty(self,task,needs):
        request=needs[0]
        key=fingerprint(dict(source='ifind',operation='report_query',parameters=request['parameters']))
        task['ledger']['used']['data_requests']+=1
        task['ledger']['dispatches'].append(dict(kind='data_requests',source='ifind',operation='report_query',
            fingerprint=key,status='未返回数据',business_code=-4001,supplier_rejection_category='no_data'))

    def test_distinct_empty_query_can_be_followed_by_one_explicit_changed_window(self):
        def acquire(task,needs):
            if self.engine._acquire_sources.call_count==1:self.classified_empty(task,needs)
            else:
                task['ledger']['used']['data_requests']+=1
                self.fixture.stage=3
        self.engine._acquire_sources.side_effect=acquire
        self.fixture.run_navigation()
        self.assertEqual(self.fixture.model.call_count,2)
        self.assertEqual(self.task['ledger']['used']['data_requests'],2)
        self.assertEqual(self.task['ledger']['supplements'],0)
        self.assertEqual(self.task['reasoning_state']['acquisition']['status'],'complete')
        self.assertEqual(self.task['reasoning_state']['acquisition']['decisions'][0]['progress'],[0,0,0,1])
        payload=self.fixture.model.call_args.args[3]
        self.assertEqual(len(payload['negative_catalogue_searches']),1)
        self.assertFalse(payload['negative_catalogue_searches'][0]['financial_approval'])
        self.assertFalse(payload['negative_catalogue_searches'][0]['absence_of_event_proven'])
        self.assertIn('current_disclosure_window',payload['navigation'])

    def test_same_request_never_gains_second_progress_or_additional_query(self):
        self.fixture.decisions=[dict(data_requests=[self.first],stop_reason='')]*3
        def acquire(task,needs):
            if self.engine._acquire_sources.call_count==1:self.classified_empty(task,needs)
        self.engine._acquire_sources.side_effect=acquire
        self.fixture.run_navigation();self.fixture.run_navigation()
        self.assertEqual(self.fixture.model.call_count,2)
        self.assertEqual(self.task['ledger']['used']['data_requests'],1)
        self.assertEqual(self.task['reasoning_state']['acquisition']['decisions'][-1]['progress'],[0,0,0,0])

    def test_legacy_query_keeps_old_stop_behavior(self):
        self.task.pop('catalogue_search_navigation_version')
        self.engine._acquire_sources.side_effect=self.classified_empty
        self.fixture.run_navigation()
        self.assertEqual(self.fixture.model.call_count,1)
        self.assertEqual(negative_catalogue_searches(self.task),[])

    def test_unknown_transport_permission_and_unbound_events_are_not_search_coverage(self):
        self.task['data_need_specs']=[self.first]
        self.classified_empty(self.task,[self.first])
        valid=copy.deepcopy(self.task['ledger']['dispatches'][-1])
        for change in (dict(status='已派发'),dict(status='传输失败'),dict(business_code=-401),
                       dict(supplier_rejection_category='permission_denied'),dict(fingerprint='foreign'),dict(operation='get_access_token')):
            self.task['ledger']['dispatches'][-1]=dict(valid,**change)
            with self.subTest(change=change):self.assertEqual(negative_catalogue_searches(self.task),[])
        self.task['ledger']['dispatches']=[valid,copy.deepcopy(valid)]
        self.assertEqual(len(negative_catalogue_searches(self.task)),1)

    def test_three_decision_limit_and_budget_remain_in_force(self):
        requests=[self.request('search_'+str(i),'2026-06-30','2026-07-'+str(i+1).zfill(2)) for i in range(3)]
        self.fixture.decisions=[dict(data_requests=[need],stop_reason='') for need in requests]
        self.engine._acquire_sources.side_effect=self.classified_empty
        limits=copy.deepcopy(self.task['ledger']['limits'])
        self.fixture.run_navigation();self.fixture.run_navigation()
        self.assertEqual(self.fixture.model.call_count,3)
        self.assertEqual(self.task['ledger']['limits'],limits)
        self.assertEqual(self.task['ledger']['used']['data_requests'],3)
        self.assertEqual(self.task['ledger']['supplements'],0)
        self.assertEqual(self.task['reasoning_state']['acquisition']['status'],'stopped')


class RetainedNegativeSearchTests(unittest.TestCase):
    def test_actual_closed_failure_remains_closed_while_query_scope_is_explainable(self):
        source=ROOT/'result/acceptance/one-sentence-research-tgas_d2l/task.json'
        original=source.read_bytes();task=json.loads(original)
        task['catalogue_search_navigation_version']=1
        found=negative_catalogue_searches(task)
        self.assertEqual(len(found),1)
        self.assertEqual(found[0]['parameters']['codes'],'510050.SH')
        self.assertFalse(found[0]['financial_approval'])
        self.assertEqual(task['status'],'部分完成')
        self.assertFalse(task['calculations'])
        self.assertEqual(source.read_bytes(),original)


if __name__=='__main__':
    unittest.main()
