"""Actual frozen ETF finding, not a newly generated investment study."""
import copy
import hashlib
import json
from pathlib import Path
import unittest

from bs4 import BeautifulSoup
from researchhelper.reports import render
from researchhelper.visual import thesis_reading_calculations

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'result/acceptance/one-sentence-research-fclwbfts/report.json'


class StrategyReadingStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = SOURCE.read_bytes()
        cls.content = json.loads(cls.raw)

    def paper(self, content=None):
        return BeautifulSoup(render(self.content if content is None else content, ROOT), 'html.parser')

    def test_core_conclusion_is_the_complete_reviewed_main_answer(self):
        paper = self.paper()
        core = paper.select_one('main .core-conclusion')
        self.assertIsNotNone(core)
        self.assertEqual(core.h2.get_text(), '核心结论')
        answer = ''.join(part.get_text() for part in core.select('[data-core-answer-part]'))
        norm = lambda value: ''.join(value.split())
        self.assertEqual(norm(answer), norm(self.content['questions'][0]['answer']))

    def test_main_answer_is_not_repeated_inside_strategy_logic(self):
        paper = self.paper()
        answer = self.content['questions'][0]['answer']
        self.assertEqual(paper.select_one('main').get_text().count(answer), 1)
        self.assertFalse(paper.select('.research-argument [data-main-question-ref]'))

    def test_supported_opposition_is_a_strategy_logic_not_a_buy_recommendation(self):
        paper = self.paper()
        headings = [heading.get_text() for heading in paper.select('main h2')]
        self.assertEqual(headings[0], '核心结论')
        self.assertEqual(headings[1], '策略逻辑一：' + self.content['theses'][0]['title'])
        self.assertIn('反对', paper.select_one('.argument-judgment').get_text())
        self.assertFalse(paper.select('main nav,main details,main aside'))

    def test_actual_calculation_figure_remains_with_its_only_thesis(self):
        paper = self.paper()
        figures = paper.select('main [data-calculation-ref]')
        self.assertEqual(len(figures), 1)
        self.assertEqual(figures[0].find_parent('article')['data-thesis-ref'], self.content['theses'][0]['id'])
        self.assertEqual(figures[0]['data-calculation-ref'], self.content['calculations'][0]['id'])
        self.assertFalse(paper.select('.core-conclusion .chart'))

    def test_factual_report_does_not_invent_strategy_sections(self):
        path = ROOT/'result/acceptance/one-sentence-research-8jqdx045/report.json'
        content = json.loads(path.read_text())
        self.assertFalse(content['theses'])
        paper = self.paper(content)
        self.assertFalse(paper.select('.core-conclusion,[data-thesis-ref]'))
        self.assertNotIn('策略逻辑一', paper.select_one('main').get_text())

    def test_rendering_preserves_frozen_data_and_original_file(self):
        before = copy.deepcopy(self.content)
        self.paper()
        self.assertEqual(self.content, before)
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), hashlib.sha256(self.raw).hexdigest())


class ThesisFigureOwnershipTests(unittest.TestCase):
    """Identifier-only software routing, never research or evidence approval."""
    def setUp(self):
        self.content = dict(questions=[
            dict(id='main', status='已回答', calculation_refs=['margin', 'cash']),
            dict(id='sales', status='已回答', calculation_refs=['growth'])],
            theses=[dict(id='profit', question_ids=['main', 'sales'], calculation_refs=['margin']),
                    dict(id='liquidity', question_ids=['main'], calculation_refs=['cash'])])

    def test_shared_main_does_not_give_all_figures_to_the_first_thesis(self):
        self.assertEqual(thesis_reading_calculations(self.content),
                         {'profit': ['margin', 'growth'], 'liquidity': ['cash']})

    def test_reordering_theses_does_not_change_figure_ownership(self):
        before = thesis_reading_calculations(self.content)
        self.content['theses'].reverse()
        self.assertEqual(thesis_reading_calculations(self.content), before)

    def test_one_owner_keeps_its_question_figures_without_mutating_records(self):
        self.content['theses'] = self.content['theses'][:1]
        before = copy.deepcopy(self.content)
        self.assertEqual(thesis_reading_calculations(self.content)['profit'], ['margin', 'cash', 'growth'])
        self.assertEqual(self.content, before)

    def test_shared_dependency_has_no_invented_owner(self):
        self.content['questions'][0]['dependencies'] = ['sales']
        self.assertEqual(thesis_reading_calculations(self.content),
                         {'profit': ['margin'], 'liquidity': ['cash']})


if __name__ == '__main__':
    unittest.main()
