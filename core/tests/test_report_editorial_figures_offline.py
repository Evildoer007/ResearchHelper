"""New report-design checks. Historical tests and original reports stay intact."""
from html.parser import HTMLParser
from lxml import html as dom
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
        tree = dom.document_fromstring(report)
        figures = example_figures('semi', self.catalog['semi'])
        tables = Tables(report).tables
        self.assertEqual(tables[:len(figures)], [figure['title'] for figure in figures])
        named = tree.xpath('//table[@aria-label=$title]', title=figures[0]['title'])
        self.assertTrue(named)
        self.assertIn('87+', named[0].xpath('.//td/text()'))
        self.assertIn('全年实际/最新预期（%）', named[0].text_content())
        self.assertTrue(named[0].xpath('.//th[@scope="row"]'))
        self.assertIn('图4 单位GDP的AI算力', tree.text_content())

    def test_missing_values_do_not_become_zero_or_a_revenue_path(self):
        medicine = example_figures('medicine', self.catalog['medicine'])[0]
        self.assertIsNone(medicine['series'][0]['data'][-1])
        tree = dom.document_fromstring(render_example('medicine', ROOT))
        scripts = [node.text for node in tree.xpath('//script') if node.text and node.text.startswith('window.RH_EXAMPLE_FIGURES=')]
        self.assertEqual(len(scripts), 1)
        actual = json.loads(scripts[0].split('=', 1)[1].rstrip(';'))
        self.assertIsNone(actual[0]['series'][0]['data'][-1])
        table = tree.xpath('//table[@aria-label=$title]', title=medicine['title'])[0]
        last = table.xpath('.//tbody/tr')[-1]
        self.assertEqual(last.xpath('./td/text()'), ['未披露'])


if __name__ == '__main__':
    unittest.main()
