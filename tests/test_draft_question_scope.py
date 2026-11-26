"""Scope reaches both reasoning phases and the Skill without extra calls."""
from copy import deepcopy
import json
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lxml import html
from tests import ROOT
from core.session import Session
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import charge, ENTRIES
from researchhelper.draft_workflow import _plan, _write
from researchhelper.drafts import normalize_report
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import snapshot_record
from researchhelper.planning import draft_research_scope, input_fingerprint
from researchhelper.publication_content import CLIENT_REPORT_BRIEF, client_report_brief

POLICY = 'draft_question_scope_version'
QUESTION = '查询600519.SH已取得的收盘观测'


class CapturingModel:
    def __init__(self, structured=False, charge_calls=False):
        self.structured = structured
        self.charge_calls = charge_calls
        self.calls = []

    def supports_structured_analysis(self):
        return self.structured

    def reason_structured_acquisition(self, task, messages, description):
        self.calls.append(('draft_plan', messages))
        return json.dumps(dict(data_requests=[]))

    def reason(self, task, messages, **options):
        phase = options['phase']
        self.calls.append((phase, messages))
        if self.charge_calls:
            charge(task['ledger'], 'model_calls', 1, delivery=options.get('delivery', False))
        if phase == 'draft_plan':
            return json.dumps(dict(data_requests=[]))
        return json.dumps(dict(title='数据观察', report_scope='fact', conclusion='按本次观察范围核对。',
            logics=[dict(title='已取得的数据', analysis='逐项核对日期与口径。', data_refs=['D1', 'D2'])]))


class DraftQuestionScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / 'result/acceptance')
        self.addCleanup(self.tmp.cleanup)
        self.archive = ResearchArchive(self.tmp.name)
        self.agent = ResearchAgent(self.archive, ROOT, offline=True, draft_first=True, require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)

    def task(self, kind=None, intent='综合研究'):
        questions = None if kind is None else [dict(id='q1', question=QUESTION, kind=kind, required=True)]
        return self.agent.create(QUESTION, entry='个股', intent=intent, questions=questions,
            objects=['600519.SH'], cutoff='2026-10-30T18:00:00+08:00')

    def test_planner_and_writer_both_receive_same_explicit_scope(self):
        for structured in (False, True):
            for kind in ('fact', 'comparison', 'mechanism', 'investment'):
                with self.subTest(structured=structured, kind=kind):
                    task = self.task(kind)
                    before = deepcopy(task)
                    model = CapturingModel(structured)
                    engine = SimpleNamespace(model=model)
                    _plan(engine, task, 1)
                    _write(engine, task, [])
                    payloads = [json.loads(messages[1]['content']) for phase, messages in model.calls]
                    self.assertEqual(payloads[0]['research_scope'], payloads[1]['research_scope'])
                    scope = payloads[1]['research_scope']
                    self.assertEqual(scope['questions'][0]['kind'], kind)
                    self.assertFalse(scope['changes_question_obligations'])
                    self.assertEqual([phase for phase, messages in model.calls], ['draft_plan', 'draft_report'])
                    self.assertEqual(task, before)

    def test_default_question_type_is_identified_as_an_initial_default(self):
        task = self.task()
        model = CapturingModel()
        _write(SimpleNamespace(model=model), task, [])
        scope = json.loads(model.calls[0][1][1]['content'])['research_scope']
        self.assertEqual(scope['mode'], 'by_question')
        self.assertEqual(scope['allowed_report_scopes'], ['fact', 'comparison', 'mechanism', 'research'])
        self.assertEqual(scope['questions'][0]['question_ref'], 'main')
        self.assertNotIn('question', scope['questions'][0])

    def test_model_fact_scope_can_organize_an_unclassified_initial_question(self):
        task = self.task()
        before = deepcopy(task)
        value = dict(title='指定正文', report_scope='fact',
            logics=[dict(title='观察读数', analysis='按实际资料核对。')])
        report = normalize_report(value, task, [])
        self.assertEqual(report.get('organization'), 'fact')
        self.assertEqual(report['logics'][0]['analysis'], value['logics'][0]['analysis'])
        self.assertEqual(task, before)

    def test_explicit_investment_obligation_is_not_downgraded_by_a_presentation_field(self):
        task = self.task('investment')
        report = normalize_report(dict(report_scope='fact',
            logics=[dict(title='原样保留', analysis='有限资料只能支持条件性说明。')]), task, [])
        self.assertNotIn(report.get('organization'), ('fact', 'comparison'))
        self.assertEqual(task['questions'][0]['kind'], 'investment')

    def test_explicit_direct_scope_applies_to_submitted_prose_too(self):
        report = normalize_report(dict(logics=[dict(title='指定核对', analysis='保留这段正文。')]), self.task('fact'), [])
        self.assertEqual(report.get('organization'), 'fact')
        self.assertEqual(report['logics'][0]['analysis'], '保留这段正文。')

    def test_research_without_evidence_keeps_scope_and_reports_its_limit(self):
        for kind in (None, 'investment'):
            with self.subTest(kind=kind):
                report = normalize_report(dict(report_scope='research'), self.task(kind), [])
                self.assertEqual(report.get('organization'), 'research')
                self.assertEqual(report['logics'], [])
                self.assertEqual(report['conclusion'], '本次未取得足以形成投资判断的资料。')

    def test_authored_investment_hypothesis_is_kept_without_generic_sections(self):
        task = self.task('investment')
        statement = '若后续现金回款恢复，才重新评估盈利变化是否具有持续性。'
        report = normalize_report(dict(report_scope='research',
            logics=[dict(title='回款验证', analysis=statement)]), task, [])
        self.assertEqual([row['analysis'] for row in report['logics']], [statement])
        self.assertEqual(report['logics'][0]['display_heading'], '策略逻辑一：回款验证')

    def test_invalid_optional_scope_neither_blocks_nor_rewrites_prose(self):
        for selected in (None, 'unknown', [], {'fact': True}):
            with self.subTest(selected=selected):
                report = normalize_report(dict(report_scope=selected,
                    logics=[dict(title='保留', analysis='保留提供的正文。')]), self.task(), [])
                self.assertNotIn('organization', report)
                self.assertEqual(report['logics'][0]['analysis'], '保留提供的正文。')

    def test_optional_comparison_does_not_replace_required_investment(self):
        task = self.agent.create(QUESTION, questions=[dict(id='q1', question=QUESTION, kind='investment', required=True),
            dict(id='q2', question='可选比较', kind='comparison', required=False)])
        scope = draft_research_scope(task)
        self.assertEqual(scope['mode'], 'research')
        self.assertEqual(scope['allowed_report_scopes'], ['research'])
        self.assertFalse(scope['questions'][1]['required'])

    def test_question_needs_are_included_without_mutating_the_plan(self):
        task = self.task('comparison')
        task['questions'][0].update(evidence_needs=['已登记观测'], method_needs=['price_return'])
        before = deepcopy(task)
        scope = draft_research_scope(task)
        self.assertEqual(scope['questions'][0]['evidence_needs'], ['已登记观测'])
        self.assertEqual(scope['questions'][0]['method_needs'], ['price_return'])
        scope['questions'][0]['method_needs'].append('another-method')
        self.assertEqual(task, before)

    def test_skill_receives_same_scoped_brief_and_optional_scope_field(self):
        # A real Skill project is outside its read-only installation. This
        # temporary independent project is always removed by the context.
        with tempfile.TemporaryDirectory(prefix='rh-skill-question-scope-') as project:
            session = Session(project)
            try:
                task = session.create(QUESTION, questions=[dict(id='q1', question=QUESTION, kind='fact', required=True)])
                before = deepcopy(task)
                description = session.tools(task['id'])
                first = description['first_report']
                self.assertIn('report_scope', first['optional_report_fields'])
                self.assertEqual(first['writing_brief'], client_report_brief(task))
                self.assertEqual(first['research_scope']['mode'], 'fact')
                self.assertEqual(session.status(task['id']), before)
            finally:
                session.agent.close_runtime()

    def test_frozen_legacy_task_does_not_silently_adopt_new_presentation(self):
        task = self.task()
        task.pop(POLICY)
        # A pre-scope task also predates the independently versioned tracking
        # contract. Keep both absent rather than reuse a current-task fixture.
        task.pop('draft_tracking_version')
        before = deepcopy(task)
        report = normalize_report(dict(report_scope='fact',
            logics=[dict(title='原样保留', analysis='保留旧版组织。')]), task, [])
        self.assertNotIn('organization', report)
        self.assertEqual(client_report_brief(task), CLIENT_REPORT_BRIEF)
        self.assertIsNone(draft_research_scope(task))
        self.assertEqual(task, before)

    def mechanism_task(self, **options):
        question = '解释ETF折溢价变化的可能机制，不评价买入价值'
        return self.agent.create(question, entry='ETF', intent='解释变化',
            questions=[dict(id='q1', question=question, kind='mechanism', required=True)], **options)

    def test_normalization_keeps_the_complete_long_mechanism_without_mutating_input(self):
        labels=['需求变化','订单调整','产能反应','交付变化','库存变化','定价调整','成本传导',
                '利润变化','现金回收','融资反馈','投资调整','供给反应','后续验证']
        task=self.mechanism_task()
        for count in (6,13,31):
            with self.subTest(count=count):
                steps=[dict(label=labels[i%len(labels)],kind='hypothesis',data_refs=[]) for i in range(count)]
                value=dict(logics=[dict(title='传导条件',analysis='传导关系保持待检验。',mechanism=dict(steps=steps))])
                before=deepcopy(value);report=normalize_report(value,task,[])
                self.assertEqual(report['logics'][0]['mechanism']['steps'],steps)
                self.assertEqual(value,before)

    def test_invalid_entries_do_not_consume_the_complete_path(self):
        task=self.mechanism_task()
        labels=['需求变化','订单调整','交付变化','成本传导','利润变化','现金回收']
        steps=[None,{},'invalid']+[dict(label=label,kind='condition') for label in labels]
        result=normalize_report(dict(logics=[dict(analysis='保留正文。',mechanism=dict(steps=steps))]),task,[])
        self.assertEqual([step['label'] for step in result['logics'][0]['mechanism']['steps']],labels)

    def test_unreferenced_numbers_after_the_old_cutoff_are_still_checked(self):
        task=self.mechanism_task()
        steps=[dict(label=label) for label in ['需求变化','订单调整','交付变化','成本传导','利润变化','利润增长99%']]
        report=normalize_report(dict(logics=[dict(analysis='原来的分析仍保留。',mechanism=dict(steps=steps))]),task,[])
        self.assertNotIn('mechanism',report['logics'][0])
        self.assertEqual(report['logics'][0]['analysis'],'原来的分析仍保留。')
        self.assertTrue(report['logics'][0]['reference_notes'])

    def test_mechanism_scope_has_distinct_guidance_without_an_extra_model_phase(self):
        task = self.mechanism_task()
        before = deepcopy(task)
        model = CapturingModel()
        _plan(SimpleNamespace(model=model), task, 1)
        _write(SimpleNamespace(model=model), task, [])
        scopes = [json.loads(messages[1]['content'])['research_scope'] for phase, messages in model.calls]
        self.assertEqual(scopes[0], scopes[1])
        self.assertEqual(scopes[0]['mode'], 'mechanism')
        self.assertEqual(scopes[0]['allowed_report_scopes'], ['mechanism'])
        self.assertFalse(model.calls[1][1][0]['content'].startswith(CLIENT_REPORT_BRIEF))
        self.assertEqual([phase for phase, messages in model.calls], ['draft_plan', 'draft_report'])
        self.assertEqual(task, before)

    def test_initial_question_can_select_mechanism_without_amending_obligations(self):
        task = self.task()
        before = deepcopy(task)
        report = normalize_report(dict(report_scope='mechanism',
            logics=[dict(title='传导条件', analysis='保留条件性解释。')]), task, [])
        self.assertEqual(report.get('organization'), 'mechanism')
        self.assertEqual(report['logics'][0].get('display_heading'), '传导逻辑一：传导条件')
        self.assertEqual(task, before)

    def test_mechanism_required_and_investment_optional_keeps_explanation_scope(self):
        task = self.agent.create('解释变化', questions=[
            dict(id='q1', question='解释变化', kind='mechanism', required=True),
            dict(id='q2', question='核对读数', kind='fact', required=True),
            dict(id='q3', question='可选投资含义', kind='investment', required=False)])
        self.assertEqual(draft_research_scope(task)['mode'], 'mechanism')
        before = deepcopy(task)
        report = normalize_report(dict(report_scope='research', logics=[dict(title='解释', analysis='保留原文。')]), task, [])
        self.assertEqual(report.get('organization'), 'mechanism')
        self.assertEqual(task, before)

    def test_mechanism_fallback_exposes_the_gap_without_template_sections(self):
        task = self.mechanism_task()
        done = self.agent.start(task['id'], dict(draft_report={}, data_requests=[]))
        self.assertTrue(done.get('delivery'), done.get('error'))
        self.assertEqual(done['status'], '部分完成')
        draft = done['draft_report']
        self.assertEqual(draft.get('organization'), 'mechanism')
        self.assertFalse(draft['logics'])
        self.assertEqual(done['question'], task['question'])
        main = html.document_fromstring(self.archive.report(done['id'], 1)['html']).xpath('//main')[0].text_content()
        self.assertIn('现有资料不足以解释所问变化', main)
        self.assertNotIn('传导逻辑', main)
        self.assertEqual(done['ledger']['used']['model_calls'], 0)

    def test_relevant_mechanism_uses_the_locked_entry_and_registered_source(self):
        task = self.agent.create('本轮不联网，降息通常如何影响资产估值？', research_mode='current')
        done = self.agent.start(task['id'])
        self.assertEqual(done['status'], '部分完成', done.get('error'))
        self.assertEqual(done['questions'][0]['status'], '待回答')
        draft = done['draft_report']
        self.assertEqual(draft['organization'], 'mechanism')
        logic, = draft['logics']
        citation, = logic['knowledge_refs']
        self.assertEqual(citation['id'], 'rh_k_rate_transmission')
        self.assertTrue(citation['entry_hash'])
        self.assertFalse(logic['data_refs'] or logic['source_refs'] or logic['calculation_refs'])
        page = html.document_fromstring(self.archive.report(done['id'], 1)['html'])
        self.assertIn('风险溢价', page.get_element_by_id('logic-1').text_content())
        self.assertTrue(page.get_element_by_id('draft-basis-1').xpath('.//a[starts-with(@href, "https://www.ecb.europa.eu/")]'))
        self.assertIn('European Central Bank', page.find('.//main/footer').text_content())

    def test_inflation_explanation_follows_the_requested_profit_channel(self):
        task = self.agent.create('本轮不联网，解释CPI变化如何影响企业利润。', entry='宏观', intent='解释变化')
        done = self.agent.start(task['id'])
        self.assertEqual(done['status'], '部分完成', done.get('error'))
        draft = done['draft_report']
        self.assertIn('销量、投入成本和费用', draft['conclusion'])
        logic, = draft['logics']
        self.assertEqual(logic['title'], '物价变化的利润传导')
        self.assertIn('消费价格不是企业收入', logic['analysis'])
        self.assertEqual([row['id'] for row in logic['knowledge_refs']], ['rh_k_inflation_transmission'])

    def test_firm_financing_and_bank_margin_choose_distinct_mechanisms(self):
        for question, entry, expected in (
                ('解释降息怎样通过银行贷款影响企业融资成本。', '宏观', 'rh_k_rate_transmission'),
                ('解释降息后银行净息差为何变化。', '行业', 'rh_k_bank_rate_repricing')):
            with self.subTest(question=question):
                task = self.agent.create('本轮不联网，'+question, entry=entry, intent='解释变化')
                done = self.agent.start(task['id'])
                self.assertEqual(done['status'], '部分完成', done.get('error'))
                logic, = done['draft_report']['logics']
                self.assertEqual([row['id'] for row in logic['knowledge_refs']], [expected])

    def test_an_unverified_company_event_does_not_adopt_incidental_knowledge(self):
        task = self.agent.create('本轮不联网，解释600519.SH所问资产收购对盈利的影响。',
                                 entry='事件', intent='解释变化')
        done = self.agent.start(task['id'])
        self.assertEqual(done['status'], '部分完成', done.get('error'))
        self.assertIn('rh_k_cash_profit', {entry['id'] for entry in done['knowledge']['entries']})
        self.assertFalse(done['draft_report']['logics'])
        self.assertIn('未取得可核验的事件依据', done['draft_report']['conclusion'])

    def test_explicit_investment_cannot_be_downgraded_to_mechanism(self):
        task = self.task('investment')
        report = normalize_report(dict(report_scope='mechanism',
            logics=[dict(title='原文', analysis='保留原文。')]), task, [])
        self.assertNotEqual(report.get('organization'), 'mechanism')
        self.assertEqual(task['questions'][0]['kind'], 'investment')

    def test_version_one_report_is_frozen_when_updated_to_mechanism_scope(self):
        task = self.mechanism_task()
        task[POLICY] = 1
        task['input_hash'] = input_fingerprint(task)
        self.archive.save(task)
        first = self.agent.start(task['id'], dict(draft_report={}, data_requests=[]))
        original = deepcopy(self.archive.report(task['id'], 1))
        self.assertNotIn('organization', first['draft_report'])
        self.assertEqual(draft_research_scope(first)['mode'], 'research')
        updated = self.agent.update(task['id'], host_result=dict(draft_report={}, data_requests=[]))
        self.assertEqual(updated[POLICY], 2)
        self.assertEqual(updated['draft_report'].get('organization'), 'mechanism')
        self.assertEqual(self.archive.report(task['id'], 1), original)

    def test_each_entry_keeps_an_explanation_intent_without_company_fallback(self):
        for entry in ENTRIES:
            with self.subTest(entry=entry):
                question = '解释' + entry + '观测变化的可能机制'
                task = self.agent.create(question, entry=entry, intent='解释变化')
                self.assertEqual(draft_research_scope(task)['mode'], 'mechanism')
                done = self.agent.start(task['id'], dict(draft_report={}, data_requests=[]))
                self.assertTrue(done.get('delivery'), done.get('error'))
                self.assertFalse(done['draft_report']['logics'])
                self.assertIn('仍待核对', done['draft_report']['conclusion'])
                self.assertEqual(done['status'], '部分完成')
                self.assertEqual(done['ledger']['used']['model_calls'], 0)

    def test_required_investment_is_preserved_and_mechanism_count_is_not_fixed(self):
        question = '分析传导及投资含义'
        task = self.agent.create(question, questions=[
            dict(id='q1', question=question, kind='mechanism', required=True),
            dict(id='q2', question='判断投资含义', kind='investment', required=True)])
        before = deepcopy(task)
        self.assertEqual(draft_research_scope(task)['mode'], 'research')
        normalize_report(dict(report_scope='mechanism'), task, [])
        self.assertEqual(task, before)
        questions = [dict(id='q'+str(i),question='解释渠道'+str(i),kind='mechanism',required=True) for i in range(1,12)]
        task = self.agent.create('分别解释这些渠道',questions=questions)
        submitted = [dict(title=item['question'], analysis='传导强度取决于融资条件和需求。') for item in questions]
        report = normalize_report(dict(logics=submitted), task, [])
        self.assertEqual(len(report['logics']), len(questions))
        self.assertEqual([logic['title'] for logic in report['logics']], [item['question'] for item in questions])
        self.assertEqual(report['logics'][-1]['display_heading'], '传导逻辑11：解释渠道11')

    def test_one_sentence_to_saved_report_uses_only_existing_two_phases(self):
        model = CapturingModel(charge_calls=True)
        agent = ResearchAgent(self.archive, ROOT, offline=False, model=model,
            draft_first=True, require_argument_support=True)
        self.addCleanup(agent.close_runtime)
        task = agent.create(QUESTION, objects=['600519.SH'], cutoff='2026-10-30T18:00:00+08:00')
        parameters = dict(codes='600519.SH', indicators='close', startdate='2026-10-29', enddate='2026-10-30',
            functionpara=dict(Currency='RMB', CPS='1', Fill='Omit'))
        response = dict(errorcode=0, tables=[dict(thscode='600519.SH', time=['2026-10-29', '2026-10-30'],
            table=dict(close=['101.25', '102.50']))])
        task['synthetic'] = True
        task['source_records'] = [snapshot_record(task, 'ifind', 'cmd_history_quotation', parameters, response)]
        self.archive.save(task)
        with patch.object(socket.socket, 'connect', side_effect=AssertionError('No real network')):
            done = agent.start(task['id'])
        self.assertTrue(done.get('delivery'), done.get('error'))
        self.assertEqual([phase for phase, messages in model.calls], ['draft_plan', 'draft_report'])
        self.assertEqual(done['ledger']['used']['model_calls'], 2)
        self.assertEqual(done['ledger']['used']['data_requests'], 0)
        self.assertEqual(done['questions'], task['questions'])
        self.assertEqual([row['raw_value'] for row in done['draft_report']['facts']], ['101.25', '102.50'])
        paper = self.archive.report(task['id'], 1)
        main = html.document_fromstring(paper['html']).xpath('//main')[0].text_content()
        self.assertEqual(task['draft_tracking_version'], 1)
        self.assertEqual(paper['content']['template_version'], 'rh-draft-9')
        self.assertIn('一、已取得的数据', main)
        self.assertNotIn('策略逻辑', main)
        document = json.loads(html.document_fromstring(paper['html']).get_element_by_id('rh-publication-document').text)
        self.assertIn('101.25', json.dumps(document['dataset']))


if __name__ == '__main__':
    unittest.main()
