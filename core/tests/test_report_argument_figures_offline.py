"""Figure visibility and argument bindings, independent of previous tests."""
from lxml import html as dom
import json
from pathlib import Path
import unittest

from researchhelper.report_examples import render_example

ROOT = Path(__file__).resolve().parents[2]


class ArgumentFigures:
    def __init__(self, source):
        tree = dom.document_fromstring(source)
        self.bindings = {int(node.get('data-argument')):
            [int(ident.rsplit('-', 1)[1]) for ident in node.xpath('.//*[starts-with(@id,"example-chart-")]/@id')]
            for node in tree.xpath('//main//*[@data-argument]')}
        charts = tree.xpath('//main//*[starts-with(@id,"example-chart-")]')
        if any(len(node.xpath('ancestor::*[@data-argument]')) != 1 for node in charts):
            raise AssertionError('Each historical figure needs one substantive argument')


class ArgumentFigureTests(unittest.TestCase):
    def test_each_figure_belongs_to_its_research_argument(self):
        expected = {'semi': {0: [0, 1], 1: [2, 3], 2: [4]},
                    'consumer': {0: [0], 1: [1]},
                    'internet': {0: [0], 1: [1]},
                    'medicine': {0: [0], 1: []},
                    'broker': {0: [], 1: [0], 2: []},
                    'ipo': {0: [0], 1: [], 2: []}}
        for key, bindings in expected.items():
            with self.subTest(key=key):
                self.assertEqual(ArgumentFigures(render_example(key, ROOT)).bindings, bindings)

    def test_first_ai_figures_precede_later_arguments_without_losing_text(self):
        catalog = json.loads((ROOT/'products/app/data/report_catalog.json').read_text())['sources']
        report = render_example('semi', ROOT)
        self.assertLess(report.index('id="example-chart-0"'), report.index('data-argument="1"'))
        for logic in catalog['semi']['logics']:
            for paragraph in logic['paragraphs']:
                self.assertIn(paragraph, report)


if __name__ == '__main__':
    unittest.main()
