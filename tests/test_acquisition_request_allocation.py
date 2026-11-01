"""New request IDs are compiled locally; source and catalog identities are not."""
import copy
import importlib
import unittest

from researchhelper.acquisition import allocate_request_ids

Fixture = importlib.import_module('tests.test_initial_acquisition_navigation' if __package__
                                 else 'test_initial_acquisition_navigation').InitialAcquisitionNavigationTests


class RequestAllocationTests(unittest.TestCase):
    def setUp(self):
        self.need = {'id':'dr1','source':'ifind','operation':'filing_original',
            'parameters':{'candidate_id':'catalog_original_1','artifact_id':'source_original_1'},
            'question_ids':['q1']}
        self.task = {'data_need_specs':[dict(self.need,operation='get_thscode')]}

    def test_response_local_id_does_not_overwrite_prior_request(self):
        allocated,mapping = allocate_request_ids(self.task,[{'status':'dispatched'}],[self.need])
        self.assertEqual(allocated[0]['id'],'navigation_1_filing_original_1')
        self.assertEqual(mapping,[{'proposal_id':'dr1','request_id':allocated[0]['id']}])
        self.assertEqual(self.task['data_need_specs'][0]['id'],'dr1')

    def test_catalog_and_source_references_are_untouched(self):
        allocated,_ = allocate_request_ids(self.task,[{}],[self.need])
        self.assertEqual(allocated[0]['parameters'],self.need['parameters'])
        self.assertEqual(allocated[0]['question_ids'],['q1'])

    def test_inputs_remain_detached(self):
        before=copy.deepcopy((self.task,self.need))
        allocated,_=allocate_request_ids(self.task,[{}],[self.need])
        allocated[0]['parameters']['candidate_id']='changed'
        self.assertEqual((self.task,self.need),before)

    def test_collision_with_initial_or_prior_navigation_is_avoided(self):
        self.task['data_need_specs'][0]['id']='navigation_2_filing_original_1'
        decisions=[{'data_requests':[dict(self.need,id='navigation_2_filing_original_2')]},{}]
        allocated,_=allocate_request_ids(self.task,decisions,[self.need])
        self.assertEqual(allocated[0]['id'],'navigation_2_filing_original_3')

    def test_multiple_proposals_get_distinct_deterministic_ids(self):
        needs=[self.need,dict(self.need,id='dr2')]
        first=allocate_request_ids(self.task,[{}],needs)
        self.assertEqual(first,allocate_request_ids(self.task,[{}],needs))
        self.assertEqual(len({item['id'] for item in first[0]}),2)

    def test_empty_allocation_neither_creates_requests_nor_mutates_ledger(self):
        self.task['ledger']={'used':{'model_calls':3,'data_requests':2},'supplements':0}
        before=copy.deepcopy(self.task)
        self.assertEqual(allocate_request_ids(self.task,[{}],[]),([],[]))
        self.assertEqual(self.task,before)


class VersionedAllocationNavigationTests(unittest.TestCase):
    setUp=Fixture.setUp
    proposal=staticmethod(Fixture.proposal)
    respond=Fixture.respond
    acquire=Fixture.acquire
    run_navigation=Fixture.run_navigation

    def test_version_two_local_ids_preserve_all_three_navigation_steps(self):
        original=self.respond
        def response(*args):
            proposal,receipt=original(*args)
            proposal['data_requests'][0]['id']='dr1'
            return proposal,dict(receipt,acquisition_rules_version=2)
        self.model.side_effect=response
        self.task['data_need_specs']=[dict(self.proposal('get_thscode')['data_requests'][0],id='dr1')]
        self.run_navigation()
        decisions=self.task['reasoning_state']['acquisition']['decisions']
        self.assertEqual(len(decisions),3)
        self.assertEqual(self.task['reasoning_state']['acquisition']['status'],'complete')
        self.assertEqual(len({d['data_requests'][0]['id'] for d in decisions}),3)
        self.assertEqual(self.task['ledger']['used']['model_calls'],3)
        self.assertEqual(self.task['ledger']['supplements'],0)

    def test_legacy_same_id_different_request_remains_rejected(self):
        self.task['data_need_specs']=[dict(self.proposal('report_query')['data_requests'][0],id='get_thscode')]
        self.run_navigation()
        self.engine._acquire_sources.assert_not_called()
        self.assertIn('标识已绑定',self.task['gaps'][-1])


del Fixture
