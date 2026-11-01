"""One known-complete address resubmission; no numeric or source repair."""
import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from tests import ROOT
from researchhelper.contracts import ContractError,budget,charge,fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.output_schema import (object_schema,array_schema,
    registered_literal_choice_issues,original_span_output_schema)
from tests.probe_etf_extraction_context import payload_for

POLICY='original_literal_choice_recovery_version'
PATH='$.selections[*].number_reference.literal_ref'


class LiteralChoiceRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.schema=object_schema(dict(selections=array_schema(object_schema(dict(
            number_reference=object_schema(dict(literal_ref=dict(type='string',enum=['n1_1','n1_2']))))))))
        self.event=dict(kind='model_calls',research_phase='extract',status='失败',batch=1,
            failure_code='structured_output_rejected',structured_failure_category='invalid_original_spans',
            provider_completion_status='completed',response_body_complete=True,finish_reason='stop',
            output_protocol='strict_original_spans_v1',output_schema_hash=fingerprint(self.schema),
            network_request_started=True,network_worker_reaped=True,
            structured_output_diagnostic=dict(version=1,automatic_repair=False,
                submitted_values_retained=False,schema_issues=[dict(path=PATH,code='enum_mismatch')]))
        ledger=budget();ledger['used']['model_calls']=4;ledger['dispatches']=[self.event]
        self.task=dict(id='fixture',version=1,batch=1,intent='综合研究',status='运行中',
            argument_support_contract=2,original_span_diagnostics_version=1,
            extraction_submission_recovery_version=1,ledger=ledger,reasoning_state={},
            evidence=[],calculations=[],**{POLICY:1})
        self.payload=dict(untrusted_sources=[dict(id='fixture')])
        self.agent=ResearchAgent.__new__(ResearchAgent);self.agent.model=SimpleNamespace()
        self.agent._active=Mock(return_value=self.task);self.agent._save_model_checkpoint=Mock()
        schema=patch('researchhelper.output_schema.original_span_output_schema',return_value=self.schema)
        schema.start();self.addCleanup(schema.stop)
        network=patch('socket.socket.connect',side_effect=AssertionError('Offline selection test'))
        network.start();self.addCleanup(network.stop)

    def recover(self):
        return self.agent._recover_completed_extraction_format(self.task,self.payload,'same-input',failed_dispatch_index=0)

    def submit(self,model,task,phase,payload,expected):
        self.assertEqual(phase,'extract');self.assertEqual(payload,self.payload)
        self.assertEqual(expected,{'evidence_specs'});charge(task['ledger'],'model_calls')
        return dict(evidence_specs=[]),dict(phase='extract')

    def test_only_one_same_payload_call_without_adoption_or_budget_reset(self):
        before=copy.deepcopy(self.payload);limits=copy.deepcopy(self.task['ledger']['limits'])
        with patch('researchhelper.engine.model_object',side_effect=self.submit) as call:
            self.assertEqual(self.recover()[0],dict(evidence_specs=[]));self.assertIsNone(self.recover())
        self.assertEqual(call.call_count,1);self.assertEqual(self.task['ledger']['used']['model_calls'],5)
        self.assertEqual(self.task['ledger']['limits'],limits);self.assertEqual(self.payload,before)
        marker=self.task['reasoning_state']['extraction_submission_recovery']
        self.assertEqual(marker['format_issue']['category'],'completed_original_literal_choice')
        self.assertFalse(marker['automatic_repair']);self.assertFalse(marker['further_acquisition_allowed'])
        self.assertEqual(self.task['evidence'],[]);self.assertEqual(self.task['calculations'],[])

    def test_semantic_enums_other_sources_and_mixed_errors_are_not_eligible(self):
        diagnostic=self.event['structured_output_diagnostic']
        for path in ('$.binding_ref','$.selections[*].source_ref','$.selections[*].unit',
                     '$.selections[*].contexts[*].value','$.selections[*].number_ref',
                     '$.selections[*].temporal_reference.period_ref'):
            with self.subTest(path=path):
                changed=dict(diagnostic,schema_issues=[dict(path=path,code='enum_mismatch')])
                self.assertIsNone(registered_literal_choice_issues(self.schema,changed))
        mixed=dict(diagnostic,schema_issues=diagnostic['schema_issues']+[dict(path='$.binding_ref',code='enum_mismatch')])
        self.assertIsNone(registered_literal_choice_issues(self.schema,mixed))

    def test_unknown_transport_truncation_batch_or_schema_stops_without_call(self):
        original=copy.deepcopy(self.event)
        for changes in (dict(status='已派发'),dict(response_body_complete=False),dict(finish_reason='length'),
            dict(network_request_started=False),dict(network_worker_reaped=False),dict(batch=0),
            dict(output_schema_hash='different'),dict(failure_code='http_failure')):
            self.event.clear();self.event.update(copy.deepcopy(original));self.event.update(changes)
            with self.subTest(changes=changes),patch('researchhelper.engine.model_object') as call:
                self.assertIsNone(self.recover());call.assert_not_called()

    def test_legacy_invalid_policy_or_insufficient_reserves_never_resubmit(self):
        for value in (None,True,False,0,2,1.0):
            self.task[POLICY]=value
            with self.subTest(value=value),patch('researchhelper.engine.model_object') as call:
                self.assertIsNone(self.recover());call.assert_not_called()
        self.task[POLICY]=1
        for changes in (dict(model_calls=5),dict(seconds=550)):
            used=dict(self.task['ledger']['used']);self.task['ledger']['used'].update(changes)
            with self.subTest(changes=changes),patch('researchhelper.engine.model_object') as call:
                self.assertIsNone(self.recover());call.assert_not_called()
            self.task['ledger']['used']=used

    def test_second_failure_is_journaled_and_never_repeated(self):
        with patch('researchhelper.engine.model_object',side_effect=ContractError('Second rejection')) as call:
            self.assertIsNone(self.recover());self.assertIsNone(self.recover())
        self.assertEqual(call.call_count,1)
        self.assertEqual(self.task['reasoning_state']['extraction_submission_recovery']['status'],'未采用')

    def test_diagnostic_contains_only_registered_path_not_rejected_value(self):
        d=self.event['structured_output_diagnostic']
        self.assertEqual(registered_literal_choice_issues(self.schema,d),d['schema_issues'])
        for changes in (dict(version=True),dict(automatic_repair=True),dict(submitted_values_retained=True),
            dict(schema_issues=[]),dict(schema_issues=[dict(path=PATH,code='enum_mismatch',value='unknown')])):
            self.assertIsNone(registered_literal_choice_issues(self.schema,dict(d,**changes)))

    def test_actual_closed_failure_identified_without_reopening_or_approving(self):
        path=ROOT/'result/acceptance/one-sentence-research-f_u3gj2f/task.json';raw=path.read_bytes()
        task=json.loads(raw);schema=original_span_output_schema(task,payload_for(task)['untrusted_sources'])
        event=next(e for e in task['ledger']['dispatches'] if e.get('research_phase')=='extract')
        self.assertEqual(event['output_schema_hash'],fingerprint(schema))
        self.assertEqual(registered_literal_choice_issues(schema,event['structured_output_diagnostic']),
            [dict(path=PATH,code='enum_mismatch')])
        self.assertEqual(task['status'],'部分完成');self.assertEqual(task['evidence'],[])
        self.assertEqual(path.read_bytes(),raw)
