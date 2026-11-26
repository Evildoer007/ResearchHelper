"""Structured writing is shared, optional and usable by real report delivery.

Synthetic inputs, network denied. These checks exercise the host-to-SQLite
path as well as the model prompt and client/internal renderers.
"""
from copy import deepcopy
import json
import socket
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from lxml import html
from tests import ROOT
from tests.test_client_report_audience import fixture
from core.session import Session
from researchhelper.contracts import canonical
from researchhelper.draft_workflow import _write
from researchhelper.drafts import normalize_report, compose, source_binding
from researchhelper.publication_content import report_writing_example
from researchhelper.draft_visuals import logic_figure_plan
from researchhelper.report_designer import design


def argument_report():
    return dict(title='经营兑现观察', conclusion='修复仍取决于盈利兑现。', logics=[dict(
        title='盈利修复需要收入与费用配合',
        argument=dict(claim='利润恢复需要经营改善。', evidence='收入与利润降幅不同。',
                      reasoning='收入变化传导至盈利仍受费用影响。', condition='需求回稳且费用不抵消。'),
        implication='分开观察收入与利润兑现。', risk='需求转弱可能抵消费用改善。',
        data_refs=['D1', 'D2', 'D4', 'D5'], calculation_refs=['A1', 'A2'],
        mechanism=dict(steps=[dict(label='收入与利润分化', kind='observation', data_refs=['D1', 'D2']),
                              dict(label='需求与费用配合', kind='condition', data_refs=[]),
                              dict(label='盈利可能修复', kind='hypothesis', data_refs=[])]))],
        risks=['经营修复节奏存在不确定性。'])


class DraftArgumentStructureTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No live I/O'))
        guard.start(); self.addCleanup(guard.stop)
        self.task, self.content = fixture()
        self.facts = self.task['draft_report']['facts']

    def normalize(self, report):
        return normalize_report(report, self.task, self.facts)

    def test_structured_judgment_is_not_discarded_when_legacy_analysis_is_absent(self):
        report = argument_report(); before = canonical(report)
        result = self.normalize(report)['logics'][0]
        self.assertEqual(result['analysis'].splitlines(), list(report['logics'][0]['argument'].values()))
        self.assertEqual(result['argument'], report['logics'][0]['argument'])
        self.assertEqual(result['status'], '待验证')
        self.assertEqual(canonical(report), before)

    def test_legacy_and_structured_material_both_survive_mixed_submission(self):
        report = argument_report(); logic = report['logics'][0]
        logic['analysis'] = '保留宿主另写的竞争解释。'
        logic['argument']['condition'] = logic['argument']['claim']
        result = self.normalize(report)['logics'][0]['analysis']
        self.assertTrue(result.startswith(logic['analysis']))
        self.assertEqual(result.count(logic['argument']['claim']), 1)
        self.assertIn(logic['argument']['reasoning'], result)

    def test_malformed_optional_fields_do_not_discard_usable_prose(self):
        for argument in (None, [], 'invalid', dict(claim='有限资料可保留假设。', evidence=9, reasoning={})):
            report = argument_report(); logic = report['logics'][0]
            logic.update(argument=argument, analysis='宿主原始正文。')
            with self.subTest(argument=argument):
                result = self.normalize(report)['logics'][0]
                self.assertTrue(result['analysis'].startswith('宿主原始正文。'))
                self.assertNotIn('invalid', result['analysis'])

    def test_empty_argument_uses_existing_fallback_without_inventing_a_path(self):
        result = self.normalize(dict(logics=[dict(argument={})]))
        self.assertTrue(result['logics'])
        self.assertFalse(any(row.get('mechanism') for row in result['logics']))

    def test_unreferenced_path_observation_is_presented_as_a_hypothesis(self):
        report = argument_report(); report['logics'][0]['mechanism']['steps'][0]['data_refs'] = ['foreign']
        logic = self.normalize(report)['logics'][0]
        self.assertEqual(logic['mechanism']['steps'][0]['kind'], 'hypothesis')
        self.assertTrue(logic['reference_notes'])
        self.assertNotIn('foreign', canonical(logic))
        self.assertEqual(logic['analysis'].splitlines(), list(logic['argument'].values()))

    def test_structured_prose_is_not_used_to_invent_a_causal_graph(self):
        report = argument_report(); report['logics'][0].pop('mechanism')
        normalized = self.normalize(report)
        self.assertNotIn('mechanism', normalized['logics'][0])
        task = deepcopy(self.task)
        task['draft_report'] = dict(normalized, facts=self.facts,
            calculations=self.task['draft_report']['calculations'], source_binding=source_binding(task))
        self.assertFalse(any(spec.get('coverage_kind') == 'mechanism' for spec in logic_figure_plan(compose(task))))

    def test_client_and_internal_reports_include_the_same_judgment_and_explicit_path(self):
        report = argument_report(); task = deepcopy(self.task)
        normalized = self.normalize(report)
        task['draft_report'] = dict(normalized, facts=self.facts,
            calculations=self.task['draft_report']['calculations'], source_binding=source_binding(task))
        content = compose(task); before = canonical(content)
        figures = logic_figure_plan(content)
        self.assertTrue(any(spec.get('coverage_kind') == 'mechanism' for spec in figures))
        self.assertFalse(any(spec.get('coverage_kind') == 'numeric' for spec in figures))
        for profile in ('standard', 'detailed'):
            rendered = design(content, ROOT, profile=profile)
            tree = html.document_fromstring(rendered['html'])
            if profile == 'standard':
                reading = tree.get_element_by_id('logic-1')
                self.assertEqual(rendered['document']['figure_coverage']['logic-1']['status'], 'numeric_text')
                self.assertEqual(set(reading.xpath('.//*[@data-calculation-ref]/@data-calculation-ref')), {'A1', 'A2'})
                self.assertFalse(reading.xpath('.//table'))
            main = tree.xpath('//main')[0].text_content()
            for sentence in report['logics'][0]['argument'].values():
                self.assertIn(sentence, main)
            self.assertNotIn('<details', html.tostring(tree, encoding='unicode'))
        self.assertEqual(canonical(content), before)

    def test_path_inspector_keeps_the_parent_registered_calculation_and_its_inputs(self):
        from researchhelper.drafts import logic_basis
        report = argument_report(); task = deepcopy(self.task)
        report['logics'][0]['mechanism']['steps'][0]['data_refs'] = ['D1']
        normalized = self.normalize(report)
        task['draft_report'] = dict(normalized, facts=self.facts,
            calculations=self.task['draft_report']['calculations'], source_binding=source_binding(task))
        content = compose(task); before = canonical(content)
        graph = next(spec for spec in logic_figure_plan(content) if spec.get('coverage_kind') == 'mechanism')
        self.assertEqual(graph['calculation_refs'], ['A1', 'A2'])
        self.assertEqual(set(graph['data_refs']), {'D1', 'D2', 'D4', 'D5'})
        rows, _ = logic_basis(content, graph)
        self.assertEqual({row['id'] for row in rows}, {'D1', 'D2', 'D4', 'D5'})
        self.assertEqual(canonical(content), before)

    def test_path_does_not_adopt_an_unknown_calculation(self):
        from researchhelper.draft_visuals import _mechanism_figures
        logic = argument_report()['logics'][0]
        logic['calculation_refs'] = ['foreign']
        facts = {row['id']: row for row in self.facts}
        graph = _mechanism_figures(logic, 1, facts, {})[0]
        self.assertEqual(graph['calculation_refs'], [])
        self.assertEqual(graph['data_refs'], ['D1', 'D2'])

    def macro_path_content(self):
        from researchhelper.draft_analysis import analyze
        task = deepcopy(self.task)
        task['source_records'] = [dict(id='macro', task_id=task['id'], research_version=1,
            source='ifind', operation='edb_service')]
        facts = [dict(id='D'+str(i), artifact_id='macro', code='macro-cpi', field='value', label='CPI同比',
            period=period, value=value, raw_value=str(value), unit='%', series_basis='cpi-yoy')
            for i, (period, value) in enumerate([('2026-01-31', .2), ('2026-08-31', .8), ('2026-09-30', .9)], 1)]
        task['draft_analysis'] = analyze(task, facts)
        report = argument_report(); logic = report['logics'][0]
        logic['data_refs'] = ['D3']; logic['calculation_refs'] = ['A2']
        logic['mechanism']['steps'][0]['data_refs'] = ['D3']
        draft = normalize_report(report, task, facts)
        task['draft_report'] = dict(draft, facts=facts, calculations=task['draft_analysis']['results'],
            source_binding=source_binding(task))
        return compose(task)

    def test_percentage_point_path_keeps_the_registered_difference_and_both_months(self):
        content = self.macro_path_content(); before = canonical(content)
        graph = next(spec for spec in logic_figure_plan(content) if spec.get('coverage_kind') == 'mechanism')
        self.assertEqual(graph['calculation_refs'], ['A2'])
        self.assertEqual(set(graph['data_refs']), {'D2', 'D3'})
        self.assertEqual(canonical(content), before)

    def test_changed_macro_result_never_becomes_a_path_dependency(self):
        content = self.macro_path_content()
        content['draft']['calculations'][1].update(value=9, display='9.00')
        graph = next(spec for spec in logic_figure_plan(content) if spec.get('coverage_kind') == 'mechanism')
        self.assertEqual(graph['calculation_refs'], [])
        self.assertEqual(graph['data_refs'], ['D3'])

    def test_app_and_skill_receive_one_shared_example_for_each_problem_kind(self):
        with TemporaryDirectory(prefix='rh-argument-contract-') as folder:
            session = Session(folder)
            self.addCleanup(session.agent.close_runtime)
            for kind in ('fact', 'comparison', 'mechanism', 'investment'):
                task = session.create('研究已知对象', objects=['600519.SH'],
                    cutoff='2026-10-30T18:00:00+08:00',
                    questions=[dict(id='q1', question='研究已知对象', kind=kind, required=True)])
                captured = []
                class Model:
                    def reason(self, task, messages, **options):
                        captured.append((messages, options)); return '{}'
                class Engine:
                    model = Model()
                _write(Engine(), task, [])
                first = session.tools(task['id'])['first_report']
                example = json.loads(captured[0][0][1]['content'])['output_example']
                self.assertEqual(example, first['report_example'])
                self.assertEqual(example, report_writing_example(task))
                self.assertEqual(len(captured), 1)
                self.assertEqual(captured[0][1]['phase'], 'draft_report')
                logic = example['logics'][0]
                if kind in ('fact', 'comparison'):
                    self.assertIn('analysis', logic)
                    self.assertNotIn('argument', logic)
                    self.assertNotIn('mechanism', logic)
                else:
                    self.assertEqual(set(logic['argument']), {'claim', 'evidence', 'reasoning', 'condition'})
                    self.assertTrue(logic['mechanism']['steps'])

    def test_host_structured_submission_saves_then_reopens_without_model_or_data_calls(self):
        with TemporaryDirectory(prefix='rh-argument-host-') as folder:
            session = Session(folder)
            self.addCleanup(session.agent.close_runtime)
            task = session.create('解释经营改善如何传导至利润', objects=['600519.SH'],
                cutoff='2026-10-30T18:00:00+08:00')
            report = argument_report()
            report['logics'][0]['data_refs'] = []
            report['logics'][0]['calculation_refs'] = []
            report['logics'][0].pop('mechanism')
            done = session.draft(task['id'], report=report)
            self.assertTrue(done.get('delivery'), done.get('error'))
            reopened = session.status(task['id'])
            self.assertEqual(reopened['draft_report']['logics'][0]['argument'], report['logics'][0]['argument'])
            self.assertEqual(reopened['ledger']['used']['model_calls'], 0)
            self.assertEqual(reopened['ledger']['used']['data_requests'], 0)
            content = session.agent.archive.report(task['id'], 1)['content']
            self.assertEqual(content['draft']['logics'][0]['analysis'].splitlines(), list(report['logics'][0]['argument'].values()))


if __name__ == '__main__':
    unittest.main()
