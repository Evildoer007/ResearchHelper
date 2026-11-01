"""Independent field-level review of closed real data-stage studies.

The original driver's success label is not rewritten. Text availability and
structured requested inputs are reviewed separately, with no network, model,
task resumption, answer injection, or production writes.
"""
import copy
from decimal import Decimal
import hashlib
import json
import unittest

from tests import ROOT
from tests.test_asset_data_acceptance_review import load_retained_task, verify_source
from researchhelper.contracts import fingerprint


CASES = {'battery_operations': 'r91uw48x', 'broker_financials': 'pfoiaixv'}
PERIOD = '2026-01-01/2026-06-30'


def retained(name):
    folder = ROOT / 'result/acceptance' / ('autonomous-data-phase-' + CASES[name])
    task = load_retained_task(folder)
    receipt_bytes = (folder / 'receipt.json').read_bytes()
    return task, json.loads(receipt_bytes), hashlib.sha256(receipt_bytes).hexdigest()


def numeric_matches(task, value, unit, object_code):
    """Sample-specific numeric presence, not general semantic approval."""
    return [item['id'] for item in task['evidence']
            if type(item.get('value')) in (int, float)
            and Decimal(str(item['value'])) == Decimal(value)
            and item.get('unit') == unit and item.get('object') == object_code
            and item.get('period') == PERIOD]


def review():
    battery, battery_receipt, battery_hash = retained('battery_operations')
    broker, broker_receipt, broker_hash = retained('broker_financials')
    battery_fields = {
        'battery_capacity': numeric_matches(battery, '525', 'GWh', '300750.SZ'),
        'battery_production': numeric_matches(battery, '498', 'GWh', '300750.SZ'),
        'capacity_utilization': numeric_matches(battery, '94.86', '%', '300750.SZ'),
    }
    broker_fields = {
        'company_revenue': numeric_matches(broker, '49691932907.10', '元', '600030.SH'),
        'parent_net_profit': numeric_matches(broker, '23343231403.68', '元', '600030.SH'),
    }
    result = dict(scope='independent_requested_field_review', network_calls=0, model_calls=0,
                  task_resumed=False, original_receipts_rewritten=False,
                  installed_app_accepted=False, report_accepted=False, cases={})
    for name, task, receipt, receipt_hash, fields in (
        ('battery_operations', battery, battery_receipt, battery_hash, battery_fields),
        ('broker_financials', broker, broker_receipt, broker_hash, broker_fields),
    ):
        result['cases'][name] = dict(
            task_id=task['id'], task_fingerprint=fingerprint(task),
            original_receipt_sha256=receipt_hash, original_driver_status=receipt['status'],
            independent_status='partial_requested_numeric_inputs',
            original_downloaded=any(s['operation']=='filing_original' for s in task['source_records']),
            checked_numeric_fields=fields,
            missing_numeric_fields=[name for name, ids in fields.items() if not ids],
            adopted_numeric_evidence=[dict(id=e['id'], value=e['value'], unit=e['unit'])
                                      for e in task['evidence'] if 'value' in e])
    result['cases']['battery_operations']['limits'] = [
        'Capacity and production are present in the quoted original table, not numeric evidence.',
        '525 is capacity; 764 is construction capacity; 498 is production. Do not swap columns.']
    result['cases']['broker_financials']['limits'] = [
        'Requested financial and segment tables are quoted, not structured monetary inputs.',
        'The two adopted numeric percentages describe shareholder stakes, not revenue or profit.',
        'Some adopted flow quotes use a point date or period start instead of the complete period.']
    return result


def save_review():
    result = review()
    target = ROOT / 'result/acceptance/requested-operating-data-review.json'
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return target


class RequestedOperatingDataReviewTests(unittest.TestCase):
    def test_closed_real_tasks_and_original_source_hashes(self):
        for name in CASES:
            task, receipt, _ = retained(name)
            self.assertEqual(task['status'], '已取消')
            self.assertTrue(receipt['owned_runtime_closed'] and receipt['production_unchanged'])
            self.assertFalse(receipt['supplied_plan'] or receipt['supplied_evidence'] or receipt['supplied_answers'])
            for source in task['source_records']:
                verify_source(source, task)

    def test_downloaded_table_does_not_count_as_structured_capacity_or_production(self):
        task, _, _ = retained('battery_operations')
        expected = '项目\t产能\t在建产能\t产能利用率\t产量\n电池系统（GWh）\t525\t764\t94.86%\t498'
        self.assertTrue(any(expected in e.get('text', '') for e in task['evidence']))
        fields = review()['cases']['battery_operations']['checked_numeric_fields']
        self.assertEqual(fields['battery_capacity'], [])
        self.assertEqual(fields['battery_production'], [])
        self.assertEqual(fields['capacity_utilization'], ['s3'])

    def test_broker_requested_amounts_exist_in_text_but_not_numeric_evidence(self):
        task, _, _ = retained('broker_financials')
        text = task['evidence'][0]['text']
        self.assertIn('营业收入 49,691,932,907.10', text)
        self.assertIn('归属于母公司股东的净利润 23,343,231,403.68', text)
        self.assertIn('人民币元', text)
        self.assertEqual(review()['cases']['broker_financials']['missing_numeric_fields'],
                         ['company_revenue', 'parent_net_profit'])

    def test_unrelated_share_percentages_cannot_satisfy_requested_amounts(self):
        task, _, _ = retained('broker_financials')
        numeric = [e for e in task['evidence'] if 'value' in e]
        self.assertEqual([e['value'] for e in numeric], [19.84, 23.96])
        self.assertTrue(all(e['unit']=='%' and '持有公司股份比例' in e['text'] for e in numeric))

    def test_flow_period_and_segment_context_are_not_implicitly_repaired(self):
        task, _, _ = retained('broker_financials')
        segment = next(e for e in task['evidence'] if '十三、 分部报告' in e.get('text', ''))
        self.assertIn('证券投资业务', segment['text'])
        self.assertEqual(segment['period'], '2026-01-01')
        self.assertNotEqual(segment['period'], PERIOD)

    def test_review_preserves_original_pass_receipts_and_task_fingerprints(self):
        before = {name:(fingerprint(retained(name)[0]),retained(name)[2]) for name in CASES}
        result = review()
        for name in CASES:
            self.assertEqual(result['cases'][name]['original_driver_status'], 'data_phase_sample_passed')
            self.assertEqual((fingerprint(retained(name)[0]),retained(name)[2]),before[name])
            self.assertEqual(result['cases'][name]['independent_status'], 'partial_requested_numeric_inputs')

    def test_same_number_with_wrong_unit_period_or_object_is_not_a_match(self):
        task, _, _ = retained('battery_operations')
        for field, bad in (('unit', 'GWh'), ('period', '2026-06-30'), ('object', '600030.SH')):
            changed = copy.deepcopy(task)
            next(e for e in changed['evidence'] if 'value' in e)[field] = bad
            self.assertFalse(numeric_matches(changed, '94.86', '%', '300750.SZ'))


if __name__ == '__main__':
    unittest.main()
