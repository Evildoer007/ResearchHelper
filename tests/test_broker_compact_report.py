"""A compact paper keeps the case's investment evidence and removes repetitions."""
import unittest
from html.parser import HTMLParser
from tests.test_broker_strategy_report import module

class BodyText(HTMLParser):
    def __init__(self):super().__init__();self.parts=[]
    def handle_data(self,data):self.parts.append(data)

class BrokerCompactReportTests(unittest.TestCase):
    def test_all_essential_case_figures_remain_in_visible_prose(self):
        data=module.load_case();paper=module.render(data);main=paper.split('<main>',1)[1].split('</main>',1)[0]
        parser=BodyText();parser.feed(main);text=''.join(parser.parts)
        for value in ['1.5万亿元','九成以上','17.13%','30.72%','69%、52%、41%','69.59%','1.37倍','27%分位','10%至13%','735亿元','32%','33%','4个百分点','1.8%至1.9%','3.8%至4.0%','512000.SH']:
            self.assertTrue(value in text,value)
        self.assertEqual(main.count('class="strategy"'),3)

    def test_repeated_summary_figures_and_dividend_table_are_removed(self):
        paper=module.render(module.load_case());main=paper.split('<main>',1)[1].split('</main>',1)[0]
        self.assertFalse('<table' in main)
        summary=main.split('<section id="conclusion">',1)[1].split('</section>',1)[0]
        self.assertFalse('735亿元' in summary)
        self.assertTrue('卖压趋缓、盈利改善与分红提升' in summary)
        self.assertTrue('735亿元' in main)

    def test_full_underlying_evidence_remains_available(self):
        data=module.load_case();paper=module.render(data)
        self.assertEqual(len(data['sectors']),6);self.assertEqual(len(data['heads']),3)
        self.assertTrue('735亿元' in paper and '33%' in paper and '4个百分点' in paper)
        for item in module.figures(data):self.assertTrue('basis-'+item['target'] in paper)
        self.assertEqual((data['model_calls'],data['data_requests']),(0,0))

if __name__=='__main__':unittest.main()
