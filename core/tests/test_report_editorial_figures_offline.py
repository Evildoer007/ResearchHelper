"""New report-design checks. Historical tests and original reports stay intact."""
from html.parser import HTMLParser
import json
from pathlib import Path
import unittest

from researchhelper.report_examples import example_figures, render_example

ROOT = Path(__file__).resolve().parents[2]


class Tables(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.tables = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'table' and attrs.get('aria-label'):
            self.tables.append(attrs['aria-label'])


class EditorialFigureTests(unittest.TestCase):
    def setUp(self):
        self.catalog = json.loads((ROOT/'products/app/data/report_catalog.json').read_text())['sources']

    def test_chart_form_follows_observation_type_without_new_values(self):
        figures = example_figures('semi', self.catalog['semi'])
        self.assertEqual(figures[1]['presentation'], 'endpoints')
        self.assertEqual(figures[1]['series'][0]['data'], [30, 9])
        self.assertEqual(figures[2]['orientation'], 'vertical')
        self.assertEqual(figures[2]['series'][0]['data'], [439, 28, 33, 19])
        self.assertEqual(figures[3]['series'][0]['data'], [280, 54, 22, 17])
        for key in ('internet', 'medicine'):
            self.assertEqual(example_figures(key, self.catalog[key])[0]['orientation'], 'vertical')

    def test_disclosed_lower_bound_and_units_survive_accessible_table(self):
        report = render_example('semi', ROOT)
        self.assertIn('>87+</td>', report)
        self.assertIn('全年实际/最新预期（%）', report)
        self.assertIn('scope="row"', report)
        self.assertIn('图4 单位GDP的AI算力', report)
        self.assertEqual(Tables(report).tables[:4], [figure['title'] for figure in
            example_figures('semi', self.catalog['semi'])])

    def test_missing_values_do_not_become_zero_or_a_revenue_path(self):
        medicine = example_figures('medicine', self.catalog['medicine'])[0]
        self.assertIsNone(medicine['series'][0]['data'][-1])
        script = (ROOT/'products/app/frontend/report-examples.js').read_text()
        self.assertIn("value===null?'-':value", script)
        self.assertNotIn('max:120', script)
        self.assertNotIn("type:'line'", script.split('function exampleOption', 1)[1])


if __name__ == '__main__':
    unittest.main()
