"""Independent contracts for the six-evidence research paper."""
import importlib.util
import json
import re
import tempfile
import unittest
from lxml import html
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FILE=ROOT/'modules/reportcomposer/examples/broker_report.py'
SPEC=importlib.util.spec_from_file_location('broker_six_evidence',FILE)
module=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class BrokerSixEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.data=module.load_case()
        self.specs={row['target']:row for row in module.figures(self.data)}

    def test_all_six_actual_sector_coordinate_pairs_remain(self):
        self.assertEqual([(p['x'],p['y']) for p in self.specs['valuation-chart']['data']['points']],
                         [(-17.13,30.72),(-17.46,20.48),(-22.55,4.87),(-6.32,31.70),(37.98,28.69),(-8.57,-23.30)])

    def test_revenue_and_profit_are_separate_periods_and_measures(self):
        self.assertEqual(self.specs['earnings-chart']['data']['values'],[69,52,41])
        self.assertEqual(self.data['profit']['period'],'2026H1')
        self.assertEqual(self.data['profit']['growth'],69.59)

    def test_percentile_is_approximate_position_not_distribution(self):
        value=self.specs['pb-chart']['data']
        self.assertEqual((value['pb'],value['percentile'],value['percentile_qualifier']),(1.37,27,'约'))
        self.assertEqual(value['roe'],[10,13])
        self.assertNotIn('distribution',value)

    def test_cash_comparison_is_explicit_index_not_inferred_cash_history(self):
        value=self.specs['cash-chart']['data']
        self.assertEqual((value['cash'],value['growth'],value['baseIndex']),(735,32,100))
        self.assertAlmostEqual(value['currentIndex'],value['baseIndex']*(1+value['growth']/100))
        self.assertEqual(value['currentIndex']-value['baseIndex'],value['increment'])
        self.assertEqual(value['unit'],'base_index_2024_100')
        self.assertFalse(any('prior_cash' in key or 'cash_2024' in key for key in value))

    def test_payout_field_encodes_percent_not_investor_or_company_samples(self):
        value=self.specs['payout-chart']['data']
        self.assertEqual((value['value'],value['change'],value['total']),(33,4,100))
        self.assertEqual(value['unit'],'percentage_point_not_company')

    def test_yields_remain_observed_endpoints_without_midpoint(self):
        self.assertEqual(self.specs['dividend-chart']['data']['ranges'],[
            dict(name='A股券商',min=1.8,max=1.9),dict(name='H股券商',min=3.8,max=4.0)])

    def test_each_figure_has_distinct_evidence_and_bound_details(self):
        self.assertEqual(len(self.specs),6)
        self.assertEqual(len({row['scheme'] for row in self.specs.values()}),6)
        paper=module.render(self.data)
        for target in self.specs:
            self.assertIn('id="'+target+'"',paper)
            self.assertIn('id="basis-'+target+'"',paper)
        main=html.document_fromstring(paper).find('.//main')
        self.assertEqual(len(main.xpath('.//section[starts-with(@id,"logic-")]')),3)
        for token in ['非银','医药','食品饮料','地产','银行','九成以上','1.5万亿元','69.59%','512000.SH']:
            self.assertIn(token,main.text_content())
        for token in ['figure-note','sector-context','valuation-ledger']:
            self.assertFalse(main.xpath('.//*[contains(concat(" ",normalize-space(@class)," "),$token)]',token=' '+token+' '))
        self.assertFalse(main.xpath('.//details|.//summary'))

    def test_private_identifiers_external_resources_and_products_are_absent(self):
        paper=module.render(self.data)
        for token in ['光大','金融创新业务总部','参考报价','收益互换','期权费','敲入','敲出','/Users/']:
            self.assertNotIn(token,paper)
        self.assertFalse(re.search(r'<(?:script|link)[^>]+(?:src|href)=["\']https?://',paper))

    def test_source_is_immutable_and_build_does_not_call_services(self):
        before=module.digest(module.SOURCE)
        with tempfile.TemporaryDirectory() as folder:
            file=module.build(folder)
            manifest=json.loads(file.with_name('manifest.json').read_text())
            self.assertEqual(manifest['figures'],6)
            self.assertEqual((manifest['model_calls'],manifest['data_requests']),(0,0))
            self.assertEqual(manifest['source_sha256'],before)
        self.assertEqual(module.digest(module.SOURCE),before)


if __name__=='__main__':
    unittest.main()
