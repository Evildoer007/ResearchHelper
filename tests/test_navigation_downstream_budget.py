"""Keep finite-budget navigation from spending the pending binding call."""
import copy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from researchhelper import acquisition
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.contracts import ContractError,budget
from researchhelper.planning import input_fingerprint
from researchhelper.reports import compose

ROOT=Path(__file__).resolve().parents[1]
POLICY='acquisition_downstream_budget_version'
METHOD='etf_daily_nav_premium_v1'


class NavigationDownstreamBudgetTests(unittest.TestCase):
    def setUp(self):
        self.task=dict(id='downstream_navigation_control',version=1,batch=1,cutoff='2026-10-23',
            input_hash='offline-control',question='核对ETF价格与净值的原始来源。',objects=[],
            intent='综合研究',run_mode='model',questions=[dict(id='q1',required=True,
                evidence_needs=['原始来源'],method_needs=[METHOD])],
            ledger=budget(dict(data_requests=100,model_calls=8,seconds=600)),
            source_records=[],materials=[],data_need_specs=[],calculation_specs=[],gaps=[],
            acquisition_budget_navigation_version=1,acquisition_downstream_budget_version=1)
        self.task['ledger']['used']['model_calls']=1
        self.stage=0
        self.decisions=[dict(data_requests=[dict(id=op,source='ifind',operation=op,
            parameters=dict(synthetic_scheduler_fixture=True),question_ids=['q1'])],stop_reason='')
            for op in ('get_thscode','report_query','report_query','filing_original')]
        def acquire(task,needs):
            self.stage+=1;task['source_records'].append(dict(id=needs[0]['id'],kind='api'))
            task['ledger']['used']['data_requests']+=1
        def respond(*args):
            self.task['ledger']['used']['model_calls']+=1
            return self.decisions.pop(0),dict(synthetic=True)
        engine=SimpleNamespace(offline=False,model=object(),source_credentials={'ifind':{'refresh_token':'synthetic-no-network'}},
            _active_supplement=Mock(return_value=None),_reasoning_available=Mock(return_value=True),
            _save_model_checkpoint=Mock(),_active=Mock(),_validated_data_needs=Mock(),
            _gap=lambda task,text,questions:task['gaps'].append(text),_acquire_sources=Mock(side_effect=acquire))
        replacements=dict(extractable_source_views=lambda task:[dict(id='original')] if self.stage>=4 else [],
            navigation_progress=lambda task:({'mapped'} if self.stage>=1 else set(),
                {str(self.stage)} if self.stage>=2 else set(),{'original'} if self.stage>=4 else set()),
            data_request_contract=lambda task:dict(callable_codes=[],tools=[dict(source='ifind',operation=op)
                for op in ('get_thscode','report_query','filing_original')]),
            question_specs=lambda task:copy.deepcopy(task['questions']),context_sources=lambda task:dict(catalog=[]),
            supplement_request_blocked=lambda *args:False)
        for name,callback in replacements.items():
            guard=patch.object(acquisition,name,side_effect=callback);guard.start();self.addCleanup(guard.stop)
        guard=patch.object(acquisition,'validate_plan');guard.start();self.addCleanup(guard.stop)
        guard=patch.object(acquisition,'model_object',side_effect=respond)
        model=guard.start();self.addCleanup(guard.stop)
        guard=patch('socket.socket.connect',side_effect=AssertionError('No services in navigation check'))
        guard.start();self.addCleanup(guard.stop)
        self.control=SimpleNamespace(model=model,run_navigation=lambda:acquisition.complete_initial_acquisition(engine,self.task))

    def policy(self):return acquisition.acquisition_navigation_budget(self.task)

    def test_pending_binding_keeps_extraction_binding_analysis_and_review(self):
        before=copy.deepcopy(self.task)
        result=self.policy()
        self.assertEqual(result['protected_model_calls'],4)
        self.assertEqual(result['additional_decisions'],3)
        self.assertEqual(result['downstream_protection'],dict(version=1,extraction=1,
            calculation_binding=1,analysis=1,review=1))
        self.assertEqual(self.task,before)

    def test_no_unbound_required_method_keeps_the_ordinary_three_calls(self):
        for changes in (dict(method_needs=[]),dict(required=False),dict(status='已回答')):
            task=copy.deepcopy(self.task);task['questions'][0].update(changes)
            self.assertEqual(acquisition.acquisition_navigation_budget(task)['protected_model_calls'],3)
        self.task['calculation_specs']=[dict(method=METHOD,question_ids=['q1'])]
        self.assertEqual(self.policy()['protected_model_calls'],3)

    def test_multiple_methods_share_one_binding_call_not_one_each(self):
        self.task['questions'][0]['method_needs']=[METHOD,'net_profit_margin_v1','operating_cash_profit_ratio_v1']
        self.assertEqual(self.policy()['protected_model_calls'],4)
        self.assertEqual(self.policy()['downstream_protection']['calculation_binding'],1)

    def test_progressing_navigation_stops_before_spending_the_binding_slot(self):
        before=copy.deepcopy((self.task['ledger']['limits'],self.task['ledger']['reserve']))
        self.control.run_navigation()
        self.assertEqual(self.control.model.call_count,3)
        self.assertEqual(self.task['ledger']['used']['model_calls'],4)
        state=self.task['reasoning_state']['acquisition']
        self.assertEqual(state['status'],'stopped');self.assertEqual(state['binding'][POLICY],1)
        self.assertIn('计算绑定',state['stop_reason'])
        self.assertEqual((self.task['ledger']['limits'],self.task['ledger']['reserve']),before)

    def test_explicit_larger_budget_is_not_replaced_with_a_fixed_navigation_cap(self):
        self.task['ledger']['limits']['model_calls']=20
        self.assertEqual(self.policy()['additional_decisions'],15)
        self.task['ledger']['used']['model_calls']=19
        self.assertEqual(self.policy()['additional_decisions'],0)

    def test_stopped_navigation_is_not_replayed_or_given_a_new_allowance(self):
        self.control.run_navigation();before=copy.deepcopy(self.task['ledger'])
        self.control.run_navigation()
        self.assertEqual(self.control.model.call_count,3)
        self.assertEqual(self.task['ledger'],before)

    def test_legacy_policy_is_unchanged_and_unknown_new_versions_do_not_open_calls(self):
        self.task.pop(POLICY)
        self.assertEqual(self.policy()['protected_model_calls'],3)
        self.assertEqual(self.policy()['additional_decisions'],4)
        for value in (True,2,'1',0,-1):
            self.task[POLICY]=value
            with self.subTest(value=value),self.assertRaises(ContractError):self.policy()
        self.control.model.assert_not_called()

    def test_recorded_boundary_after_three_navigations_has_no_fourth_allowance(self):
        self.task['ledger']['used']['model_calls']=4
        self.assertEqual(self.policy()['additional_decisions'],0)
        legacy=copy.deepcopy(self.task);legacy.pop(POLICY)
        self.assertEqual(acquisition.acquisition_navigation_budget(legacy)['additional_decisions'],1)
        self.assertEqual(self.task['ledger']['limits']['model_calls'],8)

    def test_new_task_policy_is_input_and_report_bound(self):
        resources=Path(os.environ.get('RESEARCHHELPER_SSE_ETF_RESOURCES',str(ROOT)))
        with tempfile.TemporaryDirectory(prefix='rh-downstream-policy-') as folder:
            agent=ResearchAgent(ResearchArchive(folder),resources,offline=True,require_argument_support=True)
            try:
                with patch('socket.socket.connect',side_effect=AssertionError('No services in policy check')):
                    task=agent.create('研究510300.SH的日终折溢价。',entry='ETF',objects=['510300.SH'])
                    self.assertEqual(task[POLICY],1)
                    legacy=copy.deepcopy(task);legacy.pop(POLICY)
                    self.assertNotEqual(input_fingerprint(task),input_fingerprint(legacy))
                    agent._adopt_method_knowledge(task);task['status']='部分完成';task['gaps']=['未执行研究，仅核对预算策略。']
                    self.assertIn(POLICY,json.dumps(compose(task)))
            finally:agent.close_runtime()


if __name__=='__main__':unittest.main()
