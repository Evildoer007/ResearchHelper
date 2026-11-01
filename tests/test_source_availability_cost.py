"""Availability must not build page-level extraction or approve financial data."""
import copy
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.acquisition import _with_method_source_coverage


class SourceAvailabilityCostTests(unittest.TestCase):
    def task(self, methods=None):
        return dict(id='availability_fixture',version=1,method_source_coverage_version=1,
                    question='核对600030.SH的报表原值',objects=[],
                    questions=[dict(id='q1',question='核对600030.SH的报表原值',required=True,
                                    dependencies=[],method_needs=methods or [])],
                    source_records=[],materials=[])

    def test_no_calculation_never_builds_original_extraction_contract(self):
        task=self.task();before=copy.deepcopy(task)
        with patch('researchhelper.reasoning.evidence_contract',side_effect=AssertionError('Wrong layer')):
            result=_with_method_source_coverage(task,[dict(id='large_original',kind='filing')],
                dict(ready=False,financial_approval=False))
        self.assertFalse(result['ready']);self.assertEqual(result['method_inputs'],[])
        self.assertEqual(result['pending_method_inputs'],[]);self.assertEqual(task,before)

    def test_readable_material_is_only_a_potential_input_path(self):
        task=self.task(['net_profit_margin_v1'])
        record=dict(id='original',kind='material',task_id=task['id'],research_version=1,question_ids=['q1'])
        task['materials']=[record]
        with patch('researchhelper.reasoning.evidence_contract',side_effect=AssertionError('Wrong layer')):
            result=_with_method_source_coverage(task,[dict(id='original',kind='material')],
                dict(ready=True,financial_approval=False))
        self.assertTrue(result['method_inputs'])
        self.assertTrue(all(row['original_source_ids']==['original'] for row in result['method_inputs']))
        self.assertTrue(all(not row['financial_approval'] and not row['numeric_values_approved']
                            for row in result['method_inputs']))

    def test_foreign_original_cannot_satisfy_a_method_source_path(self):
        task=self.task(['net_profit_margin_v1'])
        task['materials']=[dict(id='foreign',kind='material',task_id='other',research_version=1,question_ids=['q1'])]
        result=_with_method_source_coverage(task,[dict(id='foreign',kind='material')],dict(ready=True))
        self.assertFalse(result['ready'])
        self.assertTrue(result['pending_method_inputs'])
        self.assertTrue(all(not row['original_source_ids'] for row in result['method_inputs']))


if __name__=='__main__':unittest.main()
