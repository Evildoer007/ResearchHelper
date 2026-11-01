"""Latest bounded failure replaces stale UI wording, not original evidence."""
import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from researchhelper import argument_support
from researchhelper.contracts import budget, charge
from researchhelper.engine import ResearchAgent
from researchhelper.model import ModelCompletionError


class CurrentExtractionFailureTests(unittest.TestCase):
    def setUp(self):
        self.task = dict(id='synthetic-failure-control',version=1,batch=1,intent='综合研究',
            argument_support_contract=argument_support.VERSION,extraction_submission_recovery_version=1,
            extraction_value_recovery_version=1,reasoning_state={},ledger=budget())
        self.first = dict(kind='model_calls',research_phase='extract',batch=1,status='失败',
            failure_code='structured_output_rejected',structured_failure_category='invalid_argument_json',
            provider_completion_status='completed',response_body_complete=True,finish_reason='stop',
            structured_output_diagnostic=dict(version=1,json_error='duplicate_key',automatic_repair=False))
        self.task['ledger']['dispatches'].append(copy.deepcopy(self.first))
        charge(self.task['ledger'],'model_calls')
        self.agent = ResearchAgent.__new__(ResearchAgent)
        self.agent.model = SimpleNamespace()
        self.agent._active = Mock(return_value=self.task)
        self.agent._save_model_checkpoint = Mock()

    def fail(self, *, diagnostic=None, category='invalid_original_spans', append=True):
        def attempt(model,task,phase,payload,expected):
            if append:
                charge(task['ledger'],'model_calls')
                event = dict(self.first,structured_failure_category=category,
                    structured_output_diagnostic=diagnostic or dict(version=1,
                        schema_issues=[dict(path='$.selections[*].additional_contexts',code='missing_field')],
                        automatic_repair=False,submitted_values_retained=False))
                task['ledger']['dispatches'].append(event)
            raise ModelCompletionError('PRIVATE_PROVIDER_BODY_NOT_TO_RETAIN')
        with patch('researchhelper.engine.model_object',side_effect=attempt) as model:
            result = self.agent._recover_completed_extraction_format(self.task,dict(questions=[]),
                'control-input',failed_dispatch_index=0)
        self.assertIsNone(result)
        return model

    def message(self):
        return self.agent._extraction_failure_message(self.task,'control-input',
            ModelCompletionError('首次失败：重复JSON字段'))

    def test_latest_structure_rejection_does_not_repeat_first_duplicate_error(self):
        before = copy.deepcopy(self.task['ledger']['limits'])
        self.fail()
        state = self.task['reasoning_state']['extraction_submission_recovery']
        self.assertEqual(state['format_issue']['json_error'],'duplicate_key')
        self.assertEqual(state['last_failure']['dispatch_index'],1)
        self.assertEqual(state['last_failure']['structured_category'],'invalid_original_spans')
        self.assertIn('字段结构',self.message())
        self.assertNotIn('重复',self.message())
        self.assertEqual(self.task['ledger']['limits'],before)
        self.assertEqual(self.task['ledger']['used']['model_calls'],2)
        self.assertEqual(self.task['ledger']['used']['data_requests'],0)
        self.assertNotIn('PRIVATE_PROVIDER',json.dumps(self.task))

    def test_latest_duplicate_or_nonfinite_remains_a_real_failure(self):
        self.fail(diagnostic=dict(version=1,json_error='duplicate_key'),category='invalid_argument_json')
        self.assertIn('仍含重复',self.message())
        event = self.task['ledger']['dispatches'][-1]
        event['structured_output_diagnostic']['json_error']='nonfinite_constant'
        recovery = self.task['reasoning_state']['extraction_submission_recovery']
        recovery['last_failure']['diagnostic']=copy.deepcopy(event['structured_output_diagnostic'])
        self.assertIn('非有限',self.message())

    def test_no_second_dispatch_does_not_invent_a_new_provider_failure(self):
        self.fail(append=False)
        recovery = self.task['reasoning_state']['extraction_submission_recovery']
        self.assertNotIn('last_failure',recovery)
        self.assertIn('首次失败',self.message())
        self.assertEqual(self.task['ledger']['used']['model_calls'],1)

    def test_stale_input_batch_or_changed_event_cannot_replace_current_message(self):
        self.fail()
        original = copy.deepcopy(self.task)
        self.task['reasoning_state']['extraction_submission_recovery']['input_digest']='foreign'
        self.assertIn('首次失败',self.message())
        self.task=copy.deepcopy(original);self.task['batch']=2
        self.assertIn('首次失败',self.message())
        self.task=copy.deepcopy(original);self.task['ledger']['dispatches'][-1]['status']='成功'
        self.assertIn('首次失败',self.message())
        self.task=copy.deepcopy(original)
        self.task['ledger']['dispatches'][-1]['structured_output_diagnostic']={}
        self.assertIn('首次失败',self.message())

    def test_failed_recovery_still_cannot_make_a_third_model_request(self):
        self.fail()
        with patch('researchhelper.engine.model_object') as model:
            self.assertIsNone(self.agent._recover_completed_extraction_format(self.task,dict(questions=[]),
                'control-input',failed_dispatch_index=1))
        model.assert_not_called()
        self.assertEqual(self.task['ledger']['used']['model_calls'],2)
