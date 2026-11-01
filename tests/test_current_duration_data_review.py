"""Independent R75 amounts/units/periods review; keep its source gap visible."""
import hashlib
import gzip
import json
import unittest
from tests import ROOT
from tests.test_asset_data_acceptance_review import load_retained_task,verify_source
from tests.review_requested_asset_data_matrix import broker_fields
from researchhelper.contracts import validate_evidence
from researchhelper.evidence import validate_reporting_period_basis

FOLDER=ROOT/'result/acceptance/autonomous-data-phase-bellujck'

def retained_task():
    path=FOLDER/'task.json.gz'
    if not (FOLDER/'task.json').exists() and path.is_file():
        with gzip.open(path,'rt',encoding='utf-8') as incoming:return json.load(incoming)
    return load_retained_task(FOLDER)


class CurrentDurationDataReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.receipt=json.loads((FOLDER/'receipt.json').read_text());cls.task=retained_task()
        cls.receipt_hash=hashlib.sha256((FOLDER/'receipt.json').read_bytes()).hexdigest()

    @classmethod
    def tearDownClass(cls):
        assert hashlib.sha256((FOLDER/'receipt.json').read_bytes()).hexdigest()==cls.receipt_hash

    def test_all_seven_requested_amounts_have_correct_literal_units_and_full_period(self):
        fields=broker_fields(self.task)
        self.assertEqual(len(fields),7)
        self.assertTrue(all(fields.values()),fields)
        sources={s['id']:s for s in self.task['source_records']}
        for source in sources.values():verify_source(source,self.task)
        for e in self.task['evidence']:
            validate_evidence(e,self.task)
            validate_reporting_period_basis(e,sources[e['artifact_id']])
            if 'value' in e:
                self.assertEqual(e['period'],'2026-01-01/2026-06-30')
                self.assertEqual(e['reporting_period_basis']['version'],3)

    def test_fresh_question_only_run_keeps_all_scope_and_production_boundaries(self):
        r=self.receipt
        self.assertTrue(r['owned_runtime_closed'] and r['production_unchanged'] and r['source_unchanged'])
        self.assertFalse(r['supplied_codes'] or r['supplied_plan'] or r['supplied_evidence'] or r['supplied_answers'])
        self.assertFalse(r['report_accepted'] or r['installed_app_accepted'])
        self.assertEqual(self.task['original_duration_anchor_version'],1)
        self.assertFalse(any(e.get('research_phase')=='analyze' for e in self.task['ledger']['dispatches']))

    def test_declared_extra_catalogue_obligation_remains_a_real_unresolved_gap(self):
        self.assertEqual(self.receipt['status'],'data_phase_partial')
        self.assertTrue(self.task['gaps'])
        self.assertEqual(sum(s['operation']=='filing_original' for s in self.task['source_records']),1)
        self.assertEqual(sum(s['operation']=='report_query' for s in self.task['source_records']),2)


def save_review():
    task=retained_task();receipt=json.loads((FOLDER/'receipt.json').read_text())
    target=FOLDER/'independent-review.json'
    if target.exists():raise ValueError('不覆盖独立字段复核')
    fields=broker_fields(task)
    record=dict(scope='required_financial_field_review_not_whole_task_approval',model_calls=0,provider_calls=0,
        receipt_sha256=hashlib.sha256((FOLDER/'receipt.json').read_bytes()).hexdigest(),
        required_fields=fields,required_numeric_fields_complete=all(fields.values()),
        period='2026-01-01/2026-06-30',original_driver_status=receipt['status'],unresolved_gaps=task['gaps'],
        task_reopened=False,report_accepted=False,installed_app_accepted=False,whole_task_accepted=False)
    target.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(receipt=str(target),required_numeric_fields_complete=record['required_numeric_fields_complete']),ensure_ascii=False))
