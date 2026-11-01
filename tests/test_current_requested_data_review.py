"""Independent requested-field review of new R72 real acquisition runs.

No production changes, network, model, task resumption or financial approval.
The broker's extra failed catalogue request stays a real unresolved task gap.
"""
from decimal import Decimal
import hashlib
import json
import re
import unittest

from tests import ROOT
from tests.test_asset_data_acceptance_review import load_retained_task,verify_source
from researchhelper.contracts import fingerprint,validate_evidence
from researchhelper.evidence import validate_reporting_period_basis


CASES={'battery_operations':'spvikuhn','broker_financials':'7id2abhz'}
FIELDS={
    'battery_operations':('300750.SZ',{
        'capacity':('525','GWh'),'production':('498','GWh'),'utilization':('94.86','%')}),
    'broker_financials':('600030.SH',{
        'company_revenue':('49691932907.10','元'),'parent_net_profit':('23343231403.68','元'),
        'brokerage':('13142238411.64','元'),'asset_management':('8002725294.56','元'),
        'securities_investment':('19854064804.25','元'),'underwriting':('2914385272.80','元'),
        'other_business':('5778519123.85','元')})}
PERIOD='2026-01-01/2026-06-30'


def retained(name):
    folder=ROOT/'result/acceptance'/('autonomous-data-phase-'+CASES[name])
    raw=(folder/'receipt.json').read_bytes()
    return load_retained_task(folder),json.loads(raw),hashlib.sha256(raw).hexdigest()


def matching_fields(task,name):
    object_code,fields=FIELDS[name]
    return {field:[e['id'] for e in task['evidence'] if type(e.get('value')) in (int,float)
        and Decimal(str(e['value']))==Decimal(value) and e['unit']==unit
        and e['object']==object_code and e['period']==PERIOD
        and e['source_check']==e['applicability_check']=='通过']
        for field,(value,unit) in fields.items()}


def save_review():
    result=dict(scope='independent_requested_field_review',source_rules_version=72,
        network_calls=0,model_calls=0,task_resumed=False,original_receipts_rewritten=False,
        calculation_accepted=False,report_accepted=False,installed_app_accepted=False,cases={})
    for name in CASES:
        task,receipt,receipt_hash=retained(name);fields=matching_fields(task,name)
        result['cases'][name]=dict(task_id=task['id'],task_fingerprint=fingerprint(task),
            receipt_sha256=receipt_hash,driver_status=receipt['status'],
            requested_numeric_fields=fields,missing_fields=[field for field,ids in fields.items() if not ids],
            requested_numeric_sample_passed=all(fields.values()),
            unresolved_task_gaps=receipt['gaps'],ledger=task['ledger']['used'],
            original_sources=[dict(id=s['id'],hash=s['snapshot']['material']['hash'])
                for s in task['source_records'] if s['operation']=='filing_original'])
    target=ROOT/'result/acceptance/data-acquisition-r72-review.json'
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return target


class CurrentRequestedDataReviewTests(unittest.TestCase):
    def test_real_name_only_studies_are_closed_and_sources_match_the_ledger(self):
        for name in CASES:
            task,receipt,_=retained(name)
            self.assertEqual(task['status'],'已取消')
            self.assertTrue(receipt['owned_runtime_closed'] and receipt['production_unchanged'] and receipt['source_unchanged'])
            self.assertFalse(any(receipt[key] for key in ('supplied_codes','supplied_plan','supplied_evidence','supplied_answers')))
            for source in task['source_records']:verify_source(source,task)

    def test_all_requested_numeric_fields_have_correct_unit_object_and_full_period(self):
        for name in CASES:
            task,_,_=retained(name)
            fields=matching_fields(task,name)
            self.assertTrue(all(fields.values()),fields)
            records={source['id']:source for source in task['source_records']}
            for item in task['evidence']:
                validate_evidence(item,task)
                validate_reporting_period_basis(item,records[item['artifact_id']])

    def test_operating_numbers_match_the_actual_row_not_construction_capacity(self):
        task,_,_=retained('battery_operations')
        original=next(s for s in task['source_records'] if s['operation']=='filing_original')
        table=next(p for p in original['snapshot']['material']['parts'] if p['locator']=='第17页表1')
        self.assertIn('项目\t产能\t在建产能\t产能利用率\t产量',table['text'])
        self.assertIn('电池系统（GWh）\t525\t764\t94.86%\t498',table['text'])
        self.assertFalse(any(e.get('value')==764 for e in task['evidence']))

    def test_broker_values_match_company_and_segment_rows_not_share_percentages(self):
        task,_,_=retained('broker_financials')
        original=next(s for s in task['source_records'] if s['operation']=='filing_original')
        parts={p['locator']:p for p in original['snapshot']['material']['parts']}
        self.assertIn('49,691,932,907.10',parts['第115页列式文本']['text'])
        self.assertIn('23,343,231,403.68',parts['第116页列式文本']['text'])
        self.assertIn('归属于母公司股东的净利润',parts['第116页列式文本']['text'])
        segment=parts['第262页']['text']
        self.assertRegex(segment,r'经纪业务\s+资产管理业务\s+证券投资业务\s+证券承销业务\s+其他\s+合计')
        row=next(line for line in segment.splitlines() if line.strip().startswith('一、营业收入'))
        amounts=re.findall(r'\d{1,3}(?:,\d{3})+\.\d{2}',row)
        self.assertEqual([Decimal(x.replace(',','')) for x in amounts],
            [Decimal(FIELDS['broker_financials'][1][field][0]) for field in
             ('brokerage','asset_management','securities_investment','underwriting','other_business','company_revenue')])
        self.assertFalse(any(e.get('unit')=='%' and e.get('value') in (19.84,23.96) for e in task['evidence']))

    def test_independent_decimal_sum_is_not_an_app_calculation(self):
        fields=FIELDS['broker_financials'][1]
        total=sum(Decimal(fields[name][0]) for name in
                  ('brokerage','asset_management','securities_investment','underwriting','other_business'))
        self.assertEqual(total,Decimal(fields['company_revenue'][0]))
        task,receipt,_=retained('broker_financials')
        self.assertFalse(task.get('calculations'))
        self.assertFalse(receipt['report_accepted'])

    def test_extra_failed_catalogue_request_is_not_hidden_or_declared_complete(self):
        task,receipt,_=retained('broker_financials')
        self.assertTrue(all(matching_fields(task,'broker_financials').values()))
        self.assertEqual(receipt['status'],'data_phase_partial')
        self.assertTrue(any('-4001' in gap for gap in receipt['gaps']))
        _,clean,_=retained('battery_operations')
        self.assertEqual(clean['gaps'],[])


if __name__=='__main__':
    unittest.main()
