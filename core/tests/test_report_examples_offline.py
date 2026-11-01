"""Independent tests for disclosed-data one-page historical reading editions."""
import copy
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import unittest

from researchhelper.report_examples import example_figures, render_example

ROOT = Path(__file__).resolve().parents[2]


class Words(HTMLParser):
    def __init__(self, source):
        super().__init__();self.words=[];self.feed(source)
    def handle_data(self, text):
        if text.strip():self.words.append(text)


class ReportExampleTests(unittest.TestCase):
    def setUp(self):
        self.catalog=json.loads((ROOT/'products/app/data/report_catalog.json').read_text())['sources']

    def test_all_six_readers_have_main_figures_without_directory(self):
        for key in self.catalog:
            with self.subTest(key=key):
                reading=render_example(key,ROOT)
                self.assertIn('class="example-chart"',reading)
                self.assertIn('echarts.init',reading)
                self.assertIn('历史案例，非实时研究',reading)
                self.assertNotIn('<aside',reading)
                self.assertNotIn('<nav',reading)
                self.assertNotIn('<button',reading)
                self.assertIn('查看完整研究依据',reading)

    def test_complete_source_body_text_and_catalog_are_preserved(self):
        before=copy.deepcopy(self.catalog)
        for key,item in self.catalog.items():
            source=(ROOT/'products/app/data'/item['source_html']).read_text()
            body=source.split('</main>',1)[0].split('aria-label="完整历史报告">',1)[1]
            reading=render_example(key,ROOT)
            for text in Words(body).words:
                self.assertIn(text,Words(reading).words)
            self.assertIn(item['conclusion'],''.join(Words(reading).words))
        self.assertEqual(before,self.catalog)

    def test_consumer_uses_disclosed_returns_not_reconstructed_price_path(self):
        figures=example_figures('consumer',self.catalog['consumer'])
        self.assertEqual(figures[0]['series'][0]['data'],[-28.1,10.9,13.7])
        self.assertNotIn('line',[figure.get('type') for figure in figures])

    def test_ipo_has_event_nodes_without_fictitious_return_coordinates(self):
        figure=example_figures('ipo',self.catalog['ipo'])[0]
        self.assertEqual(figure['kind'],'timeline')
        self.assertNotIn('series',figure)
        self.assertEqual(figure['nodes'][-1]['name'],'预计上市')

    def test_broker_excludes_arbitrary_bubble_coordinates(self):
        figures=example_figures('broker',self.catalog['broker'])
        self.assertEqual(len(figures),1)
        self.assertEqual(figures[0]['series'][0]['data'][0],-17.13)
        self.assertNotIn('bubble',[figure.get('type') for figure in figures])

    def test_missing_values_and_lower_bound_annotation_remain_explicit(self):
        self.assertIsNone(example_figures('medicine',self.catalog['medicine'])[0]['series'][0]['data'][-1])
        self.assertEqual(example_figures('semi',self.catalog['semi'])[0]['series'][1]['labels'][-1],'87+')

    def test_original_reports_are_never_written(self):
        paths=[ROOT/'products/app/data'/item['source_html'] for item in self.catalog.values()]
        before={path:hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        for key in self.catalog:render_example(key,ROOT)
        self.assertEqual(before,{path:hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})

    def test_unknown_example_is_rejected(self):
        with self.assertRaises(ValueError):render_example('../secret',ROOT)


if __name__=='__main__':unittest.main()
