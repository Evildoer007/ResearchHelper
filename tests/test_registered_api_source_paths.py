"""Source readiness recognizes selectable API roles without adopting them."""
import copy
import unittest
from unittest.mock import patch

try:
    from . import test_sse_etf_price_context as fixtures
except ImportError:
    import test_sse_etf_price_context as fixtures

from researchhelper.acquisition import _with_method_source_coverage
from researchhelper.reasoning import extractable_source_views
from researchhelper.evidence import api_cell_choices
from researchhelper.contracts import ContractError
from researchhelper.planning import input_fingerprint

POLICY='registered_api_source_paths_version'


class RegisteredAPISourcePathsTests(unittest.TestCase):
    def setUp(self):
        self.control=fixtures.SSEETFPcfPriceContextTests();self.control.setUp()
        self.addCleanup(self.control.doCleanups)
        self.task=self.control.task
        for source in self.task['source_records']:source['question_ids']=['q1']
        self.views=extractable_source_views(self.task)

    def coverage(self,task=None,views=None,ready=True):
        return _with_method_source_coverage(task or self.task,self.views if views is None else views,
            dict(ready=ready,financial_approval=False))

    def test_nav_fixed_role_and_selectable_share_proof_make_a_source_path(self):
        self.assertEqual(self.task[POLICY],1)
        before=copy.deepcopy(self.task)
        result=self.coverage()
        self.assertTrue(result['ready']);self.assertEqual(result['pending_method_inputs'],[])
        price=next(x for x in result['method_inputs'] if x['role']=='close_price')
        nav=next(x for x in result['method_inputs'] if x['role']=='official_nav')
        self.assertIn('share_class',price['missing_api_context_fields'])
        self.assertEqual(price['missing_source_path_fields'],[])
        self.assertEqual(price['selectable_context_references'][0]['source_artifact_id'],self.control.nav['id'])
        self.assertEqual(nav['api_source_ids'],[self.control.nav['id']])
        self.assertTrue(all(not x['financial_approval'] and not x['numeric_values_approved']
            and not x['automatic_context_selection'] for x in result['method_inputs']))
        self.assertEqual(self.task,before)
        legacy=copy.deepcopy(self.task);legacy.pop(POLICY)
        self.assertNotEqual(input_fingerprint(self.task),input_fingerprint(legacy))

    def test_price_only_cannot_discharge_missing_official_nav(self):
        task=copy.deepcopy(self.task);task['source_records']=[self.control.price]
        views=[v for v in self.views if v['id']==self.control.price['id']]
        result=self.coverage(task,views)
        self.assertFalse(result['ready'])
        self.assertTrue(any(x['role']=='official_nav' for x in result['pending_method_inputs']))

    def test_nav_only_cannot_discharge_missing_price(self):
        task=copy.deepcopy(self.task);task['source_records']=[self.control.nav]
        views=[v for v in self.views if v['id']==self.control.nav['id']]
        self.assertFalse(self.coverage(task,views)['ready'])

    def test_unread_nav_is_not_used_for_a_supplementary_source_path(self):
        views=[v for v in self.views if v['id']!=self.control.nav['id']]
        result=self.coverage(views=views)
        self.assertFalse(result['ready'])
        price=next(x for x in result['method_inputs'] if x['role']=='close_price')
        self.assertEqual(price['selectable_context_references'],[])

    def test_share_link_does_not_supply_missing_currency_or_merge_other_paths(self):
        original=api_cell_choices
        def incomplete(task,record,*args,**kwargs):
            values=original(task,record,*args,**kwargs)
            if record['id']==self.control.price['id']:
                for value in values:value['calculation_context'].pop('currency')
            return values
        with patch('researchhelper.evidence.api_cell_choices',side_effect=incomplete):
            self.assertFalse(self.coverage()['ready'])

    def test_legacy_policy_and_old_share_permissions_keep_original_requirements(self):
        for changes in ({POLICY:None},{'official_etf_pcf_version':1}):
            self.assertFalse(self.coverage(dict(self.task,**changes))['ready'])

    def test_historical_and_document_contexts_do_not_gain_this_current_path(self):
        from researchhelper import acquisition
        with patch.object(acquisition,'_selectable_api_source_paths',side_effect=AssertionError('Current only')):
            for changes in (dict(research_mode='historical'),dict(intent='原件内容核对')):
                self.assertFalse(self.coverage(dict(self.task,**changes))['ready'])

    def test_other_unresolved_source_obligations_are_not_removed(self):
        result=self.coverage(ready=False)
        self.assertFalse(result['ready'])
        self.assertTrue(all(x['source_path_ready'] for x in result['method_inputs']))
        self.assertFalse(result['financial_approval'])

    def test_unknown_or_boolean_versions_are_rejected(self):
        for value in (True,2,0,'1'):
            with self.subTest(value=value),self.assertRaises(ContractError):
                self.coverage(dict(self.task,**{POLICY:value}))


if __name__=='__main__':unittest.main()
