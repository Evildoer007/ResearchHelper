"""Readiness covers each method's source paths, never approves its numbers."""
import copy
import json
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT, test_initial_acquisition_navigation as navigation
from researchhelper import acquisition
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.planning import input_fingerprint, research_brief
from researchhelper.reports import compose


POLICY = 'method_source_coverage_version'


class MethodSourceCoverageTests(unittest.TestCase):
    def setUp(self):
        self.paths = [ROOT / 'result/acceptance' / ('one-sentence-research-' + name) / 'task.json'
                      for name in ('vcehmtgd', '8bhoacvp', 'f6e_kjx1')]
        self.originals = {path: path.read_bytes() for path in self.paths}
        self.task = json.loads(self.originals[self.paths[0]])
        self.task[POLICY] = 1
        initial = self.task['reasoning_state']['plan']['proposed']['data_requests']
        ids = {item['id'] for item in initial}
        self.task['data_need_specs'] = copy.deepcopy(initial)
        self.task['source_records'] = [source for source in self.task['source_records']
            if ids.intersection(source.get('data_need_ids', []))]

    def tearDown(self):
        for path, original in self.originals.items():
            self.assertEqual(path.read_bytes(), original)

    def test_real_api_price_and_raw_nav_do_not_cover_the_nav_method(self):
        before = copy.deepcopy(self.task)
        result = acquisition.initial_acquisition_coverage(self.task)
        self.assertFalse(result['ready'])
        self.assertEqual(result['pending_method_question_ids'], ['q1'])
        self.assertIn('official_nav', [item['role'] for item in result['pending_method_inputs']])
        self.assertFalse(result['financial_approval'])
        self.assertEqual(self.task, before)

    def test_existing_same_fund_original_is_a_path_not_fact_or_method_approval(self):
        task = json.loads(self.originals[self.paths[1]]); task[POLICY] = 1
        result = acquisition.initial_acquisition_coverage(task)
        self.assertTrue(result['ready'])
        self.assertFalse(result['pending_method_inputs'])
        self.assertTrue(all(item['original_source_ids'] for item in result['method_inputs']))
        self.assertFalse(result['financial_approval'])
        self.assertFalse(task['calculations'])

    def test_wrong_fund_original_cannot_cover_requested_object(self):
        task = json.loads(self.originals[self.paths[1]]); task[POLICY] = 1
        task['question'] = task['question'].replace('588000.SH', '159901.SZ')
        task['objects'] = ['159901.SZ']
        task['questions'][0]['question'] = task['question']
        result = acquisition.initial_acquisition_coverage(task)
        self.assertFalse(result['ready'])
        self.assertTrue(all(item['object'] == '159901.SZ' for item in result['method_inputs']))
        self.assertTrue(all(not item['original_source_ids'] for item in result['method_inputs']))

    def test_price_only_source_does_not_require_pdf(self):
        self.task['questions'][0].update(question='研究159901.SZ的区间收盘价变化',
            method_needs=['observed_price_return_v1'], evidence_needs=['未复权收盘价格'])
        result = acquisition.initial_acquisition_coverage(self.task)
        self.assertTrue(result['ready'])
        self.assertFalse(result['pending_method_inputs'])
        self.assertTrue(all(not item['original_source_ids'] for item in result['method_inputs']))

    def test_financial_methods_need_a_numeric_and_accounting_source_path(self):
        task = json.loads(self.originals[self.paths[2]]); task[POLICY] = 1
        task.update(source_records=[], materials=[], evidence=[], data_need_specs=[])
        result = acquisition.initial_acquisition_coverage(task)
        self.assertFalse(result['ready'])
        roles = {item['role'] for item in result['pending_method_inputs']}
        self.assertTrue({'revenue', 'net_profit', 'operating_cash_flow'}.issubset(roles))

    def test_legacy_unknown_and_boolean_policy_do_not_change_old_readiness(self):
        for version in (None, True, 0, 2):
            task = copy.deepcopy(self.task)
            if version is None: task.pop(POLICY)
            else: task[POLICY] = version
            with self.subTest(version=version):
                result = acquisition.initial_acquisition_coverage(task)
                self.assertTrue(result['ready'])
                self.assertNotIn('method_inputs', result)

    def test_optional_method_does_not_block_a_required_independent_branch(self):
        self.task['questions'][0]['required'] = False
        result = acquisition.initial_acquisition_coverage(self.task)
        self.assertFalse(result['pending_method_inputs'])
        self.assertTrue(result['ready'])

    def test_other_task_sources_do_not_count_as_available_inputs(self):
        for source in self.task['source_records']:
            source['task_id'] = 'another_task'
        result = acquisition.initial_acquisition_coverage(self.task)
        self.assertFalse(result['ready'])
        self.assertTrue(all(not item['api_source_ids'] for item in result['method_inputs']))

    def test_dependency_sources_are_shared_only_when_the_dependency_is_declared(self):
        task = json.loads(self.originals[self.paths[1]]); task[POLICY] = 1
        task['questions'].append(dict(id='q2', question='取得588000.SH的原始资料', required=True,
                                     dependencies=[], method_needs=[], evidence_needs=[]))
        for source in task['source_records']:
            source['question_ids'] = ['q2']
        for need in task['data_need_specs']:
            need['question_ids'] = ['q2']
        self.assertFalse(acquisition.initial_acquisition_coverage(task)['ready'])
        task['questions'][0]['dependencies'] = ['q2']
        self.assertTrue(acquisition.initial_acquisition_coverage(task)['ready'])

    def test_new_policy_is_input_bound_frozen_and_not_a_plan_or_permission_override(self):
        with tempfile.TemporaryDirectory(prefix='method-source-policy-') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            try:
                task = agent.create('解释ETF净值', questions=[dict(id='q1', kind='fact', question='解释ETF净值',
                    answer_scope='general_definition', knowledge_needs=['rh_k_etf_premium_measures'])])
                self.assertEqual(task[POLICY], 1)
                changed = copy.deepcopy(task); changed.pop(POLICY)
                self.assertNotEqual(task['input_hash'], input_fingerprint(changed))
                guide = research_brief(task)['method_source_policy']
                self.assertFalse(guide['financial_approval']); self.assertFalse(guide['automatic_plan_change'])
                done = agent.start(task['id'])
                self.assertEqual(compose(done)[POLICY], 1)
                self.assertNotIn(POLICY, agent.create_case('mechanism'))
            finally:
                agent.close_runtime()


class MethodSourceNavigationTests(unittest.TestCase):
    def test_mixed_financial_original_and_price_paths_keep_both_tools_available(self):
        fixture = navigation.InitialAcquisitionNavigationTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.task[POLICY] = 1
        fixture.task['questions'][0]['method_needs'] = ['net_profit_margin_v1']
        fixture.decisions = [fixture.proposal(operation) for operation in
                             ('report_query', 'filing_original', 'cmd_history_quotation')]
        def coverage(task):
            pending = [] if fixture.stage == 3 else [dict(question_id='q1', role='close_price',
                requires_original_path=False, object='600519.SH', method='observed_price_return_v1')]
            if fixture.stage < 2:
                pending.append(dict(question_id='q1', role='net_profit', requires_original_path=True,
                                    object='600519.SH', method='net_profit_margin_v1'))
            return dict(ready=not pending, financial_approval=False,
                pending_original_question_ids=['q1'] if fixture.stage < 2 else [],
                pending_method_question_ids=['q1'] if pending else [], pending_method_inputs=pending)
        with patch.object(acquisition, 'initial_acquisition_coverage', side_effect=coverage):
            fixture.run_navigation()
        self.assertEqual(fixture.task['reasoning_state']['acquisition']['status'], 'complete')
        self.assertEqual(fixture.model.call_count, 3)
        for call in fixture.model.call_args_list:
            operations = {tool['operation'] for tool in call.args[3]['data_request_contract']['tools']}
            self.assertIn('cmd_history_quotation', operations)

    def test_method_gap_runs_original_navigation_before_extraction(self):
        fixture = navigation.InitialAcquisitionNavigationTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.task[POLICY] = 1
        def coverage(task):
            pending = [] if fixture.stage == 3 else [dict(question_id='q1', role='official_nav',
                requires_original_path=True, object='159901.SZ', method='etf_daily_nav_premium_v1')]
            return dict(ready=not pending, financial_approval=False, pending_original_question_ids=[],
                pending_method_question_ids=['q1'] if pending else [], pending_method_inputs=pending)
        with patch.object(acquisition, 'initial_acquisition_coverage', side_effect=coverage):
            fixture.run_navigation()
        self.assertEqual(fixture.model.call_count, 3)
        self.assertEqual(fixture.task['ledger']['supplements'], 0)
        payload = fixture.model.call_args.args[3]
        allowed = {tool['operation'] for tool in payload['data_request_contract']['tools']}
        self.assertNotIn('cmd_history_quotation', allowed)
        self.assertIn('filing_original', allowed)
        self.assertEqual(fixture.task['reasoning_state']['acquisition']['status'], 'complete')


if __name__ == '__main__':
    unittest.main()
