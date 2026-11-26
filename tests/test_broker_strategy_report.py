"""The rebuilt paper follows three investment theses with exact case numbers."""
import hashlib,importlib.util,json,re,tempfile,unittest
from pathlib import Path
from lxml import html
from tests import ROOT

FILE=ROOT/'modules/reportcomposer/examples/broker_report.py'
spec=importlib.util.spec_from_file_location('broker_strategy_case',FILE);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class BrokerStrategyReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before=module.digest(module.SOURCE);cls.data=module.load_case()
    @classmethod
    def tearDownClass(cls):assert module.digest(module.SOURCE)==cls.before

    def test_pdf_numbers_for_all_six_sectors_are_retained(self):
        self.assertEqual([r['price'] for r in self.data['sectors']],[-17.13,-17.46,-22.55,-6.32,37.98,-8.57])
        self.assertEqual([r['revenue'] for r in self.data['sectors']],[30.72,20.48,4.87,31.7,28.69,-23.3])
        self.assertTrue(all(r['quote'] for r in self.data['sectors']))

    def test_profit_periods_and_valuation_context_are_separate(self):
        self.assertEqual([r['revenue'] for r in self.data['heads']],[69,52,41])
        self.assertEqual(self.data['profit']['period'],'2026H1');self.assertEqual(self.data['profit']['growth'],69.59)
        self.assertEqual(self.data['valuation'],dict(pb=1.37,percentile=27,percentile_qualifier='约',window='2016年以来',roe=[10,13]))

    def test_inexact_selling_estimate_is_never_turned_into_precise_completion(self):
        self.assertEqual(self.data['reduction'],dict(trillion=1.5,completion='九成以上',status='原材料估计'))
        figures=module.figures(self.data)
        self.assertTrue(all(not {'completion','completion_percent','completed_pct'}.intersection(item['data']) for item in figures))
        comparison=next(item for item in figures if item['target']=='valuation-chart')
        self.assertEqual(comparison['scheme'],'scatter')
        self.assertEqual([(row['x'],row['y']) for row in comparison['data']['points']],[(row['price'],row['revenue']) for row in self.data['sectors']])

    def test_dividend_ranges_have_no_invented_midpoint_or_prior_year_data(self):
        item=next(item for item in module.figures(self.data) if item['target']=='dividend-chart')
        self.assertEqual(item['scheme'],'range')
        self.assertEqual(item['data']['ranges'],[dict(name='A股券商',min=1.8,max=1.9),dict(name='H股券商',min=3.8,max=4.0)])
        self.assertEqual(self.data['dividends']['cash'],735);self.assertEqual(self.data['dividends']['cash_growth'],32)
        self.assertEqual(self.data['dividends']['payout'],33);self.assertEqual(self.data['dividends']['payout_change_pp'],4)

    def test_the_paper_is_core_conclusion_and_three_named_investment_logics(self):
        text=module.render(self.data);main=html.document_fromstring(text).find('.//main');body=main.text_content()
        self.assertEqual(len(main.xpath('.//section[starts-with(@id,"logic-")]')),3)
        for title in ['核心结论','策略逻辑一：卖压衰减','策略逻辑二：盈利改善','策略逻辑三：分红提升']:
            self.assertTrue(title in body,title)
        self.assertNotIn('三、关键数据',body);self.assertNotIn('四、跟踪与风险',body)
        self.assertTrue('512000.SH' in body)

    def test_sensitive_institution_and_derivative_content_is_absent(self):
        text=module.render(self.data)
        for token in ['光大','金融创新业务总部','参考报价','收益互换','期权费','敲入','敲出','Risky','/Users/']:
            self.assertFalse(token in text,token)
        self.assertFalse(bool(re.search(r'\bsk-[A-Za-z0-9]{20,}',text)))

    def test_no_accordion_or_external_script_is_introduced(self):
        text=module.render(self.data)
        self.assertFalse('<details' in text);self.assertFalse('<summary' in text)
        self.assertFalse(bool(re.search(r'<(?:script|link)[^>]+(?:src|href)=["\']https?://',text)))
        figures=module.figures(self.data)
        self.assertEqual({item['scheme'] for item in figures},{'scatter','ranked_bar','percentile','cash_index','payout_units','range'})
        self.assertEqual(len({item['target'] for item in figures}),len(figures))

    def test_original_is_preserved_and_no_network_is_needed(self):
        with tempfile.TemporaryDirectory() as folder:
            file=module.build(folder);receipt=json.loads(file.with_name('manifest.json').read_text())
            self.assertTrue(receipt['source_unchanged']);self.assertEqual((receipt['model_calls'],receipt['data_requests']),(0,0))
            self.assertEqual(receipt['strategy_logics'],3);self.assertEqual(module.digest(module.SOURCE),self.before)

if __name__=='__main__':unittest.main()
