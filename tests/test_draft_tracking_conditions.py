"""Saved draft conditions are reusable research premises, never approved outcomes."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from tests import ROOT
from tests import test_draft_first_workflow as financial
from core.session import Session
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.drafts import draft_conditions, normalize_report
from researchhelper.draft_workflow import _writing_messages
from researchhelper.planning import input_fingerprint
from researchhelper.publication_content import client_report_brief
from researchhelper.reports import build_version_changes, render, validate_version_changes

RULE = dict(indicator='经营现金流与利润的变化方向', source='公司后续定期报告',
            window='下一次定期报告发布后', condition='若现金回收持续弱于盈利，复核盈利兑现判断。',
            benchmark='相同报表口径的上年同期')
PREMISE = '现金回收节奏未发生显著变化时，本条经营解释才具备可比性。'


class DraftTrackingConditionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        financial.DraftFirstWorkflowTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        financial.DraftFirstWorkflowTests.tearDownClass()

    def setUp(self):
        self.control = financial.DraftFirstWorkflowTests()
        self.control.setUp()
        self.addCleanup(self.control.doCleanups)
        self.addCleanup(self.control.agent.close_runtime)
        self.agent = self.control.agent
        self.rule = deepcopy(RULE)
        self.messages = []
        self.planning = []
        reason = self.control.model.reason

        def with_conditions(task, messages, **options):
            value = json.loads(reason(task, messages, **options))
            if options['phase'] == 'draft_report':
                self.messages.append(deepcopy(messages))
                value['logics'][0]['argument'] = dict(condition=PREMISE)
                value['logics'][0]['tracking'] = deepcopy(self.rule)
            elif options['phase'] == 'draft_plan':
                self.planning.append(json.loads(messages[-1]['content']))
            return json.dumps(value, ensure_ascii=False)

        self.control.model.reason = with_conditions

    def initial_task(self):
        return self.agent.create(financial.QUESTION, research_mode='current')

    def test_complete_rule_is_trimmed_and_bound_to_the_same_report_logic(self):
        task = self.initial_task()
        proposal = deepcopy(financial.REPORT)
        proposal['logics'][0].update(argument=dict(condition=PREMISE),
            tracking={key:' '+value+' ' for key,value in RULE.items()})
        original = deepcopy(proposal)
        draft = normalize_report(proposal, task, [])
        self.assertEqual(draft['tracking_version'], 1)
        self.assertEqual(draft['logics'][0]['tracking'], RULE)
        self.assertEqual(proposal, original)
        self.assertEqual(task['tracking'], [])
        self.assertEqual(draft_conditions(dict(draft=draft)), [dict(logic_index=1,
            title=financial.REPORT['logics'][0]['title'], condition=PREMISE, tracking=RULE)])

    def test_incomplete_or_forged_status_is_advisory_and_never_prevents_a_report(self):
        task = self.initial_task()
        for rule in [dict(RULE, status='已兑现'), dict(RULE, task_id='foreign'),
                     dict(RULE, indicator=123), {key:value for key,value in RULE.items() if key!='window'}]:
            proposal = deepcopy(financial.REPORT)
            proposal['logics'][0]['tracking'] = rule
            draft = normalize_report(proposal, task, [])
            with self.subTest(rule=rule):
                self.assertEqual(draft['conclusion'], financial.REPORT['conclusion'])
                self.assertNotIn('tracking', draft['logics'][0])
                self.assertTrue(draft['logics'][0]['workpaper_notes'])
                self.assertEqual(task['ledger']['used']['model_calls'], 0)
                self.assertEqual(task['ledger']['used']['data_requests'], 0)

    def test_optional_or_unknown_policy_does_not_invent_observation_rules(self):
        for version in (None, True, 2, '1'):
            task = self.initial_task()
            if version is None:
                task.pop('draft_tracking_version')
            else:
                task['draft_tracking_version'] = version
            proposal = deepcopy(financial.REPORT)
            proposal['logics'][0].update(argument=dict(condition=PREMISE), tracking=RULE)
            draft = normalize_report(proposal, task, [])
            with self.subTest(version=version):
                self.assertNotIn('tracking_version', draft)
                self.assertNotIn('tracking', draft['logics'][0])
                self.assertEqual(draft_conditions(dict(draft=draft))[0]['condition'], PREMISE)
                self.assertIsNone(draft_conditions(dict(draft=draft))[0]['tracking'])
                self.assertNotIn('tracking对象', client_report_brief(task))

    def test_policy_participates_in_input_and_does_not_change_old_prompts(self):
        task = self.initial_task()
        self.assertEqual(task['draft_tracking_version'], 1)
        original = task['input_hash']
        task.pop('draft_tracking_version')
        self.assertNotEqual(input_fingerprint(task), original)
        task['input_hash'] = input_fingerprint(task)
        old = _writing_messages(task, [])
        self.assertNotIn('tracking_contract', json.loads(old[-1]['content']))
        self.assertNotIn('tracking对象', old[0]['content'])
        self.assertNotIn('tracking', json.loads(old[-1]['content'])['output_example']['logics'][0])

    def test_app_and_skill_expose_the_same_optional_rule_without_a_model_call(self):
        with tempfile.TemporaryDirectory(prefix='rh-tracking-skill-') as directory:
            session = Session(directory)
            self.addCleanup(session.agent.close_runtime)
            task = session.agent.create(financial.QUESTION, research_mode='current')
            first = session.tools(task['id'], scope='first_report')['first_report']
            app = json.loads(_writing_messages(task, [])[-1]['content'])
            self.assertEqual(first['tracking_contract'], app['tracking_contract'])
            self.assertIn('tracking', first['optional_logic_fields'])
            self.assertEqual(first['report_example'], app['output_example'])
            self.assertEqual(first['tracking_contract']['automatic_monitoring'], False)
            self.assertEqual(first['tracking_contract']['condition_is_observed_result'], False)
            self.assertEqual(session.agent.archive.get(task['id'])['ledger']['used']['model_calls'], 0)

    def test_only_detailed_report_displays_tracking_without_approving_a_thesis(self):
        task, saved = self.control.run_task()
        self.assertEqual(self.control.model.calls, ['draft_plan', 'draft_report'])
        self.assertEqual(len(task['draft_report']['facts']), 8)
        self.assertEqual(task['theses'], [])
        self.assertEqual(task['tracking'], [])
        self.assertEqual(saved['content']['tracking'], [])
        self.assertEqual(saved['content']['template_version'], 'rh-draft-9')
        self.assertEqual(saved['content']['draft']['logics'][0]['tracking'], RULE)
        for profile in ('standard', 'detailed'):
            document = render(saved['content'], ROOT, report_profile=profile)
            with self.subTest(profile=profile):
                for text in ('class="draft-tracking-rule"', RULE['condition'], RULE['source']):
                    self.assertEqual(text in document, profile == 'detailed')
                self.assertIn('echarts.init', document)
                self.assertNotIn('监控已开启', document)
        self.assertEqual(self.agent.archive.report(task['id'], 1), saved)
        payload = json.loads(self.messages[0][-1]['content'])
        self.assertEqual(payload['tracking_contract']['required_fields'], ['indicator', 'source', 'window', 'condition'])

    def test_missing_period_still_saves_rules_and_available_figures(self):
        self.control.failed_financial_period = '20250630'
        task, saved = self.control.run_task()
        self.assertEqual(len(task['draft_report']['facts']), 4)
        self.assertEqual(task['draft_report']['logics'][0]['tracking'], RULE)
        self.assertEqual(self.control.model.calls, ['draft_plan', 'draft_report'])
        self.assertNotIn(RULE['condition'], saved['html'])
        self.assertIn(RULE['condition'], render(saved['content'], ROOT, report_profile='detailed'))
        self.assertTrue(task['delivery'])

    def test_new_version_preserves_old_conditions_and_compares_without_matching_by_position(self):
        first, saved = self.control.run_task()
        self.rule = dict(RULE, window='下一次完整年度报告发布后', condition='若全年现金兑现未改善，重新评估经营解释。')
        done = self.agent.update(first['id'])
        self.assertTrue(done.get('delivery'), done.get('error'))
        delta = done['changes']['draft_conditions']
        self.assertEqual(delta['version'], 1)
        self.assertEqual(delta['prior'][0]['tracking'], RULE)
        self.assertEqual(delta['current'][0]['tracking'], self.rule)
        self.assertNotIn('status', delta['prior'][0])
        self.assertEqual(done['update_from']['draft_conditions'], delta['prior'])
        writing = json.loads(self.messages[-1][-1]['content'])
        self.assertEqual(writing['active_update']['original_report_conditions'], delta['prior'])
        planning = self.planning[-1]['active_update']
        self.assertEqual(planning['tracking_inputs'], [dict(logic_index=1,
            indicator=RULE['indicator'], source=RULE['source'], window=RULE['window'], benchmark=RULE['benchmark'])])
        self.assertNotIn(RULE['condition'], json.dumps(planning, ensure_ascii=False))
        self.assertEqual(self.agent.archive.report(first['id'], 1), saved)
        validate_version_changes(done, saved)
        self.assertEqual(done['ledger']['used']['model_calls'], 2)
        self.assertEqual(done['tracking'], [])

    def test_changed_prior_condition_cannot_rewrite_frozen_history(self):
        first, saved = self.control.run_task()
        done = self.agent.update(first['id'])
        altered = deepcopy(done)
        altered['update_from']['draft_conditions'][0]['tracking']['condition'] = '事后改写旧条件'
        with self.assertRaisesRegex(ContractError, '原报告条件'):
            build_version_changes(altered, saved)
        altered = deepcopy(done)
        altered['changes']['draft_conditions']['prior'][0]['condition'] = '改写对照'
        with self.assertRaises(ContractError):
            validate_version_changes(altered, saved)
        self.assertEqual(self.agent.archive.report(first['id'], 1), saved)

    def test_malformed_saved_rule_is_not_rendered_as_a_verified_result(self):
        task, saved = self.control.run_task()
        content = deepcopy(saved['content'])
        content['draft']['logics'][0]['tracking'] = dict(RULE, status='已兑现')
        content.pop('content_hash')
        content['content_hash'] = fingerprint(content)
        self.assertIsNone(draft_conditions(content)[0]['tracking'])
        document = render(content, ROOT)
        self.assertNotIn(RULE['condition'], document.split('研究依据')[0])
        self.assertNotIn('已兑现', financial.html_main_text(document))
        self.assertEqual(task['tracking'], [])


if __name__ == '__main__':
    unittest.main()
