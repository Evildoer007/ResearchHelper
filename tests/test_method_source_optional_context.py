"""Legacy/scenario methods must not crash original-source discovery."""
import copy
import json
import tempfile
import unittest

from tests import ROOT
from researchhelper.acquisition import _with_method_source_coverage, initial_acquisition_coverage
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import METHOD_REGISTRY
from researchhelper.engine import ResearchAgent


class MethodSourceOptionalContextTests(unittest.TestCase):
    def task(self, method):
        return dict(id='context-test',version=1,batch=1,input_hash='synthetic',cutoff='2026-10-17',
            question='研究510300.SH与159919.SZ',objects=[],questions=[dict(id='q1',
                question='核对510300.SH与159919.SZ的已登记输入',required=True,
                method_needs=[method],dependencies=[],evidence_needs=['必要原值'])],
            source_records=[],materials=[],evidence=[],method_source_coverage_version=1,
            ledger=dict(dispatches=[]))

    def test_all_registered_methods_leave_absent_data_unapproved_without_key_errors(self):
        registry=copy.deepcopy(METHOD_REGISTRY)
        for method in METHOD_REGISTRY:
            task=self.task(method);before=copy.deepcopy(task)
            with self.subTest(method=method):
                result=_with_method_source_coverage(task,[],dict(ready=False,financial_approval=False))
                self.assertFalse(result['ready'])
                self.assertFalse(result['financial_approval'])
                self.assertEqual(task,before)
        self.assertEqual(METHOD_REGISTRY,registry)

    def test_unscoped_method_has_no_invented_source_roles_or_identity_context(self):
        for method in ('fee_difference_v1','residual_present_value_v1'):
            result=_with_method_source_coverage(self.task(method),[],dict(ready=False,financial_approval=False))
            self.assertEqual(result['method_inputs'],[])
            self.assertEqual(result['pending_method_inputs'],[])
            self.assertFalse(result['ready'])

    def test_context_bound_method_still_requires_its_original_numeric_roles(self):
        task=self.task('management_custody_fee_comparison_v1')
        result=_with_method_source_coverage(task,[],dict(ready=False,financial_approval=False))
        self.assertTrue(result['pending_method_inputs'])
        self.assertEqual({item['object'] for item in result['pending_method_inputs']},
                         {'510300.SH','159919.SZ'})
        self.assertEqual({item['role'] for item in result['pending_method_inputs']},
                         {'management_fee','custody_fee'})
        self.assertTrue(all(not item['numeric_values_approved'] for item in result['method_inputs']))

    def test_closed_actual_failure_is_only_inspected_not_resumed_or_rewritten(self):
        path=ROOT/'result/acceptance/autonomous-data-phase-qtb1jxmu/task.json'
        original=path.read_bytes();task=json.loads(original)
        self.assertEqual(task['status'],'失败')
        result=initial_acquisition_coverage(task)
        self.assertFalse(result['ready'])
        self.assertTrue(result['pending_original_question_ids'])
        self.assertEqual(task['evidence'],[])
        self.assertEqual(task['ledger']['used']['data_requests'],3)
        self.assertEqual(path.read_bytes(),original)
