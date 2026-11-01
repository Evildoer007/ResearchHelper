"""A complete but rejected model result is not a provider interruption."""
import copy
import json
import unittest

from tests import ROOT
from researchhelper.model import analysis_completion_failure


class AnalysisCompletionDiagnosticTests(unittest.TestCase):
    def task(self, **changes):
        event=dict(kind='model_calls',research_phase='analyze',batch=1,status='失败',
            response_body_complete=True,provider_completion_status='completed',finish_reason='stop',
            failure_code='structured_output_rejected',structured_output_diagnostic=dict(schema_issues=[
                dict(path='$.answers.q1.segments[*].refs[*]',code='enum_mismatch')]))
        event.update(changes)
        return dict(batch=1,ledger=dict(dispatches=[event]))

    def test_known_rejected_reference_has_a_scope_description(self):
        task=self.task();before=copy.deepcopy(task)
        result=analysis_completion_failure(task)
        self.assertEqual(result,dict(field='analysis.output_selection',reason='分析引用不属于对应问题的允许范围'))
        self.assertEqual(task,before)

    def test_actual_closed_failure_is_classified_without_reopening_or_rewriting(self):
        path=ROOT/'result/acceptance/one-sentence-research-zy9srfaq/task.json'
        original=path.read_bytes();task=json.loads(original)
        self.assertEqual(analysis_completion_failure(task)['field'],'analysis.output_selection')
        self.assertEqual(path.read_bytes(),original)

    def test_incomplete_provider_or_other_batch_does_not_get_known_selection_label(self):
        for changes in (dict(response_body_complete=False),dict(provider_completion_status='incomplete'),
                        dict(finish_reason='length'),dict(status='已派发'),dict(batch=0),
                        dict(failure_code='http_failure')):
            with self.subTest(changes=changes):
                self.assertEqual(analysis_completion_failure(self.task(**changes))['field'],'analysis.completion')

    def test_other_schema_failure_is_not_mislabelled_as_reference_scope(self):
        task=self.task(structured_output_diagnostic=dict(schema_issues=[dict(path='$.answers',code='expected_object')]))
        result=analysis_completion_failure(task)
        self.assertEqual(result['field'],'analysis.output_selection')
        self.assertNotIn('对应问题',result['reason'])
