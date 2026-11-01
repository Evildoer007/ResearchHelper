"""Independent financial reasoning-chain acceptance, without external calls.

The originals and reasoning are deliberately synthetic and visibly labelled.
This checks binding, dependencies, typed contrary evidence and frozen delivery;
it does not evaluate a live model's investment judgement or a real company.
"""
import copy
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, METHOD_REGISTRY
from researchhelper.engine import ResearchAgent
from researchhelper.reports import compose, research_figures
from researchhelper.visual import reading_dependency_order

ROOT = Path(__file__).resolve().parents[1]
RESOURCES = Path(os.environ.get('RESEARCHHELPER_FIXTURE_RESOURCES', str(ROOT))).resolve()
ENTITY = '合成财务验收主体'
PERIOD = '2026年上半年'
INPUTS = {'revenue': ('营业收入', 100), 'net_profit': ('净利润', 12),
          'operating_cash_flow': ('经营活动产生的现金流量净额', -8)}
METHODS = ('net_profit_margin_v1', 'operating_cash_profit_ratio_v1')
NEEDS = {
    'financial': ['同口径营业收入', '同口径报表净利润', '同口径经营现金流净额'],
    'mechanism': ['利润与现金流的原始披露', '管理层解释及其性质', '逾期与收回结果的披露'],
    'meaning': ['利润与现金流的原始披露', '管理层解释及其性质', '逾期与收回结果的披露'],
}


class MainAnswerReader(HTMLParser):
    def __init__(self, paper):
        super().__init__()
        self.answers, self.current, self.in_part = {}, None, False
        self.feed(paper)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if 'data-main-question-ref' in attrs:
            self.current = attrs['data-main-question-ref']
            assert self.current not in self.answers
            self.answers[self.current] = ''
        if tag == 'p' and 'data-main-answer-part' in attrs:
            self.in_part = True

    def handle_endtag(self, tag):
        if tag == 'p':
            self.in_part = False
        if tag == 'div':
            self.current = None

    def handle_data(self, value):
        if self.current and self.in_part:
            self.answers[self.current] += value


def prepare_chain(agent, *, omit=None):
    """Every fact, context and date is bound to an actual retained TXT original."""
    questions = [
        dict(id='financial', kind='fact', group='financial',
             question='利润与经营现金流存在怎样的分歧，登记比率如何计算',
             method_needs=list(METHODS), evidence_needs=NEEDS['financial']),
        dict(id='mechanism', kind='mechanism', group='mechanism', dependencies=['financial'],
             question='管理层的回款延后解释是否已经排除客户信用风险',
             evidence_needs=NEEDS['mechanism']),
        dict(id='meaning', kind='investment', group='meaning', dependencies=['mechanism'],
             question='在已有证据范围内，怎样设置现金兑现的研究验证条件',
             evidence_needs=NEEDS['meaning']),
    ]
    task = agent.create('合成验收：利润与现金流分歧及其研究含义', entry='个股',
        objects=[ENTITY], cutoff='2026-10-02T18:00:00+08:00', questions=questions)
    task['synthetic'] = True
    agent.archive.save(task)
    specifications = []

    def retain(ident, text, nature, coverage, metric=None):
        updated = agent.add_material(task['id'], ident + '.txt', text.encode('utf-8'))
        original = updated['materials'][-1]
        anchor = dict(kind='material', artifact_id=original['id'],
            snapshot_hash=original['snapshot_hash'], part='段落1', quote=text)
        specification = dict(id=ident, artifact_id=original['id'], object=ENTITY,
            record_locator={'part': '段落1'}, text=text, period=PERIOD, nature=nature,
            unit='不适用', question_ids=list(coverage), published_at='2026-10-01',
            publication=dict(at='2026-10-01', precision='日', timezone='Asia/Shanghai',
                certainty='已确认', basis='合成原件明确发布日期', basis_ref=anchor),
            review=dict(question_ids=list(coverage), evidence_need_texts=coverage,
                allowed_natures=[nature], conditions=['仅限合成主体已载明的期间与口径'],
                reason='逐项核对原件和问题适用性；管理层解释保留其展望性质'))
        if metric:
            value = INPUTS[metric][1]
            context = dict(entity_id=ENTITY, currency='CNY', period=PERIOD,
                accounting_scope='consolidated', accounting_standard='CAS', metric=metric)
            specification.update(raw_value=value, value=value, raw_token=str(value),
                raw_unit='百万元', unit='百万元', conversion_factor=1,
                calculation_context=context,
                calculation_context_basis={key: copy.deepcopy(anchor) for key in context})
        specifications.append(specification)

    for index, (field, (label, value)) in enumerate(INPUTS.items()):
        statement = '合并现金流量表' if field == 'operating_cash_flow' else '合并利润表'
        text = f'{ENTITY}，{PERIOD}，人民币，企业会计准则，{statement}：{label}{value}百万元。发布于2026-10-01。'
        retain(field, text, '已实现事实', {'financial': [NEEDS['financial'][index]],
            'mechanism': [NEEDS['mechanism'][0]], 'meaning': [NEEDS['meaning'][0]]}, field)
    retain('management', f'{ENTITY}，{PERIOD}。管理层认为部分客户回款延后影响了经营现金流，预计后续收款改善，但没有给出已实现的收回结果。发布于2026-10-01。',
        '管理层展望', {key: [NEEDS[key][1]] for key in ('mechanism', 'meaning')})
    retain('overdue', f'{ENTITY}，{PERIOD}。财务说明载明部分应收账款逾期，未说明其与延期回款客户的对应关系，未披露后续收回结果。发布于2026-10-01。',
        '已实现事实', {key: [NEEDS[key][2]] for key in ('mechanism', 'meaning')})
    selected = [item for item in specifications if item['id'] != omit]
    financial_refs = [key for key in INPUTS if key != omit]
    all_refs = [item['id'] for item in selected]
    parameters = dict(METHOD_REGISTRY[METHODS[0]]['parameters'],
        period=PERIOD, accounting_scope='consolidated', accounting_standard='CAS')
    calculations = [dict(id='profit_margin', method=METHODS[0], question_ids=list(NEEDS),
        inputs={'revenue': 'revenue', 'net_profit': 'net_profit'}, parameters=parameters),
        dict(id='cash_conversion', method=METHODS[1], question_ids=list(NEEDS),
        inputs={'operating_cash_flow': 'operating_cash_flow', 'net_profit': 'net_profit'}, parameters=parameters)]
    financial = ('同一合并范围内，营业收入100百万元、净利润12百万元，净利润率为12%；'
        '经营现金流净额为-8百万元，经营现金流净利润比为-66.67%。'
        '账面盈利与现金流入没有同步，单独的比率不能定位具体经营原因，也不能证明盈利质量恶化。')
    mechanism = ('回款延后是管理层提出的解释，不是已经完成核验的因果结论。'
        '原件同时披露部分应收账款逾期，且未披露其与延期回款客户的对应关系和后续收回结果。'
        '因此，现有资料不能排除客户信用风险，也不能把管理层对后续收款的预期写成现金流已经恢复。')
    meaning = ('当前分歧应转化为现金兑现的验证条件，而不是价格上涨催化。'
        '后续需要同时观察经营现金流、应收款账龄及回款披露；现金流改善若主要来自付款延后，也不能直接当作客户回款改善。'
        '只有回款结果与报表变化能够相互核对，才适合重新评估这条经营解释。'
        '本次未核验估值、交易价格和市场预期，不生成目标价、买卖建议或上涨概率。')
    answers = {'financial': dict(answer=financial, refs=financial_refs,
        calculation_refs=['profit_margin', 'cash_conversion']),
        'mechanism': dict(answer=mechanism, refs=all_refs),
        'meaning': dict(answer=meaning, refs=all_refs)}
    theses = [dict(title='回款延后不足以排除信用风险', judgment='支持',
        question_ids=['mechanism'], support=[ref for ref in all_refs if ref != 'overdue'],
        counter=['overdue'] if omit != 'overdue' else [],
        investment_meaning='保留回款延后的解释，同时核对逾期款项和客户信用变化，不能宣布因果已验证。',
        limitation='管理层展望不是已实现回款，账龄及收回结果仍未取得。'),
        dict(title='现金兑现是验证项，不是已确认催化', judgment='支持',
            question_ids=['meaning'], support=[ref for ref in all_refs if ref != 'overdue'],
            counter=['overdue'] if omit != 'overdue' else [],
            investment_meaning='先验证现金兑现路径，再讨论其对估值和价格的影响；没有市场依据，不形成交易判断。',
            limitation='没有现价、估值及市场预期依据，不提供交易或收益判断。',
            tracking=dict(indicator='经营现金流、应收款账龄与回款披露', source='后续财务报告及回款公告',
                window='下一次定期披露与重大回款公告',
                condition='回款说明能与现金流及账龄变化相互核对；仍逾期则保留信用风险解释'))]
    return task, dict(evidence_specs=selected, calculations=calculations, answers=answers, theses=theses)


class FundamentalReasoningChainTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='fundamental-chain-', dir=ROOT / 'result/acceptance')
        self.addCleanup(directory.cleanup)
        self.agent = ResearchAgent(ResearchArchive(directory.name), RESOURCES, offline=True)
        denied = patch.object(socket.socket, 'connect', side_effect=AssertionError('External calls are forbidden'))
        denied.start()
        self.addCleanup(denied.stop)

    def run_chain(self, **options):
        task, host = prepare_chain(self.agent, **options)
        return self.agent.start(task['id'], host)

    def test_complete_chain_preserves_both_realized_facts_and_management_uncertainty(self):
        final = self.run_chain()
        self.assertEqual(final['status'], '已完成', {'error': final.get('error'), 'gaps': final['gaps']})
        self.assertEqual([q['status'] for q in final['questions']], ['已回答'] * 3)
        self.assertEqual(len(final['calculations']), 2)
        self.assertEqual(len(final['theses']), 2)
        management = next(item for item in final['evidence'] if item['id'] == 'management')
        self.assertEqual(management['nature'], '管理层展望')
        self.assertEqual(final['theses'][0]['counter'], ['overdue'])
        self.assertEqual(final['tracking'][0]['status'], '待主动复核')
        delivery = self.agent.export(final['id'])
        self.assertIn('合成示例，非真实研究', delivery['html'])
        self.assertEqual(MainAnswerReader(delivery['html']).answers,
            {q['id']: q['answer'] for q in final['questions']})
        self.assertEqual(len(research_figures(delivery['content'])), 2)
        self.assertEqual(final['ledger']['used']['model_calls'], 0)
        self.assertEqual(final['ledger']['used']['data_requests'], 0)
        self.assertEqual(self.agent.archive.report(final['id'], 1)['content'], delivery['content'])

    def test_missing_cash_prevents_calculation_and_dependent_investment_completion(self):
        final = self.run_chain(omit='operating_cash_flow')
        self.assertEqual(final['status'], '部分完成', final.get('error'))
        self.assertFalse(final['theses'])
        self.assertTrue(all(q['status'] != '已回答' for q in final['questions']))
        self.assertNotIn('cash_conversion', [item['id'] for item in final['calculations']])

    def test_missing_management_explanation_preserves_financial_branch_not_causal_claim(self):
        final = self.run_chain(omit='management')
        self.assertEqual(final['status'], '部分完成', final.get('error'))
        self.assertEqual(final['questions'][0]['status'], '已回答')
        self.assertFalse(final['theses'])
        self.assertEqual(len(final['calculations']), 2)
        self.assertTrue(any('管理层解释' in gap for gap in final['gaps']))

    def test_missing_contrary_original_cannot_pass_by_leaving_counter_empty(self):
        final = self.run_chain(omit='overdue')
        self.assertEqual(final['status'], '部分完成', final.get('error'))
        self.assertEqual(final['questions'][0]['status'], '已回答')
        self.assertFalse(final['theses'])
        self.assertTrue(any('逾期' in gap for gap in final['gaps']))

    def test_future_cash_original_cannot_become_historical_support(self):
        task, host = prepare_chain(self.agent)
        source = next(item for item in host['evidence_specs'] if item['id'] == 'operating_cash_flow')
        source['publication']['at'] = source['published_at'] = '2026-10-03'
        final = self.agent.start(task['id'], host)
        self.assertNotEqual(final['status'], '已完成')
        self.assertFalse(final['theses'])
        self.assertTrue(final['rejected_evidence'])

    def test_save_gate_cannot_promote_missing_prerequisite_or_rewrite_cash_output(self):
        final = self.run_chain()
        changed = copy.deepcopy(final)
        changed['questions'][0]['status'] = '未回答'
        with self.assertRaises(ContractError):
            compose(changed)
        changed = copy.deepcopy(final)
        changed['calculations'][1]['output']['value'] = 66.66666666666667
        with self.assertRaises(ContractError):
            compose(changed)

    def test_reading_order_uses_only_explicit_dependencies_not_matching_words(self):
        questions = [dict(id='cash', question='现金流事实'),
            dict(id='unrelated', question='现金流事实的相同标题'),
            dict(id='mechanism', dependencies=['cash']),
            dict(id='meaning', dependencies=['mechanism'])]
        original = copy.deepcopy(questions)
        self.assertEqual(reading_dependency_order(['meaning'], questions), ['cash', 'mechanism', 'meaning'])
        self.assertEqual(reading_dependency_order(['unrelated'], questions), ['unrelated'])
        self.assertEqual(questions, original)

    def test_shared_prerequisite_is_presented_once_without_rewriting_frozen_content(self):
        final = self.run_chain()
        delivery = self.agent.export(final['id'])
        frozen = copy.deepcopy(delivery['content'])
        self.assertEqual(delivery['html'].count("data-main-question-ref='financial'"), 1)
        self.assertEqual(delivery['html'].count("data-main-question-ref='mechanism'"), 1)
        self.assertEqual(delivery['html'].count("data-main-question-ref='meaning'"), 1)
        self.assertEqual(delivery['content'], frozen)
        self.assertIn("data-answer-role='prerequisite'", delivery['html'])
        self.assertEqual(final['theses'][0]['support'], ['revenue', 'net_profit', 'operating_cash_flow', 'management'])


def retain_positive_case():
    """Keep one clearly synthetic full report for subsequent visual inspection."""
    output = Path(tempfile.mkdtemp(prefix='fundamental-report-', dir=ROOT / 'result/acceptance'))
    agent = ResearchAgent(ResearchArchive(output / 'store'), RESOURCES, offline=True)
    task, host = prepare_chain(agent)
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('External calls are forbidden')):
        final = agent.start(task['id'], host)
    assert final['status'] == '已完成', {'error': final.get('error'), 'gaps': final['gaps']}
    delivery = agent.export(task['id'])
    (output / 'report.html').write_text(delivery['html'], encoding='utf-8')
    record = dict(status='scoped_passed', scope='synthetic_financial_chain_only',
        task_id=task['id'], report=str(output / 'report.html'), content_hash=delivery['content']['content_hash'],
        questions=3, calculations=2, theses=2, synthetic=True, provider_calls=0, model_calls=0,
        question_answers={q['id']: q['answer'] for q in final['questions']},
        financial_quality_accepted=False, actual_model_quality_accepted=False)
    (output / 'receipt.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
    return record
