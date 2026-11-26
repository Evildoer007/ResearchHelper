"""Independent source-to-customer-report review of the R74 installed backend."""
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
from researchhelper.draft_sources import READER_POLICY, VERSION, observations
from researchhelper.drafts import compose as compose_draft, normalize_report, source_binding
from researchhelper.report_designer import design
from researchhelper.reports import compose,check_content
from lxml import html
from tests.review_requested_asset_data_matrix import BROKER_LABELS

FOLDER=ROOT/'result/acceptance/one-sentence-research-o6gksb3_'
REPLAY=ROOT/'result/acceptance/broker-period-20261106/rebound-source.json.gz'


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

    def test_retired_report_preserves_the_recorded_amount_and_period_qualification_gap(self):
        for s in self.task['source_records']:verify_source(s,self.task)
        for e in self.task['evidence']:validate_evidence(e,self.task)
        fields = broker_fields(self.task)
        self.assertEqual(set(fields), set(FIELDS['broker_financials'][1]))
        self.assertFalse(any(fields.values()))
        self.assertEqual(self.task['financial_acquisition_route_version'],1)

    def test_frozen_report_and_review_match_the_same_task_not_an_empty_export(self):
        check_content(self.report)
        self.assertEqual(fingerprint(compose(self.task)),fingerprint(self.report))
        self.assertEqual(self.task['gaps'],[])
        self.assertTrue(all(q['status']=='已回答' for q in self.task['questions'] if q['required']))
        for key in ('data_requests','model_calls','seconds'):
            self.assertLessEqual(self.task['ledger']['used'][key],self.task['ledger']['limits'][key])

    def test_customer_html_is_offline_without_folded_body_or_configuration_prompts(self):
        html=(FOLDER/'report.html').read_text();parsed=CustomerReportText();parsed.feed(html)
        self.assertEqual(parsed.external_assets,[]);self.assertEqual(parsed.details,0)
        visible=''.join(parsed.text)
        self.assertNotIn('配置在线研究',visible)
        for q in self.report['questions']:
            for binding in q.get('numeric_integrity',{}).get('fields',{}).get('answer',{}).get('bindings',[]):
                self.assertIn(binding['display'],visible)


@unittest.skipUnless(REPLAY.is_file(), '缺少中信证券留存原件回放')
class CurrentBrokerReportReviewTests(unittest.TestCase):
    def test_both_report_profiles_link_all_seven_exact_original_amounts(self):
        with gzip.open(REPLAY, 'rt', encoding='utf-8') as stream:
            task = json.load(stream)
        task[READER_POLICY] = VERSION
        from researchhelper.planning import input_fingerprint
        task['input_hash'] = input_fingerprint(task)
        record = next(row for row in task['source_records'] if row['kind'] == 'filing')
        facts = observations(task)
        fields = FIELDS['broker_financials'][1]
        by_amount = {row['raw_value']: row for row in facts if row['artifact_id'] == record['id']}
        lines, refs = [], []
        for index, (key, (value, unit)) in enumerate(fields.items()):
            row = by_amount[value]
            self.assertEqual((row['period'], row['raw_unit']), ('2026-01-01至2026-06-30', unit))
            label = BROKER_LABELS[key] + ('收入' if index >= 2 else '')
            lines.append(f'中信证券（600030.SH）2026年上半年{label}为{value}{unit}。')
            refs.append(row['id'])
        proposal = dict(title='中信证券半年报核对', conclusion='核对半年报披露金额。', logics=[
            dict(title=title, analysis=''.join(lines[begin:end]), data_refs=refs[begin:end], source_refs=[record['id']])
            for title, begin, end in [('合并报表', 0, 2), ('业务分部', 2, 7)]
        ])
        draft = normalize_report(proposal, task, facts)
        task['draft_report'] = dict(draft, facts=facts, calculations=[], source_binding=source_binding(task))
        content = compose_draft(task)
        for profile in ('standard', 'detailed'):
            with self.subTest(profile=profile):
                tree = html.document_fromstring(design(content, ROOT, profile=profile)['html'])
                links = tree.xpath('//main//a[@class="quantity-link"]')
                self.assertEqual([link.text_content() for link in links],
                                 [value + unit for value, unit in fields.values()])
                for link in links:
                    target = tree.get_element_by_id(link.get('href')[1:]).text_content()
                    self.assertIn('2026-01-01至2026-06-30', target)
                    self.assertIn('人民币元', target)
