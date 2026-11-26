"""Definition routing must not abort an independent fact branch.

All cases use isolated stores and block network access. No retained study is
resumed, and a deterministic concept explanation never approves market facts.
"""
import copy
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from lxml import html

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.knowledge import ResearchKnowledge, RETAINED_VERSIONS
from researchhelper.output_schema import planning_output_schema, analysis_output_schema
from researchhelper.reasoning import analysis_contract


class DefinitionBranchRoutingTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='definition-routing-', dir=ROOT / 'result/acceptance')
        self.addCleanup(folder.cleanup)
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline checks only'))
        guard.start(); self.addCleanup(guard.stop)
        self.agent = ResearchAgent(ResearchArchive(Path(folder.name) / 'store'), ROOT,
                                   offline=True, require_argument_support=True)
        self.knowledge = ResearchKnowledge()

    def task(self, knowledge_id='rh_k_price_return_measures', only_definition=False):
        questions = [] if only_definition else [dict(id='q1', kind='fact', question='查询600519.SH收盘价',
            evidence_needs=['同一原件的未复权收盘价'], method_needs=['observed_price_return_v1'])]
        questions.append(dict(id='q2', kind='fact', question='价格变化与含分红总收益有何区别',
            answer_scope='general_definition', knowledge_needs=[knowledge_id]))
        task = self.agent.create('研究600519.SH未复权价格，并解释价格变化与总收益的区别',
            entry='个股', questions=questions)
        self.agent._adopt_method_knowledge(task)
        return task

    def test_new_formal_tasks_version_definition_projection(self):
        task = self.task('rh_k_price_change')
        self.assertEqual(task.get('definition_projection_version'), 1)

    def test_planner_can_select_only_published_applicable_definitions(self):
        task = self.task('rh_k_price_change')
        general = planning_output_schema(task)['properties']['questions']['items']['anyOf'][2]
        choices = general['properties']['knowledge_needs']['items']['enum']
        self.assertIn('rh_k_price_return_measures', choices)
        self.assertNotIn('rh_k_price_change', choices)
        self.assertNotIn('rh_k_cost_pass', choices)
        self.assertNotIn('rh_k_etf_measures', choices)

    def test_wrong_role_remains_unready_without_erasing_independent_fact_scope(self):
        task = self.task('rh_k_price_change')
        before = fingerprint(task)
        contract = analysis_contract(task)['by_question']
        self.assertIn('q1', contract)
        self.assertFalse(contract['q2']['definition']['generation_ready'])
        self.assertFalse(contract['q2']['definition']['fact_approval'])
        self.assertEqual(contract['q2']['definition']['missing_knowledge_ids'], ['rh_k_price_change'])
        self.assertEqual(fingerprint(task), before)
        with self.assertRaises(ContractError):
            self.knowledge.definition_answer(task, task['questions'][1])

    def test_legacy_contract_still_rejects_wrong_role(self):
        task = self.task('rh_k_price_change')
        task.pop('definition_projection_version', None)
        with self.assertRaises(ContractError):
            analysis_contract(task)

    def test_tampered_package_still_aborts_instead_of_becoming_missing(self):
        task = self.task('rh_k_price_change')
        task['knowledge']['entries'][0]['content'] += '保证盈利'
        with self.assertRaises(ContractError):
            analysis_contract(task)

    def test_mixed_model_container_excludes_deterministic_definitions(self):
        task = self.task('rh_k_price_change')
        answers = analysis_output_schema(task)['properties']['answers']
        self.assertEqual(answers['items']['properties']['question_id']['enum'], ['q1'])

    def test_definition_only_container_forbids_model_answers(self):
        task = self.task(only_definition=True)
        self.assertEqual(analysis_output_schema(task)['properties']['answers']['maxItems'], 0)

    def test_valid_concept_is_projected_without_facts_calculation_or_model(self):
        task = self.task(only_definition=True)
        before = fingerprint(RETAINED_VERSIONS[('rh_k_price_change', 1)])
        # Persist the explicitly prepared isolated task, never a real study.
        self.agent.archive.save(task)
        done = self.agent.start(task['id'])
        self.assertEqual(done['status'], '已完成', done.get('error'))
        self.assertEqual(done['questions'][0]['status'], '已回答')
        self.assertIn('不代表当前研究对象事实', done['questions'][0]['answer'])
        self.assertFalse(done['questions'][0]['knowledge_integrity']['fact_approval'])
        self.assertFalse(done['evidence'] or done['calculations'] or done['theses'])
        self.assertEqual(done['ledger']['used']['model_calls'], 0)
        self.assertEqual(done['ledger']['used']['data_requests'], 0)
        self.assertEqual(fingerprint(RETAINED_VERSIONS[('rh_k_price_change', 1)]), before)

    def test_valid_concept_never_accepts_appended_investment_conclusion(self):
        task = self.task(only_definition=True)
        question = task['questions'][0]
        bound = self.knowledge.definition_answer(task, question)
        self.assertIn('不等于', bound['answer'])
        with self.assertRaises(ContractError):
            self.knowledge.definition_answer(task, question,
                dict(bound, answer=bound['answer'] + '因此应当买入'))

    def test_legacy_planning_schema_is_unchanged(self):
        task = self.task('rh_k_price_change')
        task.pop('definition_projection_version', None)
        general = planning_output_schema(task)['properties']['questions']['items']['anyOf'][2]
        self.assertNotIn('enum', general['properties']['knowledge_needs']['items'])


class StandaloneDefinitionTests(unittest.TestCase):
    def setUp(self):
        store = tempfile.TemporaryDirectory()
        self.addCleanup(store.cleanup)
        self.agent = ResearchAgent(ResearchArchive(store.name), ROOT, offline=False,
                                   require_argument_support=True, draft_first=True)
        self.addCleanup(self.agent.close_runtime)
        self.agent.model = Mock()
        self.agent.model.reason.side_effect = AssertionError('A registered concept does not require a model')
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline checks only'))
        guard.start()
        self.addCleanup(guard.stop)

    def test_normal_submission_answers_registered_concepts_and_exports_both_profiles(self):
        from researchhelper.reports import render

        cases = [
            ('解释ETF的单位净值与交易价格为什么会不同。请勿联网。', 'rh_k_etf_measures', '二级市场买卖需求'),
            ('本轮不联网，ETF折溢价为什么可能收敛？', 'rh_k_etf_measures', '套利'),
            ('ETF折价和溢价有什么区别？', 'rh_k_etf_premium_measures', '市场价格高于可比的单位净值称为溢价'),
            ('CPI同比和CPI环比有何区别？', 'rh_k_cpi_reading', '同比比较上年同月，环比比较上月'),
            ('什么是制造业PMI？', 'rh_k_pmi_diffusion', '供应商配送时间'),
            ('未复权股价涨跌和含分红总收益有什么区别？', 'rh_k_price_return_measures', '期间分红等现金分配'),
        ]
        for question, ident, answer in cases:
            with self.subTest(ident=ident):
                created = self.agent.create(question, research_mode='current')
                done = self.agent.start(created['id'])
                self.assertEqual(done['question'], question)
                self.assertEqual(done['status'], '已完成', done.get('error'))
                self.assertEqual(done['run_mode'], 'offline')
                self.assertEqual(done['questions'][0]['knowledge_needs'], [ident])
                self.assertFalse(done['questions'][0]['knowledge_integrity']['fact_approval'])
                self.assertFalse(done['source_records'] or done['evidence'] or done['calculations'] or done['theses'])
                self.assertEqual(done['ledger']['used']['model_calls'], 0)
                self.assertEqual(done['ledger']['used']['data_requests'], 0)
                saved = self.agent.archive.report(done['id'], 1)
                for profile in ('standard', 'detailed'):
                    page = html.document_fromstring(render(saved['content'], ROOT, report_profile=profile))
                    main = page.find('.//main')
                    self.assertEqual(main.find('.//h1').text_content(), done['delivery']['title'])
                    self.assertIn(answer, main.text_content())
                    self.assertIn('查看条目与原始出处', main.text_content())
                    self.assertNotIn('尚无已核验来源', main.text_content())
                    self.assertNotIn('策略逻辑', main.text_content())
                    self.assertEqual(page.xpath('//*[@data-knowledge]/@data-knowledge'), [ident])
                self.assertEqual(self.agent.archive.report(done['id'], 1), saved)
        self.agent.model.reason.assert_not_called()

    def test_compound_or_unsupported_questions_preserve_the_research_route(self):
        for question in (
            '解释为什么510300.SH今天折价。请勿联网。',
            '解释510300.SH的ETF折溢价为何可能收敛。请勿联网。',
            'ETF折溢价可能收敛。请勿联网。',
            '解释最近一个月CPI同比为何回升。请勿联网。',
            '请计算ETF当日折溢价。请勿联网。',
            '解释久期与凸性有什么区别。请勿联网。',
            '不要解释ETF的单位净值。请勿联网。',
        ):
            with self.subTest(question=question):
                task = self.agent.create(question, research_mode='current')
                result = self.agent.start(task['id'])
                self.assertEqual(result['status'], '部分完成', result.get('error'))
                self.assertNotIn('answer_scope', result['questions'][0])
                self.assertTrue(result['draft_report'])
                self.assertEqual(result['question'], question)

    def test_draft_reads_dated_fact_and_definition_as_separate_branches(self):
        from researchhelper.draft_sources import stated_window_requests
        from researchhelper.draft_workflow import direct_readout
        from researchhelper.market_series import NAV_INDICATOR
        from researchhelper.report_designer import render_html

        question = '先解释ETF的单位净值和交易价格；再核对510300.SH在2026年10月21日的单位净值。'
        online = self.agent.create(question, research_mode='current')
        self.assertEqual(direct_readout(online), ('market_day', None))
        lookup, = stated_window_requests(online)
        self.assertEqual(lookup['question_ids'], ['q2'])
        self.assertEqual(lookup['operation'], 'date_sequence')
        self.assertTrue(self.agent.can_run_without_model(online))

        for available in (True, False):
            with self.subTest(available=available):
                task = self.agent.create(question+'本轮不联网。', research_mode='current')
                if available:
                    params = lookup['parameters']
                    self.agent.add_snapshot(task['id'], 'ifind', 'date_sequence', params,
                        dict(errorcode=0,tables=[dict(thscode='510300.SH',time=['2026-10-21'],
                                                    table={NAV_INDICATOR:[4.1856]})]))
                done = self.agent.start(task['id'])
                definition, fact = done['questions']
                self.assertEqual(definition['status'], '已回答')
                self.assertEqual(definition['knowledge_refs'][0]['id'], 'rh_k_etf_measures')
                self.assertFalse(definition['refs'] or definition['calculation_refs'])
                self.assertFalse(definition['knowledge_integrity']['fact_approval'])
                self.assertNotIn('answer_scope', fact)
                self.assertEqual(done['status'], '部分完成')
                self.assertEqual(done['ledger']['used']['model_calls'], 0)
                self.assertEqual(done['ledger']['used']['data_requests'], 0)
                contents = self.agent.archive.report(task['id'],1)['content']
                for profile in ('standard', 'detailed'):
                    page = html.document_fromstring(render_html(contents, ROOT, profile=profile))
                    self.assertIn('二级市场买卖需求', page.get_element_by_id('indicator-definitions').text_content())
                    summary = page.get_element_by_id('summary').text_content()
                    self.assertEqual('4.1856' in summary, available)
                    if not available:
                        self.assertIn('未取得510300.SH在2026-10-21的单位净值', summary)

    def test_clause_binding_preserves_negation_and_attachment_only_instructions(self):
        from researchhelper.draft_workflow import direct_readout

        questions = {
            '解释ETF的单位净值和交易价格；不再核对510300.SH在2026年10月21日的单位净值。': True,
            '只用附件，解释ETF的单位净值和交易价格；再核对510300.SH的净值。': False,
            '解释为什么510300.SH的单位净值和交易价格不同，以及为何最近下跌。': False,
        }
        for text, expected_definition in questions.items():
            with self.subTest(text=text):
                task = self.agent.create(text)
                self.assertEqual(any(q.get('answer_scope')=='general_definition' for q in task['questions']),
                                 expected_definition)
                self.assertIsNone(direct_readout(task))
                if expected_definition:
                    self.assertIn('不再核对', task['questions'][-1]['question'])

    def test_declared_scope_and_attached_matter_override_the_standalone_route(self):
        question = '解释ETF的单位净值。请勿联网。'
        cases = [
            {'objects': ['510300.SH']},
            {'cutoff': '2026-07-20T18:00:00+08:00', 'research_mode': 'historical'},
            {'intent': '验证观点'},
            {'questions': [{'question': question, 'kind': 'fact', 'evidence_needs': ['官方净值']}]},
        ]
        for options in cases:
            with self.subTest(options=options):
                created = self.agent.create(question, **options)
                result = self.agent.start(created['id'])
                self.assertEqual(result['status'], '部分完成', result.get('error'))
                self.assertNotIn('answer_scope', result['questions'][0])
        created = self.agent.create(question)
        self.agent.add_material(created['id'], '说明.txt', '本文件列示基金单位净值概念。'.encode())
        result = self.agent.start(created['id'])
        self.assertNotIn('answer_scope', result['questions'][0])
        self.assertTrue(result['materials'])

    def test_unconnected_skill_resumes_a_first_question_through_the_same_path(self):
        from core.session import Session

        with tempfile.TemporaryDirectory() as project:
            session = Session(project)
            try:
                task = session.create('解释ETF单位净值和市场价格。')
                done = session.resume(task['id'])
                self.assertEqual(done['status'], '已完成', done.get('error'))
                self.assertEqual(done['ledger']['used']['model_calls'], 0)
                self.assertEqual(done['ledger']['used']['data_requests'], 0)
                report = html.parse(str(session.export(done['id'], profile='standard')))
                self.assertIn('二级市场买卖需求', report.find('.//main').text_content())
            finally:
                session.agent.close_runtime()


if __name__ == '__main__':
    unittest.main()
