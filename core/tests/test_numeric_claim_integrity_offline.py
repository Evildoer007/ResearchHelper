"""Regression checks for numeric prose, using explicitly synthetic originals.

Wrong values, directions and units must stay out of client text. Correct
controls remain usable; no model, real account or supplier request is used.
"""

import copy
import unittest

from researchhelper.reports import compose, render
from researchhelper.contracts import ContractError
from lxml import html
from tests import test_management_custody_fee_offline as fee_fixture


class NumericClaimIntegrityRegressionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fee_fixture.ManagementCustodyFeeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def run_answer(self, text, expected_status='已完成'):
        task, host = self.fixture.prepare()
        host['answers']['q1']['answer'] = text
        final = self.fixture.agent.start(task['id'], host)
        self.assertEqual(final['status'], expected_status, final.get('error'))
        self.assertAlmostEqual(final['calculations'][0]['output']['value'], -.4)
        self.assertEqual(final['calculations'][0]['output']['unit'], '百分点/年')
        self.assertEqual(final['ledger']['used']['model_calls'], 0)
        self.assertEqual(final['ledger']['used']['data_requests'], 0)
        return final

    def test_wrong_magnitude_direction_unit_and_raw_value_stay_unadopted(self):
        statements = (
            'A的两项年费率合计比B低40个百分点。',
            'A的两项年费率合计比B高0.40个百分点。',
            'A的两项年费率合计比B低0.40元。',
            'A的管理费年费率为15%，两项年费率合计比B低0.40个百分点。',
        )
        for statement in statements:
            with self.subTest(statement=statement):
                final = self.run_answer(statement,'部分完成')
                self.assertNotIn('answer', final['questions'][0])
                body=html.document_fromstring(render(compose(final),fee_fixture.ROOT)).find('.//main').text_content()
                self.assertNotIn(statement,body)

    def test_wrong_magnitude_thesis_is_not_adopted_or_exported(self):
        task, host = self.fixture.prepare()
        statement = 'A的两项年费率合计比B低40个百分点。'
        host['theses'] = [{
            'title': statement, 'judgment': '支持', 'question_ids': ['q1'],
            'support': host['answers']['q1']['refs'], 'counter': [],
            'calculation_refs': ['fee_compare'], 'investment_meaning': statement,
            'limitation': '仅比较合成规则，不代表持有收益差。',
        }]
        final = self.fixture.agent.start(task['id'], host)
        self.assertEqual(final['status'], '已完成', final.get('error'))
        self.assertAlmostEqual(final['calculations'][0]['output']['value'], -.4)
        self.assertEqual(final['theses'],[])
        body=html.document_fromstring(render(compose(final),fee_fixture.ROOT)).find('.//main').text_content()
        self.assertNotIn(statement,body)

    def test_report_boundary_rejects_numeric_prose_tampering(self):
        final = self.run_answer('A的两项年费率合计比B低0.40个百分点。')
        changed = copy.deepcopy(final)
        statement = 'A的两项年费率合计比B低40个百分点。'
        changed['questions'][0]['answer'] = statement
        self.assertEqual(changed['calculations'], final['calculations'])
        self.assertEqual(changed['evidence'], final['evidence'])
        with self.assertRaises(ContractError):compose(changed)
        self.assertEqual(self.fixture.agent.archive.get(final['id']),final)

    def test_control_correct_prose_with_dates_and_identifiers_remains_accepted(self):
        statement = '2026年度规则中ETF-A的两项年费率合计比ETF-B低0.40个百分点。'
        final = self.run_answer(statement)
        self.assertIn(statement, render(compose(final), fee_fixture.ROOT))
