"""Independent review of the real ordinary-Chat operating report.

The UI/backend are current source, not the installed native App. This certifies
the requested facts and saved report, not investment logic or all asset types.
"""
import hashlib
import gzip
from html.parser import HTMLParser
import json
import unittest

from tests import ROOT
from tests.test_asset_data_acceptance_review import verify_source
from researchhelper.contracts import fingerprint,validate_evidence
from researchhelper.reports import check_content,compose


FOLDER=ROOT/'result/acceptance/one-sentence-research-vnmk1a5e'


def retained_json(name):
    path=FOLDER/name
    if path.is_file():return json.loads(path.read_text())
    with gzip.open(FOLDER/(name+'.gz'),'rt',encoding='utf-8') as incoming:
        return json.load(incoming)


class CustomerReportText(HTMLParser):
    def __init__(self):
        super().__init__();self.ignored=0;self.text=[];self.external_assets=[];self.details=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style'):self.ignored+=1
        attrs=dict(attrs)
        url=attrs.get('src') if tag in ('script','img','iframe') else attrs.get('href') if tag=='link' else None
        if isinstance(url,str) and url.startswith(('https://','http://','//')):self.external_assets.append(url)
        if tag=='details':self.details+=1
    def handle_endtag(self,tag):
        if tag in ('script','style'):self.ignored-=1
    def handle_data(self,data):
        if not self.ignored:self.text.append(data)


class CurrentOperatingReportReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.receipt=retained_json('receipt.json')
        cls.task=retained_json('task.json')
        cls.report=retained_json('report.json')
        cls.hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in FOLDER.iterdir()
                    if p.is_file() and p.suffix in ('.json','.gz')}

    @classmethod
    def tearDownClass(cls):
        for name,value in cls.hashes.items():
            assert hashlib.sha256((FOLDER/name).read_bytes()).hexdigest()==value

    def test_one_real_ordinary_chat_completed_without_supplied_artifacts_or_production_changes(self):
        r=self.receipt
        self.assertTrue(r['ordinary_chat_report_completed'])
        self.assertEqual(r['research_status'],'已完成')
        self.assertTrue(all(r[k] for k in ('service_stopped','browser_stopped','production_unchanged','source_unchanged_during_study')))
        self.assertFalse(any(r[k] for k in ('supplied_plan','supplied_evidence','supplied_answers','native_window_opened')))
        self.assertFalse(r['formal_app_launcher_used'] or r['installed'])
        self.assertEqual(r['browser']['creationRequests'],1)
        self.assertEqual(r['browser']['startRequests'],1)

    def test_raw_sources_values_and_complete_reporting_period_are_bound(self):
        for source in self.task['source_records']:verify_source(source,self.task)
        numeric={e['id']:(e['value'],e['unit'],e['period']) for e in self.task['evidence'] if 'value' in e}
        self.assertEqual(numeric,{
            'sel_capacity':(525,'GWh','2026-01-01/2026-06-30'),
            'sel_construction_capacity':(764,'GWh','2026-01-01/2026-06-30'),
            'sel_output':(498,'GWh','2026-01-01/2026-06-30'),
            'sel_capacity_utilization':(94.86,'%','2026-01-01/2026-06-30')})
        for e in self.task['evidence']:validate_evidence(e,self.task)

    def test_report_is_frozen_from_the_same_completed_task_not_an_empty_export(self):
        check_content(self.report)
        self.assertEqual(self.report['task_id'],self.task['id'])
        self.assertEqual(fingerprint(compose(self.task)),fingerprint(self.report))
        self.assertEqual(self.task['gaps'],[])
        self.assertTrue(all(q['status']=='已回答' for q in self.task['questions']))
        self.assertTrue(self.report['questions'][0]['answer'])

    def test_answer_distinguishes_capacity_and_construction_without_new_arithmetic(self):
        question=self.report['questions'][0];answer=question['answer']
        for text in ('产能525GWh','在建产能764GWh','产量498GWh','产能利用率94.86%','不能相加或混同'):
            self.assertIn(text,answer)
        self.assertEqual(question['calculation_refs'],[])
        bindings=question['numeric_integrity']['fields']['answer']['bindings']
        self.assertEqual({b['display'] for b in bindings if b['path']=='value'},
                         {'525GWh','764GWh','498GWh','94.86%'})
        self.assertEqual(self.report['calculations'],[])

    def test_customer_html_is_complete_offline_and_without_folded_report_body(self):
        html=(FOLDER/'report.html').read_text();parsed=CustomerReportText();parsed.feed(html)
        visible=''.join(parsed.text)
        for expected in ('525GWh','764GWh','498GWh','94.86%'):self.assertIn(expected,visible)
        self.assertEqual(parsed.external_assets,[])
        self.assertEqual(parsed.details,0)
        self.assertNotIn('配置在线研究',visible)
        self.assertNotIn('衍生品推荐',self.report['questions'][0]['answer'])


if __name__=='__main__':
    unittest.main()
