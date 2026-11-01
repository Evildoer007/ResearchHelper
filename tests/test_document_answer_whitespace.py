"""Independent regression for exact original quotations at answer boundaries."""
import copy
from pathlib import Path
import tempfile
import unittest

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, DOCUMENT_CONTENT_INTENT, document_content_answer
from researchhelper.engine import ResearchAgent
from researchhelper.numeric_integrity import bind_numeric_statement
from researchhelper.reports import compose

ROOT = Path(__file__).resolve().parents[1]
CLAUSE = '费用上限为100百万元。'


class DocumentAnswerWhitespaceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='document-answer-whitespace-', dir=ROOT / 'result/acceptance')
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True)

    def run_clause(self, boundary):
        task = self.agent.create('原件费用上限写了什么？', intent=DOCUMENT_CONTENT_INTENT,
                                 objects=['材料示例政策'], cutoff='2026-07-20T18:00:00+08:00')
        quote = CLAUSE + boundary
        task = self.agent.add_material(task['id'], 'policy.txt', ('材料示例政策\n\n' + quote).encode())
        spec = {'id': 'clause', 'artifact_id': task['materials'][0]['id'], 'object': '材料示例政策',
            'record_locator': {'part': '段落2'}, 'text': quote, 'period': '原件条款',
            'nature': '合同政策条款', 'unit': '百万元', 'raw_unit': '百万元', 'raw_token': '100',
            'raw_value': 100, 'value': 100, 'conversion_factor': 1, 'question_ids': ['q1'],
            'usage_scope': 'document_content', 'published_at': None,
            'publication': {'at': None, 'basis': '首次公开时间未核验', 'certainty': '未知',
                            'precision': '未知', 'timezone': '未知', 'basis_ref': None},
            'review': {'question_ids': ['q1'], 'allowed_natures': ['合同政策条款'],
                       'conditions': ['仅核对该原件所载条款'], 'reason': '摘录直接对应文件内容问题'}}
        return self.agent.start(task['id'], {'evidence_specs': [spec]})

    def assert_saved_verbatim(self, boundary):
        task = self.run_clause(boundary)
        self.assertEqual(task['status'], '已完成', task['gaps'])
        self.assertEqual(task['evidence'][0]['text'], CLAUSE + boundary)
        self.assertEqual(task['questions'][0]['answer'], document_content_answer(task['evidence']))
        self.assertIsNotNone(task['delivery'])
        self.assertEqual(task['ledger']['dispatches'], [])
        self.assertEqual((task['calculations'], task['theses'], task['tracking']), ([], [], []))
        compose(task)
        return task

    def test_final_lf_is_not_removed(self):
        self.assert_saved_verbatim('\n')

    def test_final_space_is_not_removed(self):
        self.assert_saved_verbatim(' ')

    def test_final_tab_is_not_removed(self):
        self.assert_saved_verbatim('\t')

    def test_mixed_final_whitespace_is_not_removed(self):
        self.assert_saved_verbatim('\t \n')

    def test_exact_quote_without_boundary_is_still_valid(self):
        self.assert_saved_verbatim('')

    def test_validator_still_rejects_trimmed_checked_quote(self):
        task = self.assert_saved_verbatim('\n')
        refs = [item['id'] for item in task['evidence']]
        with self.assertRaises(ContractError):
            bind_numeric_statement(task, {'answer': task['questions'][0]['answer'].strip(),
                'question_ids': ['q1']}, ['answer'], refs, [])

    def test_frozen_answer_cannot_be_edited_after_verification(self):
        task = self.assert_saved_verbatim('\n')
        changed = copy.deepcopy(task)
        changed['questions'][0]['answer'] = changed['questions'][0]['answer'].strip()
        with self.assertRaises(ContractError):
            compose(changed)

    def test_original_scope_cannot_be_upgraded_to_investment_scope(self):
        task = self.assert_saved_verbatim('\n')
        changed = copy.deepcopy(task)
        changed['intent'] = '综合研究'
        with self.assertRaises(ContractError):
            compose(changed)


if __name__ == '__main__':
    unittest.main()
