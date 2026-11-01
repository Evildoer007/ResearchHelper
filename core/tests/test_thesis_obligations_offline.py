"""A partial study cannot present a determinate claim without its prerequisites.

New isolated regression tests. No suppliers, models or production data are
used. These fabricated policies exercise workflow rules, not finance quality.
"""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.cases import CUTOFF
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.reports import compose

ROOT = Path(__file__).resolve().parents[2]


class ThesisObligationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='thesis-obligation-', dir=ROOT/'result/acceptance')
        self.addCleanup(directory.cleanup)
        self.agent = ResearchAgent(ResearchArchive(directory.name), ROOT, offline=True)
        denied = patch('socket.socket.connect', side_effect=AssertionError('No network'))
        denied.start()
        self.addCleanup(denied.stop)

    def run_policy(self, judgment='支持', ready=False, needs_method=False):
        questions = [dict(id='basis', question='政策适用条件', kind='fact', group='basis'),
                     dict(id='dependent', question='政策影响是否成立', kind='mechanism',
                          group='dependent', dependencies=['basis']),
                     dict(id='independent', question='另一条款的影响', kind='mechanism', group='independent')]
        if needs_method:
            questions[1]['method_needs'] = ['fee_difference_v1']
        task = self.agent.create('核查基金政策条件与两条影响路径', entry='ETF', cutoff=CUTOFF, questions=questions)
        task['synthetic'] = True
        self.agent.archive.save(task)
        text = '合成基金政策条款仅适用于满足资格的企业。另一条款独立适用。'
        task = self.agent.add_material(task['id'], 'synthetic-policy.txt',
            ('发布日期：2026年7月19日。\n\n' + text).encode())
        artifact = task['materials'][-1]
        publication = dict(kind='material', artifact_id=artifact['id'], snapshot_hash=artifact['snapshot_hash'],
                           part='段落1', quote='发布日期：2026年7月19日。')
        source = dict(id='original', artifact_id=artifact['id'], object='合成基金政策',
                      record_locator={'part':'段落2'}, text=text, period='政策条款',
                      nature='合同政策条款', unit='不适用', question_ids=['basis', 'dependent', 'independent'],
                      published_at='2026-07-19', publication=dict(at='2026-07-19', precision='日',
                          timezone='Asia/Shanghai', certainty='已确认', basis='原件明确发布日期', basis_ref=publication),
                      review=dict(question_ids=['basis', 'dependent', 'independent'],
                          allowed_natures=['合同政策条款'], conditions=['仅核对合成条款适用影响'],
                          reason='完整文字原件和公开时点明确，不推断金融市场事实'))
        answers = {key: dict(answer='限定合成条款核对，不推断市场价格。', refs=['original'])
                   for key in ('dependent', 'independent')}
        if ready:
            answers['basis'] = dict(answer=source['text'], refs=['original'])
        theses = [dict(title='依赖条件的政策影响', judgment=judgment, support=['original'], counter=[],
                       question_ids=['dependent'], investment_meaning='仅解释合成条款适用影响',
                       limitation='资格和执行仍需按原条件核对'),
                  dict(title='独立条款的影响', judgment='支持', support=['original'], counter=[],
                       question_ids=['independent'], investment_meaning='独立的合成条款说明',
                       limitation='不延伸为价格判断')]
        final = self.agent.start(task['id'], dict(evidence_specs=[source], answers=answers, theses=theses))
        return final, theses

    def test_missing_dependency_blocks_support_and_opposition_not_independent_claim(self):
        for judgment in ('支持', '反对'):
            final, _ = self.run_policy(judgment=judgment)
            self.assertEqual(final['status'], '部分完成', final.get('error'))
            self.assertEqual([thesis['question_ids'] for thesis in final['theses']], [['independent']])
            self.assertTrue(any('前置问题' in gap for gap in final['gaps']))
            report = self.agent.export(final['id'])
            self.assertNotIn('依赖条件的政策影响', report['html'])
            self.assertIn('独立条款的影响', report['html'])

    def test_missing_method_blocks_only_the_dependent_determinate_claim(self):
        final, _ = self.run_policy(ready=True, needs_method=True)
        self.assertEqual(final['status'], '部分完成', final.get('error'))
        self.assertEqual([thesis['question_ids'] for thesis in final['theses']], [['independent']])
        self.assertTrue(any('必要计算' in gap for gap in final['gaps']))

    def test_satisfied_dependencies_keep_both_claims(self):
        final, _ = self.run_policy(ready=True)
        self.assertEqual(final['status'], '已完成', final.get('error'))
        self.assertEqual(len(final['theses']), 2)

    def test_insufficient_claim_explains_gap_without_upgrading_completion(self):
        final, _ = self.run_policy(judgment='证据不足', needs_method=True)
        self.assertEqual(final['status'], '部分完成', final.get('error'))
        self.assertCountEqual([thesis['judgment'] for thesis in final['theses']], ['证据不足', '支持'])

    def test_save_gate_rechecks_prerequisites_not_only_question_status(self):
        final, _ = self.run_policy(ready=True)
        altered = copy.deepcopy(final)
        for question in altered['questions']:
            if question['id'] in ('basis', 'dependent'):
                question['status'] = '未回答'
        with self.assertRaises(ContractError):
            compose(altered)

    def test_method_for_another_question_does_not_satisfy_this_claim(self):
        from researchhelper.planning import thesis_obligations
        final, _ = self.run_policy(ready=True)
        altered = copy.deepcopy(final)
        for question in altered['questions']:
            if question['id'] == 'dependent':
                question['method_needs'] = ['fee_difference_v1']
        altered['calculations'] = [dict(id='fee_other', method='fee_difference_v1', question_ids=['independent'])]
        self.assertEqual(thesis_obligations(altered, ['dependent'], ['fee_other']),
                         [dict(question_id='dependent', missing_dependencies=[], missing_methods=['fee_difference_v1'])])


if __name__ == '__main__':
    unittest.main()
