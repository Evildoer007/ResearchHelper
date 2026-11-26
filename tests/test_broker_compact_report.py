"""A compact paper keeps exact case data alongside its investment arguments."""
import unittest
from lxml import html
from tests.test_broker_strategy_report import module

class BrokerCompactReportTests(unittest.TestCase):
    def test_all_essential_case_figures_remain_in_visible_prose(self):
        tree=html.document_fromstring(module.render(module.load_case()));main=tree.find('.//main')
        # Chart readouts supply exact numbers without repeating a prose table.
        # The headless reading/print checks cover the actual rendered SVG text.
        text=main.text_content()+' '.join(row.text_content() for row in tree.xpath('//*[@data-inspection-title]'))
        for value in ['1.5万亿元','九成以上','17.13%','30.72%','中信证券69%','华泰证券52%','广发证券41%','69.59%','1.37倍','27%分位','10%至13%','735亿元','32%','33%','4个百分点','1.8%至1.9%','3.8%至4.0%','512000.SH']:
            self.assertIn(value,text)
        self.assertEqual(len(main.xpath('.//section[starts-with(@id,"logic-")]')),3)
        for figure in main.xpath('.//figure'):
            targets=figure.xpath('./figcaption/a/@href')
            self.assertEqual(len(targets),1)
            self.assertEqual(len(tree.xpath('//*[@id=$target]',target=targets[0][1:])),1)

    def test_repeated_summary_figures_and_dividend_table_are_removed(self):
        main=html.document_fromstring(module.render(module.load_case())).find('.//main')
        self.assertFalse(main.xpath('.//table'))
        summary=main.xpath('.//*[@id="summary"]')
        self.assertEqual(len(summary),1)
        text=summary[0].text_content()
        self.assertNotIn('735亿元',text)
        for word in ['卖压','盈利','分红']:self.assertIn(word,text)
        self.assertEqual(len(main.xpath('.//*[@id="cash-chart"]')),1)

    def test_full_underlying_evidence_remains_available(self):
        data=module.load_case();paper=module.render(data)
        self.assertEqual(len(data['sectors']),6);self.assertEqual(len(data['heads']),3)
        self.assertTrue('735亿元' in paper and '33%' in paper and '4个百分点' in paper)
        for item in module.figures(data):self.assertIn('basis-'+item['target'],paper)
        self.assertEqual((data['model_calls'],data['data_requests']),(0,0))

if __name__=='__main__':unittest.main()
