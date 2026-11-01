"""Independent retained-data report checks without any network or model service."""
import hashlib,importlib.util,json,re,socket,tempfile,unittest
from decimal import Decimal
from pathlib import Path
from tests import ROOT

FILE=ROOT/'modules/reportcomposer/examples/cpi_report.py'
spec=importlib.util.spec_from_file_location('retained_cpi_paper',FILE)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class RetainedCPIReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hash=hashlib.sha256(module.SOURCE.read_bytes()).hexdigest();cls.data=module.load_data()

    @classmethod
    def tearDownClass(cls):
        assert hashlib.sha256(module.SOURCE.read_bytes()).hexdigest()==cls.hash

    def test_all_twenty_seven_observations_remain_owned_and_exact(self):
        source=json.loads(module.SOURCE.read_text())
        self.assertEqual(self.data['facts'],source['draft']['facts'])
        self.assertEqual(len(self.data['facts']),27)
        self.assertEqual({row['unit'] for row in self.data['facts']},{'%'})
        self.assertEqual(self.data['dates'][0],'2026-01-31');self.assertEqual(self.data['dates'][-1],'2026-09-30')

    def test_six_actual_calculations_and_their_inputs_are_unchanged(self):
        source=json.loads(module.SOURCE.read_text())
        self.assertEqual(self.data['calculations'],source['draft']['calculations'])
        self.assertEqual([row['value'] for row in self.data['calculations']],[.7,.1,-1.,-.3,.7,.2])
        facts={row['id']:row for row in self.data['facts']}
        for row in self.data['calculations']:
            a,b=[facts[key] for key in row['data_refs']]
            self.assertEqual({k:Decimal(str(v)) for k,v in row['inputs'].items()},{'a':Decimal(str(a['value'])),'b':Decimal(str(b['value']))})

    def test_four_graphs_use_real_series_and_registered_differences(self):
        figures=module.chart_specs(self.data)
        self.assertEqual([row['scheme'] for row in figures],['multi_line','diverging_bar','heatmap','dumbbell'])
        self.assertEqual(figures[0]['data']['series'][1]['values'][-1],-1.7)
        self.assertEqual(figures[1]['data']['values'],[.1,-.3,.2])
        self.assertEqual(figures[2]['data']['matrix'],self.data['matrix'])
        self.assertEqual(figures[3]['data']['pairs'],[[.2,.9],[-.7,-1.7],[.3,1.]])

    def test_food_negative_run_is_observed_april_to_september(self):
        self.assertEqual(self.data['matrix'][1][3:],[-1.6,-1.7,-1.6,-1.5,-1.4,-1.7])
        self.assertTrue(all(value>0 for row in (self.data['matrix'][0],self.data['matrix'][2]) for value in row))

    def test_report_has_full_structure_and_no_accordion_or_synthetic_scenarios(self):
        text=module.render(self.data)
        self.assertEqual(text.count('data-observation='),27)
        self.assertEqual(text.count('class="story-block"'),3)
        self.assertFalse('<details' in text);self.assertFalse('<summary' in text)
        for title in ('一、核心结论','二、策略逻辑','三、关键数据','四、跟踪与风险'):
            self.assertTrue(title in text,title)
        for key in ('trend-chart','marginal-chart','matrix-chart','endpoint-chart'):
            self.assertTrue('id="'+key+'"' in text,key)
        self.assertTrue('<math xmlns=' in text,'<math xmlns=')

    def test_report_is_self_contained_and_one_palette_is_used(self):
        text=module.render(self.data)
        self.assertIsNone(re.search(r'<(?:script|link)[^>]+(?:src|href)=["\']https?://',text))
        self.assertTrue('"palette":"teal"' in text,'"palette":"teal"')
        self.assertFalse('/Users/' in text,'Private filesystem path in report');self.assertFalse(bool(re.search(r'\bsk-[A-Za-z0-9]{20,}',text)),'Credential-shaped string in report')
        self.assertTrue('--SERIES_BENCH:#0B6569' in text,'--SERIES_BENCH:#0B6569')

    def test_source_review_limits_are_kept_in_the_new_narrative(self):
        text=module.render(self.data)
        self.assertTrue('不能把食品认定为总体变化的主要贡献项' in text,'不能把食品认定为总体变化的主要贡献项')
        self.assertTrue('不能通过两条同比的简单相减得到服务项读数' in text,'不能通过两条同比的简单相减得到服务项读数')
        self.assertTrue('不能推算分项贡献、企业利润弹性或降息概率' in text,'不能推算分项贡献、企业利润弹性或降息概率')

    def test_build_records_zero_calls_and_preserves_the_original(self):
        original=socket.socket.connect
        try:
            with tempfile.TemporaryDirectory() as folder:
                target=module.build(Path(folder));manifest=json.loads(target.with_name('manifest.json').read_text())
                self.assertEqual((manifest['observations'],manifest['figures'],manifest['registered_calculations']),(27,4,6))
                self.assertEqual(manifest['real_model_calls'],0);self.assertEqual(manifest['real_data_requests'],0)
                self.assertEqual(hashlib.sha256(module.SOURCE.read_bytes()).hexdigest(),self.hash)
        finally:socket.socket.connect=original

if __name__=='__main__':unittest.main()
