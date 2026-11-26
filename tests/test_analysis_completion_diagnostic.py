"""A complete but rejected model result is not a provider interruption."""
import copy
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, context
from researchhelper.model import ModelService, ModelSettings, analysis_completion_failure
from researchhelper.output_schema import analysis_output_schema
from researchhelper.planning import input_fingerprint
from tests.legacy_replay_fixtures import financial_task


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

    def test_archived_model_rejection_is_classified_without_reopening_or_rewriting(self):
        task = financial_task(both_ratios=True)
        task.update(analysis_citation_encoding=2, analysis_question_scope_version=1,
                    analysis_question_map_version=1)
        task['input_hash'] = input_fingerprint(task)
        task['ledger']['used']['model_calls'] = 0
        schema = analysis_output_schema(task)
        policy_question, foreign = 'financial', 'management'
        answers = dict.fromkeys(schema['properties']['answers']['properties'])
        answers[policy_question] = dict(segments=[dict(text='原件口径。', refs=[foreign], counter_refs=[])],
                                        knowledge_refs=[], provenance_refs=[])
        wire = dict(version=3, binding=context(task), answers=answers, theses=[])
        response = dict(object='response', status='completed', error=None, incomplete_details=None,
                        output=[dict(type='message', role='assistant', status='completed',
                                     content=[dict(type='output_text', text=json.dumps(wire))])], usage={})
        with TemporaryDirectory(prefix='rh-analysis-completion-') as directory:
            settings = ModelSettings(Path(directory) / 'model')
            settings.save(dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash',
                               api_key='FAKE_TEST_KEY'))
            archive = ResearchArchive(Path(directory) / 'store')
            task.pop('revision', None)
            task['status'] = '运行中'
            archive.save(task)
            model = ModelService(settings, offline=False, journal=archive.save)
            body = BytesIO(json.dumps(response).encode())
            with patch('researchhelper.model.open_verified_https', return_value=body) as request:
                with self.assertRaises(ContractError):
                    model.reason_structured_phase(task, [], 'analyze', schema)
            request.assert_called_once()

            task['status'] = '失败'
            archive.save(task)
            archived = archive.get(task['id'])
            expected = dict(field='analysis.output_selection', reason='分析引用不属于对应问题的允许范围')
            self.assertEqual(analysis_completion_failure(archived), expected)
            self.assertEqual(archived, archive.get(task['id']))

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
