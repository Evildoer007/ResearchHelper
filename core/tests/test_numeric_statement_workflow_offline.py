"""Numeric prose must survive the actual research and frozen-report boundaries."""

import copy
import importlib.util
import unittest

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.reasoning import analysis_contract
from researchhelper.reports import compose, render
from tests import test_management_custody_fee_offline as fixture


class NumericStatementWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.ManagementCustodyFeeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def execute(self, text=None, segments=None, theses=None):
        task, host = self.fixture.prepare()
        if text is not None:
            host['answers']['q1']['answer'] = text
        if segments is not None:
            host['answers']['q1'].pop('answer', None)
            host['answers']['q1']['numeric_segments'] = {'answer': segments}
        if theses is not None:
            host['theses'] = theses(host)
        return self.fixture.agent.start(task['id'], host)

    def test_wrong_magnitude_direction_unit_and_raw_rate_remain_unanswered(self):
        for text in ('A比B低40个百分点。', 'A比B高0.40个百分点。', 'A比B低0.40元。',
                     'A的管理费年费率为15%，A比B低0.40个百分点。', 'A比B低四十个百分点。'):
            with self.subTest(text=text):
                done = self.execute(text)
                self.assertEqual(done['status'], '部分完成', done.get('error'))
                self.assertEqual(done['questions'][0]['status'], '未回答')
                self.assertAlmostEqual(done['calculations'][0]['output']['value'], -.4)
                self.assertNotIn(text, render(compose(done), fixture.ROOT))
                self.assertEqual(done['ledger']['used']['model_calls'], 0)
                self.assertEqual(done['ledger']['used']['data_requests'], 0)

    def test_date_code_and_correct_legacy_direction_preserved(self):
        text = '2026年度规则中ETF-A比ETF-B低0.40个百分点，不代表收益差。'
        done = self.execute(text)
        self.assertEqual(done['status'], '已完成', done.get('error'))
        self.assertEqual(done['questions'][0]['answer'], text)
        self.assertTrue(done['questions'][0]['numeric_integrity'])
        self.assertIn(text, render(compose(done), fixture.ROOT))

    def test_structured_comparison_generates_direction_from_the_actual_result(self):
        done = self.execute(segments=[{'text': '同口径年度费用比较：'},
            {'source': 'calculation', 'ref': 'fee_compare', 'path': 'value', 'format': 'comparison', 'digits': 2},
            {'text': '。不代表实际收益差。'}])
        self.assertEqual(done['status'], '已完成', done.get('error'))
        self.assertIn('ETF-A比ETF-B低0.40', done['questions'][0]['answer'])
        self.assertIn('百分点/年', done['questions'][0]['answer'])
        self.assertEqual(compose(done)['questions'][0]['answer'], done['questions'][0]['answer'])
        self.assertEqual(self.fixture.agent.archive.get(done['id']), done)
        self.assertEqual(self.fixture.agent.export(done['id'])['content']['questions'][0]['numeric_integrity'],
                         done['questions'][0]['numeric_integrity'])

    def test_bad_thesis_is_not_saved_even_with_valid_evidence_and_calculation(self):
        text = 'A比B低40个百分点。'
        def theses(host):
            return [{'title': text, 'judgment': '支持', 'question_ids': ['q1'],
                     'support': host['answers']['q1']['refs'], 'counter': [],
                     'calculation_refs': ['fee_compare'], 'investment_meaning': text,
                     'limitation': '仅比较费用，不代表收益。'}]
        done = self.execute(theses=theses)
        self.assertEqual(done['theses'], [])
        self.assertNotIn(text, render(compose(done), fixture.ROOT))

    def test_report_rejects_answer_edit_and_deleted_binding(self):
        done = self.execute()
        self.assertEqual(done['status'], '已完成', done.get('error'))
        for mode in ('text', 'receipt', 'legacy_flag'):
            changed = copy.deepcopy(done)
            if mode in ('text', 'legacy_flag'):
                changed['questions'][0]['answer'] = 'A比B低40个百分点。'
            if mode in ('receipt', 'legacy_flag'):
                changed['questions'][0].pop('numeric_integrity')
            if mode == 'legacy_flag':
                changed.pop('numeric_integrity_contract')
            with self.subTest(mode=mode), self.assertRaises(ContractError):
                compose(changed)
        self.assertEqual(self.fixture.agent.archive.get(done['id']), done)

    def test_quantity_hidden_across_text_segments_is_rejected(self):
        done = self.execute(segments=[{'text': '费用高'}, {'text': '4'}, {'text': '0个百分点。'}])
        self.assertEqual(done['questions'][0]['status'], '未回答')
        self.assertNotEqual(done['status'], '已完成')

    def test_app_and_skill_share_the_bound_numeric_contract(self):
        done = self.execute()
        before = fingerprint(done)
        contract = analysis_contract(done)
        self.assertEqual(contract['numeric_statement']['version'], 1)
        spec = importlib.util.spec_from_file_location('numeric_skill', fixture.ROOT / 'core/session.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        session = module.Session.__new__(module.Session)
        session.archive = self.fixture.agent.archive
        self.assertEqual(session.tools(done['id'])['analysis'], contract)
        self.assertEqual(fingerprint(done), before)

    def nonfixture_host(self, explicit_reference):
        # A normal task with authored materials, rather than the built-in case
        # vocabulary. Keep the synthetic-data flag honest and use its current
        # four-input method; the legacy scalar fee method is not substituted.
        task,host=self.fixture.prepare()
        host['answers']['q1']['answer']='费用差为-0.4个百分点，不代表收益优势。'
        if not explicit_reference:host['answers']['q1'].pop('calculation_refs',None)
        return self.fixture.agent.start(task['id'],host)

    def test_nonfixture_calculation_with_explicit_reference(self):
        done = self.nonfixture_host(True)
        self.assertEqual(done['status'], '已完成', done.get('error'))
        self.assertIsNone(done.get('fixture'))
        self.assertTrue(done['synthetic'])
        self.assertAlmostEqual(done['calculations'][0]['output']['value'], -.4)
        self.assertIn('费用差为-0.4', self.fixture.agent.export(done['id'])['html'])

    def test_registered_calculation_requires_explicit_reference(self):
        done = self.nonfixture_host(False)
        self.assertEqual(done['status'], '部分完成', done.get('error'))
        self.assertEqual(done['questions'][0]['status'], '未回答')
        self.assertNotIn('answer', done['questions'][0])
        self.assertNotIn('calculation_refs', done['questions'][0])
        self.assertAlmostEqual(done['calculations'][0]['output']['value'], -.4)
