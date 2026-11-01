"""The real completed study has comparable rates, not a synthetic calculation.

Presentation may chart those already adopted numbers. It must not invent an
annualization, combined fee, missing component or approval.
"""
import copy
import hashlib
import json
from pathlib import Path
import os
import unittest
from unittest.mock import patch
from researchhelper import visual

SOURCE = Path(os.environ.get('RESEARCHHELPER_FEE_REPORT_INPUT', str(
    Path(__file__).resolve().parents[1]/'result/acceptance/one-sentence-research-9eq092nf/report.json')))


class DirectFeeFigureTests(unittest.TestCase):
    def setUp(self):
        self.digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
        self.content = json.loads(SOURCE.read_text())
        self.question = self.content['questions'][0]
        guard = patch('socket.socket.connect', side_effect=AssertionError('No new calls'))
        guard.start()
        self.addCleanup(guard.stop)

    def tearDown(self):
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), self.digest)

    def figures(self):
        return visual.annual_fee_observation_figures(self.content, self.question)

    def test_direct_comparison_preserves_original_numbers_units_and_fund_colors(self):
        before = copy.deepcopy(self.content)
        figures = self.figures()
        self.assertEqual(len(figures), 1)
        figure = figures[0]
        self.assertEqual(figure['categories'], ['管理费', '托管费'])
        self.assertEqual([group['name'] for group in figure['groups']], ['510300.SH', '510500.SH'])
        self.assertEqual([row['value'] for row in figure['rows']], [0.15, 0.05, 0.15, 0.05])
        self.assertEqual(figure['unit'], '%')
        self.assertTrue(all(row['ref'] in self.question['refs'] for row in figure['rows']))
        self.assertFalse(self.content['calculations'])
        self.assertEqual(self.content, before)

    def test_missing_component_is_not_filled_or_used_as_a_total(self):
        self.question['refs'] = [ref for ref in self.question['refs'] if ref != 's1_cust_fee_300']
        figure = self.figures()[0]
        self.assertEqual(figure['categories'], ['管理费'])
        self.assertEqual([row['value'] for row in figure['rows']], [0.15, 0.15])
        self.assertTrue(all('合计' not in row['label'] for row in figure['rows']))

    def test_unreviewed_or_uncited_answer_cannot_create_a_customer_chart(self):
        self.question['status'] = '未回答'
        self.assertEqual(self.figures(), [])
        self.question['status'] = '已回答'
        self.question['refs'] = []
        self.assertEqual(self.figures(), [])

    def test_changed_number_or_source_proof_is_not_graphed(self):
        for item in self.content['evidence']:
            if item.get('value') is not None:
                item['value'] = 999
        self.assertEqual(self.figures(), [])

    def test_render_uses_offline_chart_without_modifying_frozen_content(self):
        before = copy.deepcopy(self.content)
        resources = Path(os.environ.get('RESEARCHHELPER_FEE_REPORT_RESOURCES',
                                        str(Path(__file__).resolve().parents[1])))
        page = visual.render(self.content, resources)
        self.assertIn('rh-annual-fee-observations-1', page)
        self.assertIn('510300.SH', page)
        self.assertIn("class='chart chart-grouped'", page)
        self.assertNotIn('<details', page)
        self.assertEqual(self.content, before)
        exported = os.environ.get('RESEARCHHELPER_FEE_REPORT_EXPORT')
        if exported:
            target = Path(exported)
            self.assertEqual(target.parent.resolve(), SOURCE.parent.resolve())
            self.assertFalse(target.exists())
            target.write_text(page)


if __name__ == '__main__':
    unittest.main()
