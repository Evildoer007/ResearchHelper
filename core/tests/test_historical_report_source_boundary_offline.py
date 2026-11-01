"""Independent original/public-content boundaries; do not edit old tests."""
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import unittest

from researchhelper.report_examples import render_example, example_figures, reading_appendix

ROOT = Path(__file__).resolve().parents[2]


class PublicText(HTMLParser):
    def __init__(self, markup):
        super().__init__();self.parts=[];self.ignored=0;self.feed(markup)
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'svg'):self.ignored+=1
    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'svg'):self.ignored-=1
    def handle_data(self, text):
        if not self.ignored and text.strip():self.parts.append(text.strip())


class HistoricalSourceBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.catalog=json.loads((ROOT/'products/app/data/report_catalog.json').read_text())['sources']
    def text(self, key):
        return ''.join(PublicText(render_example(key, ROOT)).parts)

    def test_ipo_never_publishes_pixel_reconstructed_return_rows(self):
        reading=render_example('ipo', ROOT)
        self.assertNotIn('原图形态重绘（%）',reading)
        self.assertNotIn('<td>-0.35</td>',reading)
        text=self.text('ipo')
        for value in ('-1.4%', '43.5%', '41.37', '417', '7月21日至23日', '688981', 'T-20至T-2'):
            self.assertIn(value,text)

    def test_consumer_never_publishes_guessed_intermediate_path_cells(self):
        reading=render_example('consumer', ROOT)
        self.assertNotIn('<td>98</td>',reading)
        self.assertNotIn('<td>84</td>',reading)
        self.assertNotIn('<td>107</td>',reading)
        text=self.text('consumer')
        for value in ('28.1', '10.9', '13.7', '2208', '1588', '0.55', '0.61', '0.70'):
            self.assertIn(value,text.replace(',',''))

    def test_broker_keeps_qualitative_bubble_information_without_coordinates(self):
        reading=render_example('broker', ROOT)
        self.assertNotIn('scatter',json.dumps(example_figures('broker',self.catalog['broker'])))
        self.assertNotIn('持仓与减持相对关系</figcaption>',reading)
        text=self.text('broker')
        for label in ('持有规模','减持力度','非银','医药','房地产','食品饮料','银行','减持最重','规模最大'):
            self.assertIn(label,text)

    def test_public_company_names_stay_bound_to_disclosed_growth(self):
        self.assertIn('中信证券69%、华泰证券52%、广发证券41%',self.text('broker'))

    def test_public_fund_name_is_not_redacted_or_mapped_to_another_code(self):
        text=self.text('medicine')
        self.assertIn('华宝恒生港股通创新药ETF份额增加超过15亿份',text)
        self.assertIn('该资金流指标的标的映射未独立核验',text)

    def test_all_catalog_public_narrative_and_tables_survive(self):
        for key,item in self.catalog.items():
            text=self.text(key)
            for logic in item['logics']:
                for paragraph in logic['paragraphs']:
                    self.assertIn(paragraph,text,key)
            for table in item.get('evidence',{}).get('tables',[]):
                for row in table['rows']:
                    for cell in row:self.assertIn(str(cell),text,key)

    def test_archived_html_is_never_rewritten(self):
        paths=[ROOT/'products/app/data'/item['source_html'] for item in self.catalog.values()]
        before=[hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
        for key in self.catalog:render_example(key, ROOT)
        self.assertEqual(before,[hashlib.sha256(path.read_bytes()).hexdigest() for path in paths])

    def test_reading_boundary_is_explicit_and_old_transcript_is_not_called_an_original(self):
        for key in ('ipo','broker','consumer'):
            text=self.text(key)
            self.assertIn('原件未披露的重绘数值不展示',text)
            self.assertNotIn('完整保留现有Demo已整理的研究内容',text)

    def test_unrecognized_grid_does_not_silently_remove_public_content(self):
        for source in ('<div class="chart-grid">其他公开内容</div>',
                       '<div class="chart-grid">超大IPO上市前后全A收益路径</div>'*2,
                       '<div class="chart-grid">超大IPO上市前后全A收益路径'):
            with self.subTest(source=source),self.assertRaises(ValueError):
                reading_appendix('ipo',source)

    def test_adjacent_public_content_and_unaffected_reports_remain_exact(self):
        source='公开正文<div class="chart-grid"><div>超大IPO上市前后全A收益路径</div></div><table>原始数据</table>'
        rendered=reading_appendix('ipo',source)
        self.assertTrue(rendered.startswith('公开正文<section'))
        self.assertTrue(rendered.endswith('<table>原始数据</table>'))
        self.assertEqual(reading_appendix('semi',source),source)


if __name__=='__main__':unittest.main()
