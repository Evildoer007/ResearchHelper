"""Independent original-to-input review, without resuming the live study.

The calculations below are read-only arithmetic checks, not App execution,
investment quality, or a completed research report.
"""
import copy
from decimal import Decimal
import hashlib
import json
import unittest
import zipfile

from tests import ROOT
from tests.test_asset_data_acceptance_review import verify_source
from researchhelper.contracts import (ContractError, FINANCIAL_INPUT_METRICS,
    calculate, validate_evidence, validate_calculation_context, validate_financial_method_scope)
from researchhelper.knowledge import ResearchKnowledge
from researchhelper.source_reader import validate_reading


DIRECTORY=ROOT/'result/acceptance/autonomous-data-phase-26qbn76f'


def load():
    receipt=json.loads((DIRECTORY/'receipt.json').read_text())
    if (DIRECTORY/'task.json').exists():
        task=json.loads((DIRECTORY/'task.json').read_text())
    else:
        with zipfile.ZipFile(DIRECTORY/'raw-qa.zip') as archive:
            task=json.loads(archive.read('task.json'))
    return task,receipt


class CurrentFinancialDataReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.task,cls.receipt=load()
        cls.receipt_hash=hashlib.sha256((DIRECTORY/'receipt.json').read_bytes()).hexdigest()

    @classmethod
    def tearDownClass(cls):
        assert hashlib.sha256((DIRECTORY/'receipt.json').read_bytes()).hexdigest()==cls.receipt_hash
        assert load()[0]==cls.task

    def current_inputs(self):
        rows=[e for e in self.task['evidence'] if e.get('calculation_context',{}).get('period')=='2026年半年度'
              and e.get('calculation_context',{}).get('accounting_scope')=='consolidated'
              and e.get('calculation_context',{}).get('metric') in ('revenue','net_profit','operating_cash_flow')]
        result={e['calculation_context']['metric']:e for e in rows}
        self.assertEqual(len(rows),3)
        self.assertEqual(set(result),{'revenue','net_profit','operating_cash_flow'})
        return result

    def test_actual_download_reading_and_adopted_values_match_originals(self):
        self.assertEqual(self.receipt['status'],'data_phase_sample_passed')
        self.assertTrue(self.receipt['owned_runtime_closed'])
        self.assertTrue(self.receipt['production_unchanged'])
        self.assertTrue(self.receipt['source_unchanged'])
        self.assertFalse(self.receipt['supplied_plan'])
        self.assertFalse(self.receipt['supplied_evidence'])
        self.assertFalse(self.receipt['pending_method_inputs'])
        self.assertFalse(self.receipt['report_accepted'])
        self.assertEqual(self.task['status'],'已取消')
        for source in self.task['source_records']:verify_source(source,self.task)
        validate_reading(self.task,self.task['reasoning_state']['reading']['bundle'])
        inputs=self.current_inputs()
        for metric,amount in [('revenue',276916580),('net_profit',47030638),('operating_cash_flow',60216851)]:
            item=inputs[metric]
            validate_evidence(item,self.task)
            self.assertEqual(item['raw_value'],amount)
            self.assertEqual(item['unit'],'千元')
            self.assertEqual(item['raw_unit'],'千元')
            self.assertEqual(item['calculation_context']['currency'],'CNY')
            self.assertEqual(item['calculation_context']['accounting_standard'],'CAS')

    def test_registered_ratios_reproduce_decimal_without_rewriting_the_task(self):
        inputs=self.current_inputs()
        for method in ('net_profit_margin_v1','operating_cash_profit_ratio_v1'):
            bindings={field:inputs[metric] for field,metric in FINANCIAL_INPUT_METRICS[method].items()}
            params=dict(amount_unit='千元',currency='CNY',period='2026年半年度',
                        accounting_scope='consolidated',accounting_standard='CAS')
            validate_calculation_context(method,bindings,params)
            validate_financial_method_scope(method,params,ResearchKnowledge().method_binding(self.task['knowledge'],method))
            values={field:e['value'] for field,e in bindings.items()}
            output=calculate(method,values,params)
            numerator=values['net_profit'] if method=='net_profit_margin_v1' else values['operating_cash_flow']
            denominator=values['revenue'] if method=='net_profit_margin_v1' else values['net_profit']
            expected=Decimal(str(numerator))/Decimal(str(denominator))*100
            self.assertAlmostEqual(output['value'],float(expected),places=10)
        self.assertEqual(self.task['calculations'],[])

    def test_parent_profit_substitution_and_unit_mixing_are_rejected(self):
        inputs=self.current_inputs()
        wrong=copy.deepcopy(inputs['net_profit']);wrong['value']=43284002
        with self.assertRaises(ContractError):validate_evidence(wrong,self.task)
        bindings=dict(revenue=inputs['revenue'],net_profit=copy.deepcopy(inputs['net_profit']))
        bindings['net_profit']['unit']='万元'
        with self.assertRaises(ContractError):
            validate_calculation_context('net_profit_margin_v1',bindings,
                dict(amount_unit='千元',currency='CNY',period='2026年半年度',accounting_scope='consolidated',accounting_standard='CAS'))
