"""Expanded metadata and balance semantics, retaining earlier safety assertions."""
import copy
import hashlib
import json
import socket
import unittest
from unittest.mock import patch
from tests import ROOT
from tests import test_fixed_financial_catalog_delivery as retained
from tests import test_fixed_financial_reuse as reuse
from researchhelper import ifind_financial_catalog as catalog
from researchhelper.draft_sources import observations,writing_context
from researchhelper.draft_analysis import analyze
from researchhelper.financial_sources import basic_financial_definitions,metadata_request_shapes
from researchhelper.draft_workflow import _write
from researchhelper.draft_visuals import comparison_figures,comparison_table

BALANCES={'ths_account_receivable_stock','ths_inventory_stock','ths_accounts_payable_stock'}
CORE={'ths_revenue_stock','ths_operating_cost_stock','ths_np_stock','ths_np_atoopc_stock','ths_ncf_from_oa_stock'}


class ExpandedCatalogueTests(retained.DeliveredFixedFinancialCatalogueTests):
    def test_only_confirmed_fields_and_distinct_profit_meaning(self):
        rows=catalog.catalogue()['definitions']
        self.assertEqual(set(rows),CORE|BALANCES)
        self.assertEqual(rows['ths_np_atoopc_stock']['meaning'],'归属于母公司所有者的净利润')
        self.assertNotEqual(rows['ths_np_atoopc_stock']['metric'],'net_profit')
        self.assertEqual(rows['ths_np_stock']['metric'],'net_profit')
        self.assertEqual(rows['ths_np_stock']['meaning'],'净利润')
        self.assertIsNone(catalog.definition('ths_net_profit_stock'))
        self.assertIsNone(catalog.parameter_template('unknown'))
        self.assertIsNone(catalog.definition({'indicator':'ths_revenue_stock'}))

    def test_new_provenance_matches_actual_official_responses(self):
        from tests.accept_working_capital_parameters_live import selected_fields,validate_parameters
        actual={row['indicator']:row for row in selected_fields()}
        for field in BALANCES:
            row=catalog.definition(field)
            self.assertEqual(row['name'],actual[field]['name'])
            self.assertEqual(row['period_kind'],'point_in_time')
            self.assertEqual(row['source_proof']['reviewed_on'],'2026-10-27')
            self.assertIn('资产负债表',row['source_proof']['observed_catalogue_path'][0])
            for kind in ('indicator_response','parameter_response'):
                proof=row['source_proof'][kind];path=ROOT/proof['artifact']
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),proof['sha256'])
            payload=json.loads((ROOT/row['source_proof']['parameter_response']['artifact']).read_text())
            self.assertEqual([item['name'] for item in validate_parameters(field,payload)],['D','C'])
            self.assertFalse(row['financial_values_approved'])
            self.assertEqual(set(row['data_attributes'].values()),{'unverified'})


class ExpandedReuseTests(reuse.FixedFinancialReuseTests):
    def test_known_five_field_parameters_are_reusable_not_business_values(self):
        definitions=basic_financial_definitions(self.task)
        self.assertEqual(set(definitions),CORE|BALANCES)
        for value in definitions.values():
            self.assertEqual([row['name'] for row in value],['D','C'])
            self.assertFalse(any('defaultValue' in row for row in value))
        self.assertEqual(self.task['evidence'],[])

    def test_first_shape_discloses_reuse_not_permission(self):
        shapes=metadata_request_shapes(self.task)
        self.assertEqual([row['operation'] for row in shapes],['get_limit_policy'])
        self.assertEqual(set(shapes[0]['reusable_financial_fields']),CORE|BALANCES)
        self.assertTrue(shapes[0]['raw_only'])
        for field in BALANCES:
            self.assertEqual(shapes[0]['reusable_financial_fields'][field]['period_kind'],'point_in_time')


def balance_task():
    task=dict(id='synthetic-balance',version=1,input_hash='fixed',source_records=[])
    fields=['ths_account_receivable_stock','ths_inventory_stock','ths_accounts_payable_stock']
    for index,period in enumerate(['20250630','20260630']):
        record=dict(id='balance-'+str(index),task_id=task['id'],research_version=1,source='ifind',
            operation='basic_data_service',parameters=dict(codes='002594.SZ',indipara=[
                dict(indicator=field,indiparams=[period,'1']) for field in fields]),
            snapshot=dict(tables=[dict(thscode='002594.SZ',table={field:[10+index] for field in fields})]))
        task['source_records'].append(record)
    return task


class BalanceSemanticsTests(unittest.TestCase):
    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('Offline only'))
        guard.start();self.addCleanup(guard.stop)

    def test_unknown_unit_stays_unknown_and_context_marks_balance(self):
        task=balance_task();facts=observations(task)
        self.assertEqual(len(facts),6)
        self.assertEqual({row['unit'] for row in facts},{'原始单位待核对'})
        self.assertEqual({row['period_kind'] for row in facts},{'point_in_time'})
        context=writing_context(task,facts)
        self.assertEqual({row['period_kind'] for row in context['observations']},{'point_in_time'})

    def test_stock_growth_is_not_cashflow_contribution(self):
        task=balance_task();result=analyze(task,observations(task))
        self.assertEqual(len(result['results']),3)
        for row in result['results']:
            self.assertEqual((row['method'],row['display']),('raw_change_v1','10.00'))
            self.assertIn('余额',row['label']);self.assertIn('不能视为当期经营现金流贡献',row['condition'])
            self.assertTrue(row['conditional_equal_scale']);self.assertFalse(row['financial_approval'])

    def test_balance_dates_without_year_match_do_not_create_cashflow_math(self):
        task=balance_task()
        for item in task['source_records'][0]['parameters']['indipara']:item['indiparams'][0]='20251231'
        self.assertEqual(analyze(task,observations(task))['results'],[])

    def test_missing_one_balance_does_not_discard_other_results(self):
        task=balance_task();task['source_records'][1]['snapshot']['tables'][0]['table']['ths_inventory_stock']=[None]
        self.assertEqual(len(analyze(task,observations(task))['results']),2)

    def test_balance_chart_is_separate_and_all_raw_numbers_remain(self):
        task=balance_task();facts=observations(task)
        content=dict(task_id=task['id'],research_version=1,source_records=task['source_records'],
            draft=dict(facts=facts,calculations=analyze(task,facts)['results']))
        figures=comparison_figures(content,[])
        self.assertEqual(figures[0]['name'],'营运资金余额同比变化')
        self.assertEqual(len(figures[0]['data_refs']),6)
        self.assertEqual(comparison_table(content)['observations'],6)

    def test_prior_actual_calculations_and_observations_are_unchanged(self):
        path=ROOT/'result/acceptance/one-sentence-research-vgjkyxq9/task.json'
        task=json.loads(path.read_text());facts=observations(task)
        self.assertEqual(analyze(task,facts)['results'],task['draft_report']['calculations'])
        self.assertEqual(len(task['draft_report']['calculations']),11)
        self.assertFalse(any('period_kind' in row for row in facts))
