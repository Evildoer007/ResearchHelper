"""Preserve workflow assertions with archived, publication-bound source text.

Legacy tests remain untouched. Imported test modules are not exposed as classes,
so unittest discovers only the thirteen explicitly adapted methods below.
"""

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from researchhelper.archive import ResearchArchive
from researchhelper.cases import CUTOFF, get_case
from researchhelper.engine import ResearchAgent
from researchhelper.planning import input_fingerprint
from tests import test_question_plans_offline as question_tests
from tests import test_numeric_statement_workflow_offline as numeric_tests
from tests import test_question_plan_interleaving_offline as interleaving_tests

ROOT = Path(__file__).resolve().parents[2]
PREFIX = 'tests.test_publication_guard_workflow_compat_offline.PublicationGuardWorkflowCompatibilityTests.'
REPLACED_EXPECTATIONS = {
    'tests.test_question_plans_offline.QuestionPlanTests.' + name: PREFIX + name
    for name in (
        'test_plain_fact_delivers_without_invented_thesis',
        'test_optional_question_missing_does_not_change_completion',
        'test_optional_missing_calculation_does_not_block_fact',
        'test_independent_core_branch_preserves_valid_answer',
        'test_follow_up_context_retains_fact_answer_without_adopting_it',
        'test_new_version_does_not_reuse_old_host_answers_as_new_time_facts',
        'test_report_revalidates_answer_references',
        'test_comparison_uses_registered_calculation_without_thesis',
        'test_thesis_does_not_inherit_unrelated_calculations',
    )
}
REPLACED_EXPECTATIONS.update({
    'tests.test_numeric_statement_workflow_offline.NumericStatementWorkflowTests.' + name: PREFIX + name
    for name in ('test_nonfixture_calculation_with_explicit_reference',
                 'test_registered_calculation_requires_explicit_reference')
})
REPLACED_EXPECTATIONS.update({
    'tests.test_question_plan_interleaving_offline.InterleavingPlanTests.test_agent_interleaved_execution_saves_and_resumes':
        PREFIX + 'test_agent_interleaved_execution_saves_and_resumes',
    'tests.test_rebuild_contracts_offline.RuntimeTests.test_arbitrary_host_research_reuses_engine':
        PREFIX + 'test_arbitrary_host_research_reuses_engine',
})
_NEW_ID_ADAPTATION_NOTES = {
    PREFIX + 'test_plain_fact_delivers_without_invented_thesis': 'Reuse original assertions: completed fact, no thesis node, rendered date and report evidence reference.',
    PREFIX + 'test_optional_question_missing_does_not_change_completion': 'Reuse original assertions: optional missing question preserves completion, no core gap, optional finding rendered.',
    PREFIX + 'test_optional_missing_calculation_does_not_block_fact': 'Reuse original assertions: optional missing calculation does not become a dependency or core gap.',
    PREFIX + 'test_independent_core_branch_preserves_valid_answer': 'Reuse original assertions: missing mechanism leaves partial completion while independent fact remains answered and rendered.',
    PREFIX + 'test_follow_up_context_retains_fact_answer_without_adopting_it': 'Reuse original assertions: follow-up keeps historical answer context but no adopted evidence.',
    PREFIX + 'test_new_version_does_not_reuse_old_host_answers_as_new_time_facts': 'Reuse original assertions: new time version has no approved evidence or old host outputs; version one stays answered.',
    PREFIX + 'test_report_revalidates_answer_references': 'Reuse original assertion: report rejects foreign evidence reference after valid ordinary research.',
    PREFIX + 'test_comparison_uses_registered_calculation_without_thesis': 'Archived annual rate rows replace cases.evidence; original calculation id, completion, no-thesis and report link assertions preserved.',
    PREFIX + 'test_thesis_does_not_inherit_unrelated_calculations': 'Archive policy plus both annual rates; retain two calculations and require the policy thesis to inherit neither.',
    PREFIX + 'test_nonfixture_calculation_with_explicit_reference': 'Reuse original numeric workflow assertions and original case calculation/claim; only raw evidence fixtures become anchored materials.',
    PREFIX + 'test_registered_calculation_requires_explicit_reference': 'Reuse original negative assertions: calculation succeeds but absent answer calculation_refs leaves answer unaccepted.',
    PREFIX + 'test_agent_interleaved_execution_saves_and_resumes': 'Real execution interrupted after first answer through a subclass that calls the original step; reopen archive, resume, preserve node chain, cutoff, zero calls and export digest.',
    PREFIX + 'test_arbitrary_host_research_reuses_engine': 'Preserve ordinary arbitrary-host completed research assertion; archived definition and matching publication anchor replace synthetic success labels.',
}
ADAPTATION_NOTES = {
    original_id: _NEW_ID_ADAPTATION_NOTES[new_id]
    for original_id, new_id in REPLACED_EXPECTATIONS.items()
}


class PublicationGuardWorkflowCompatibilityTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='rh-workflow-compat-')
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True)
        self.fixture = SimpleNamespace(agent=self.agent)

    def source_spec(self, task, ident, subject, text, question_ids=('q1',), value=None):
        publication = '发布日期2026年7月19日。'
        task = self.agent.add_material(task['id'], ident + '.txt', (text + '\n\n' + publication).encode())
        record = task['materials'][-1]
        spec = {'id': ident, 'artifact_id': record['id'], 'object': subject,
            'text': text, 'record_locator': {'part': '段落1'}, 'published_at': '2026-07-19',
            'period': '2026年度规则' if value is not None else '2026年7月',
            'nature': '合同政策条款', 'unit': '%/年' if value is not None else '不适用',
            'publication': {'at': '2026-07-19', 'basis': '同原件独立发布段落',
                'precision': '日', 'timezone': 'Asia/Shanghai', 'certainty': '已确认',
                'basis_ref': {'kind': 'material', 'artifact_id': record['id'],
                    'snapshot_hash': record['snapshot_hash'], 'part': '段落2', 'quote': publication}},
            'question_ids': list(question_ids), 'review': {'question_ids': list(question_ids),
                'allowed_natures': ['合同政策条款'], 'conditions': ['仅核对该原件范围和共同适用期间'],
                'reason': '条款原文对应已登记研究问题'}}
        if value is not None:
            spec.update(raw_token=str(value), raw_value=value, value=value, raw_unit='%/年', conversion_factor=1)
        return spec

    def create(self, questions=None, **options):
        task = self.agent.create('核查原文日期', cutoff=CUTOFF, questions=questions or [
            {'id': 'q1', 'question': '原文何时生效', 'kind': 'fact'}], **options)
        self.fact = self.source_spec(task, 'original', '政策原文', '生效时间为2026年7月19日。')
        return self.agent.archive.get(task['id'])

    def facts(self, answers=None, **extra):
        return dict(evidence_specs=[self.fact], answers=answers or {
            'q1': {'answer': '原文于2026年7月19日生效。', 'refs': ['original']}}, **extra)

    def fee_specs(self, task):
        return [self.source_spec(task, ident, subject,
            subject + '同一人民币年度规则下年费率为' + str(value) + '%/年。', value=value)
            for ident, subject, value in [('e1', '验收ETF-A', .2), ('e2', '验收ETF-B', .6)]]

    def test_plain_fact_delivers_without_invented_thesis(self):
        question_tests.QuestionPlanTests.test_plain_fact_delivers_without_invented_thesis(self)

    def test_optional_question_missing_does_not_change_completion(self):
        question_tests.QuestionPlanTests.test_optional_question_missing_does_not_change_completion(self)

    def test_optional_missing_calculation_does_not_block_fact(self):
        task = self.create([
            {'id': 'q1', 'question': '原文日期', 'kind': 'fact', 'group': 'date'},
            {'id': 'extra', 'question': '可选费用比较', 'kind': 'comparison', 'required': False,
             'group': 'fees', 'method_needs': ['fee_difference_v1']}])
        spec = dict(get_case('etf')['calculation'], question_ids=['extra'])
        final = self.agent.start(task['id'], self.facts(calculations=[spec]))
        self.assertEqual(final['status'], '已完成', final.get('error'))
        date_node = next(node for node in final['nodes'] if node['id'] == 'answer')
        self.assertNotIn('calculation', date_node['dependencies'])
        self.assertEqual(final['gaps'], [])

    def test_independent_core_branch_preserves_valid_answer(self):
        question_tests.QuestionPlanTests.test_independent_core_branch_preserves_valid_answer(self)

    def test_follow_up_context_retains_fact_answer_without_adopting_it(self):
        question_tests.QuestionPlanTests.test_follow_up_context_retains_fact_answer_without_adopting_it(self)

    def test_new_version_does_not_reuse_old_host_answers_as_new_time_facts(self):
        question_tests.QuestionPlanTests.test_new_version_does_not_reuse_old_host_answers_as_new_time_facts(self)

    def test_report_revalidates_answer_references(self):
        question_tests.QuestionPlanTests.test_report_revalidates_answer_references(self)

    def test_comparison_uses_registered_calculation_without_thesis(self):
        case = get_case('etf')
        task = self.agent.create('比较年度费用', entry='ETF', intent='比较对象', cutoff=CUTOFF, questions=[
            {'question': '同口径费用差是多少', 'kind': 'comparison', 'method_needs': ['fee_difference_v1']}])
        spec = dict(case['calculation'], id='fee_delta', question_ids=['q1'])
        host = {'evidence_specs': self.fee_specs(task), 'calculations': [spec], 'answers': {
            'q1': {'answer': 'A比B低0.40个百分点，不等于实际收益差。', 'refs': ['e1', 'e2'], 'calculation_refs': ['fee_delta']}}}
        final = self.agent.start(task['id'], host)
        self.assertEqual(final['status'], '已完成', final.get('error'))
        self.assertEqual(final['calculations'][0]['id'], 'fee_delta')
        self.assertEqual(final['theses'], [])
        self.assertIn('#fee_delta', self.agent.export(task['id'])['html'])

    def test_thesis_does_not_inherit_unrelated_calculations(self):
        case = get_case('etf')
        task = self.create()
        # Reopen the catalogue that owns these existing fee calculations.
        task['fee_method_catalogue_version'] = 1
        task['input_hash'] = input_fingerprint(task)
        self.agent.archive.save(task)
        host = self.facts(calculations=[case['calculation'], dict(case['calculation'], id='other_delta')],
            theses=[{'title': '原文日期已确认', 'judgment': '支持', 'support': ['original'],
                'counter': [], 'investment_meaning': '仅核查日期', 'limitation': '限定原文'}])
        host['evidence_specs'] += self.fee_specs(task)
        final = self.agent.start(task['id'], host)
        self.assertEqual(final['status'], '已完成', final.get('error'))
        self.assertEqual(len(final['calculations']), 2)
        self.assertEqual(final['theses'][0]['calculation_refs'], [])

    def nonfixture_host(self, explicit_reference):
        question = '比较两只基金的已披露年费率'
        task = self.agent.create(question, entry='ETF', intent='比较对象', cutoff=CUTOFF, questions=[
            {'id': 'q1', 'question': question, 'kind': 'comparison', 'method_needs': ['fee_difference_v1']}])
        case = get_case('etf')
        calculation = dict(case['calculation'], id='host_fee', question_ids=['q1'])
        answer = {'answer': '费用差为-0.4个百分点，不代表收益优势。', 'refs': ['e1', 'e2']}
        if explicit_reference:
            answer['calculation_refs'] = ['host_fee']
        theses = [dict(thesis, calculation_refs=['host_fee'], question_ids=['q1']) for thesis in case['claims']]
        host = {'evidence_specs': self.fee_specs(task), 'calculations': [calculation],
                'theses': theses, 'answers': {'q1': answer}}
        return self.agent.start(task['id'], host)

    def test_nonfixture_calculation_with_explicit_reference(self):
        done = self.nonfixture_host(True)
        self.assertEqual(done['status'], '已完成', done.get('error'))
        self.assertIsNone(done.get('fixture'))
        self.assertFalse(done['synthetic'])
        self.assertAlmostEqual(done['calculations'][0]['output']['value'], -.4)
        self.assertIn('费用差为-0.4', self.agent.export(done['id'])['html'])

    def test_registered_calculation_requires_explicit_reference(self):
        numeric_tests.NumericStatementWorkflowTests.test_registered_calculation_requires_explicit_reference(self)

    def test_agent_interleaved_execution_saves_and_resumes(self):
        class CancelAfterFirstAnswer(ResearchAgent):
            def _step(agent, current, node, host_result):
                result = super()._step(current, node, host_result)
                if node == 'answer':
                    agent.cancel(current['id'])
                return result

        self.agent = CancelAfterFirstAnswer(self.agent.archive, ROOT, offline=True)
        questions = [interleaving_tests.question('q1'),
            interleaving_tests.question('q2', 'mechanism', ['q1']),
            interleaving_tests.question('q3', 'comparison', ['q2'])]
        task = self.agent.create('核查政策、解释影响并比较', cutoff=CUTOFF, questions=questions)
        text = '政策已生效。A与B适用同样条件，影响相同。'
        spec = self.source_spec(task, 'original', '政策原文', text, question_ids=['q1', 'q2', 'q3'])
        host = {'evidence_specs': [spec], 'answers': {
            q['id']: {'answer': text, 'refs': ['original']} for q in questions},
            'theses': [{'title': '政策影响相同', 'judgment': '支持', 'support': ['original'], 'counter': [],
                'investment_meaning': '仅解释适用影响', 'limitation': '限定原件', 'question_ids': ['q2']}]}
        stopped = self.agent.start(task['id'], host)
        self.assertEqual(stopped['status'], '已取消', stopped.get('error'))
        restored = ResearchAgent(ResearchArchive(self.agent.archive.root), ROOT, offline=True)
        resumed = restored.start(task['id'])
        self.assertEqual(resumed['status'], '已完成', resumed.get('error'))
        self.assertTrue(all(q['status'] == '已回答' for q in resumed['questions']))
        self.assertEqual(resumed['cutoff'], CUTOFF)
        self.assertEqual(resumed['ledger']['used']['model_calls'], 0)
        self.assertEqual(resumed['ledger']['used']['data_requests'], 0)
        owners = {q: node for node in resumed['nodes'] if node['kind'] in ('answer', 'thesis')
                  for q in node['questions']}
        for before, after in [('q1', 'q2'), ('q2', 'q3')]:
            self.assertNotEqual(owners[before]['id'], owners[after]['id'])
            self.assertIn(owners[before]['id'], owners[after]['dependencies'])
            self.assertLess(resumed['nodes'].index(owners[before]), resumed['nodes'].index(owners[after]))
        self.assertEqual(restored.export(task['id'])['digest'], resumed['delivery']['digest'])

    def test_arbitrary_host_research_reuses_engine(self):
        task = self.agent.create('核查原文公开事实', entry='事件', intent='验证观点', cutoff=CUTOFF)
        spec = self.source_spec(task, 'h1', '事件原文', '原文定义已核验。')
        host = {'evidence_specs': [spec], 'answers': {'q1': {'answer': '已确认原文定义。', 'refs': ['h1']}},
            'theses': [{'title': '原文定义已确认', 'judgment': '支持', 'support': ['h1'], 'counter': [],
                'investment_meaning': '仅核查事实，不形成投资机会。', 'limitation': '限定原件'}]}
        self.assertEqual(self.agent.start(task['id'], host)['status'], '已完成')
