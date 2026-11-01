"""Figure visibility and argument bindings, independent of previous tests."""
from html.parser import HTMLParser
import json
from pathlib import Path
import unittest

from researchhelper.report_examples import render_example

ROOT = Path(__file__).resolve().parents[2]


class ArgumentFigures(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.argument = None
        self.bindings = {}
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'section' and 'data-argument' in attrs:
            self.argument = int(attrs['data-argument'])
            self.bindings[self.argument] = []
        if attrs.get('id', '').startswith('example-chart-'):
            self.bindings[self.argument].append(int(attrs['id'].rsplit('-', 1)[1]))

    def handle_endtag(self, tag):
        if tag == 'section':
            self.argument = None


class ArgumentFigureTests(unittest.TestCase):
    def test_each_figure_belongs_to_its_research_argument(self):
        expected = {'semi': {0: [0, 1], 1: [2, 3], 2: []},
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
