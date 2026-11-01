"""Independent source-to-customer-report review of the R74 installed backend."""
from decimal import Decimal
import gzip
import hashlib
import json
import unittest
from tests import ROOT
from tests.test_asset_data_acceptance_review import verify_source
from tests.test_current_operating_report_review import CustomerReportText
from tests.review_requested_asset_data_matrix import broker_fields
from tests.test_current_requested_data_review import FIELDS
from researchhelper.contracts import fingerprint,validate_evidence
from researchhelper.reports import compose,check_content

FOLDER=ROOT/'result/acceptance/one-sentence-research-o6gksb3_'


def retained(name):
    path=FOLDER/name
    if path.is_file():return json.loads(path.read_text())
    with gzip.open(FOLDER/(name+'.gz'),'rt',encoding='utf-8') as stream:return json.load(stream)


class InstalledBrokerReportReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.receipt=retained('receipt.json');cls.task=retained('task.json');cls.report=retained('report.json')
        cls.hashes={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in FOLDER.iterdir()
                    if p.is_file() and p.suffix in ('.json','.gz','.html')}

    @classmethod
    def tearDownClass(cls):
        assert all(hashlib.sha256(p.read_bytes()).hexdigest()==value for p,value in cls.hashes.items())

    def test_ordinary_installed_backend_completed_with_no_source_fallback_or_production_writes(self):
        r=self.receipt
        self.assertEqual(r['backend_mode'],'installed_app_backend')
        self.assertTrue(r['installed_bundle_verified'] and r['bundled_backend_isolated'])
        self.assertFalse(r['source_backend_used'] or r['formal_app_launcher_used'] or r['native_window_opened'])
        self.assertTrue(r['ordinary_chat_report_completed'])
        self.assertEqual(r['research_status'],'已完成')
        self.assertTrue(r['service_stopped'] and r['browser_stopped'] and r['production_unchanged'])
        self.assertFalse(r['supplied_plan'] or r['supplied_evidence'] or r['supplied_answers'])
        self.assertEqual(r['browser']['creationRequests'],1)
        self.assertEqual(r['browser']['startRequests'],1)

    def test_every_requested_amount_is_bound_to_the_correct_row_unit_and_complete_period(self):
        for s in self.task['source_records']:verify_source(s,self.task)
        for e in self.task['evidence']:validate_evidence(e,self.task)
        self.assertTrue(all(broker_fields(self.task).values()))
        self.assertEqual(len(broker_fields(self.task)),7)
        self.assertEqual(self.task['financial_acquisition_route_version'],1)

    def test_frozen_report_and_review_match_the_same_task_not_an_empty_export(self):
        check_content(self.report)
        self.assertEqual(fingerprint(compose(self.task)),fingerprint(self.report))
        self.assertEqual(self.task['gaps'],[])
        self.assertTrue(all(q['status']=='已回答' for q in self.task['questions'] if q['required']))
        for key in ('data_requests','model_calls','seconds'):
            self.assertLessEqual(self.task['ledger']['used'][key],self.task['ledger']['limits'][key])

    def test_answer_quantity_bindings_cover_all_seven_amounts(self):
        evidence={e['id']:e for e in self.task['evidence']}
        amounts=set()
        for q in self.report['questions']:
            for binding in q.get('numeric_integrity',{}).get('fields',{}).get('answer',{}).get('bindings',[]):
                if binding.get('path')=='value' and binding.get('ref') in evidence:
                    amounts.add(Decimal(str(evidence[binding['ref']]['value'])))
        self.assertTrue({Decimal(value) for value,unit in FIELDS['broker_financials'][1].values()}.issubset(amounts))
        answer=''.join(q.get('answer') or '' for q in self.report['questions'])
        self.assertIn('证券投资业务',answer)
        self.assertIn('营业收入',answer)

    def test_customer_html_is_offline_without_folded_body_or_configuration_prompts(self):
        html=(FOLDER/'report.html').read_text();parsed=CustomerReportText();parsed.feed(html)
        self.assertEqual(parsed.external_assets,[]);self.assertEqual(parsed.details,0)
        visible=''.join(parsed.text)
        self.assertNotIn('配置在线研究',visible)
        for q in self.report['questions']:
            for binding in q.get('numeric_integrity',{}).get('fields',{}).get('answer',{}).get('bindings',[]):
                self.assertIn(binding['display'],visible)
