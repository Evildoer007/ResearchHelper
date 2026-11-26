"""Financial ratios preserve declared same-unit evidence and locked knowledge.

All figures and statements are explicit synthetic fixtures. These checks use
no provider/model request or production store.
"""
import copy
import inspect
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import (ContractError, FINANCIAL_PERIOD_METHODS, FINANCIAL_AMOUNT_UNITS,
    METHOD_REGISTRY, calculate, calculation_input_units_available, validate_calculation_context,
    validate_calculation_parameters, validate_financial_method_scope)
from researchhelper.engine import ResearchAgent
from researchhelper.knowledge import ResearchKnowledge, ENTRIES, RETAINED_VERSIONS
from researchhelper.reasoning import calculation_plan_contract
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
import fundamental_reasoning_chain_fixture as prior
PERIOD = '2026年上半年'
FIELDS = {
    'gross_margin_v1': {'revenue':('revenue',100), 'cost':('operating_cost',80)},
    'net_profit_margin_v1': {'revenue':('revenue',100), 'net_profit':('net_profit',12)},
    'operating_cash_profit_ratio_v1': {'operating_cash_flow':('operating_cash_flow',-8), 'net_profit':('net_profit',12)}}


def parameters(method, unit):
    return dict(METHOD_REGISTRY[method]['parameters'], amount_unit=unit, period=PERIOD,
        accounting_scope='consolidated', accounting_standard='CAS')


def bindings(method, unit):
    return {field:dict(object='合成测试主体', unit=unit, raw_unit=unit, value=value, period=PERIOD,
        calculation_context=dict(entity_id='合成测试主体', currency='CNY', period=PERIOD,
            accounting_scope='consolidated', accounting_standard='CAS', metric=metric))
        for field,(metric,value) in FIELDS[method].items()}


def unit_fixture(agent, unit):
    source = inspect.getsource(prior.prepare_chain).replace('百万元', unit)
    namespace = dict(prior.prepare_chain.__globals__)
    exec(compile(source, __file__, 'exec'), namespace)
    task, host = namespace['prepare_chain'](agent)
    for spec in host['calculations']: spec['parameters']['amount_unit'] = unit
    return task, host


class FinancialAmountRepresentationTests(unittest.TestCase):
    def test_each_registered_ratio_accepts_three_explicit_same_unit_representations(self):
        for method in FINANCIAL_PERIOD_METHODS:
            for unit in FINANCIAL_AMOUNT_UNITS:
                with self.subTest(method=method,unit=unit):
                    record = bindings(method, unit)
                    before = copy.deepcopy(record)
                    params = parameters(method, unit)
                    validate_calculation_context(method, record, params)
                    value = calculate(method, {key:item['value'] for key,item in record.items()}, params)
                    expected = {'gross_margin_v1':20,'net_profit_margin_v1':12,
                        'operating_cash_profit_ratio_v1':-200/3}[method]
                    self.assertAlmostEqual(value['value'], expected)
                    self.assertEqual(value['unit'], '%')
                    self.assertEqual(record, before)

    def test_same_units_in_inputs_are_required_not_independent_unit_membership(self):
        for method in FINANCIAL_PERIOD_METHODS:
            record = bindings(method, '元'); record[next(iter(record))]['unit'] = '万元'
            with self.subTest(method=method), self.assertRaises(ContractError):
                validate_calculation_context(method, record, parameters(method, '元'))

    def test_explicit_amount_unit_must_match_every_original_input(self):
        for method in FINANCIAL_PERIOD_METHODS:
            with self.subTest(method=method), self.assertRaises(ContractError):
                validate_calculation_context(method, bindings(method,'万元'), parameters(method,'元'))

    def test_missing_unit_or_unregistered_amount_scale_is_not_filled(self):
        for unit in ('十元','兆元','USD',None,[],1):
            with self.subTest(unit=unit), self.assertRaises(ContractError):
                validate_calculation_parameters('gross_margin_v1', parameters('gross_margin_v1',unit))
        params = parameters('gross_margin_v1','元'); params.pop('amount_unit')
        with self.assertRaises(ContractError): validate_calculation_parameters('gross_margin_v1', params)

    def test_currency_is_not_inferred_or_changed_by_amount_representation(self):
        record = bindings('gross_margin_v1','元')
        record['cost']['calculation_context']['currency'] = 'USD'
        with self.assertRaises(ContractError): validate_calculation_context('gross_margin_v1',record,parameters('gross_margin_v1','元'))

    def test_quarter_and_cumulative_periods_are_not_combined(self):
        record = bindings('operating_cash_profit_ratio_v1','元')
        record['operating_cash_flow']['period'] = '2026年第二季度'
        record['operating_cash_flow']['calculation_context']['period'] = '2026年第二季度'
        with self.assertRaises(ContractError): validate_calculation_context('operating_cash_profit_ratio_v1', record,
            parameters('operating_cash_profit_ratio_v1','元'))

    def test_point_in_time_alone_cannot_stand_for_a_financial_flow_period(self):
        params=parameters('gross_margin_v1','元');params['period']='2026-06-30'
        with self.assertRaises(ContractError):validate_calculation_parameters('gross_margin_v1',params)

    def test_entity_scope_standard_and_metric_still_require_joint_consistency(self):
        for changes in (dict(entity_id='其他主体'), dict(accounting_scope='parent'),
                        dict(accounting_standard='IFRS'), dict(metric='parent_net_profit')):
            record = bindings('net_profit_margin_v1','万元')
            record['net_profit']['calculation_context'].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ContractError):
                validate_calculation_context('net_profit_margin_v1', record, parameters('net_profit_margin_v1','万元'))

    def test_candidate_availability_requires_distinct_inputs_in_one_unit(self):
        for method in FINANCIAL_PERIOD_METHODS:
            self.assertFalse(calculation_input_units_available(method,['元','万元']))
            self.assertFalse(calculation_input_units_available(method,['元']))
            self.assertTrue(calculation_input_units_available(method,['元','元']))
        self.assertTrue(calculation_input_units_available('management_custody_fee_summary_v1',['%','%/年','%','%/年']))

    def test_legacy_knowledge_version_does_not_acquire_expanded_unit_scope(self):
        knowledge=ResearchKnowledge()
        for method in FINANCIAL_PERIOD_METHODS:
            ident=METHOD_REGISTRY[method]['knowledge_id']
            entries={}
            def retain(key):
                item=copy.deepcopy(RETAINED_VERSIONS[key])
                for dependency in item.get('dependencies',[]):
                    retain((dependency['id'],dependency['version']))
                entries[key[0]]=item
            retain((ident,1))
            package=dict(entries=list(entries.values()),index={key:item['version'] for key,item in entries.items()})
            before=copy.deepcopy(package)
            binding=knowledge.method_binding(package,method)
            self.assertEqual(binding['knowledge_version'],1)
            validate_financial_method_scope(method,parameters(method,'百万元'),binding)
            with self.assertRaises(ContractError):validate_financial_method_scope(method,parameters(method,'元'),binding)
            self.assertEqual(package,before)

    def test_current_method_knowledge_is_versioned_without_erasing_v1(self):
        knowledge=ResearchKnowledge();package=knowledge.read('个股','财务比率',methods=FINANCIAL_PERIOD_METHODS)
        versions={'gross_margin_v1':3,'net_profit_margin_v1':4,'operating_cash_profit_ratio_v1':4}
        for method in FINANCIAL_PERIOD_METHODS:
            binding=knowledge.method_binding(package,method);version=versions[method]
            self.assertEqual(binding['knowledge_version'],version)
            self.assertEqual(binding['implementation_version'],2)
            self.assertEqual(binding['amount_units'],list(FINANCIAL_AMOUNT_UNITS))
            self.assertEqual(sorted(value for ident,value in RETAINED_VERSIONS if ident==binding['knowledge_id']),list(range(1,version+1)))
            self.assertEqual([item['version'] for item in ENTRIES if item['id']==binding['knowledge_id']],[version])

    def test_tools_separate_fixed_currency_from_registered_amount_choices(self):
        task = dict(id='unit_test',version=1,batch=1,input_hash='offline',cutoff='2026-10-02',questions=[{'id':'q1'}])
        contract = calculation_plan_contract(task)
        for method in FINANCIAL_PERIOD_METHODS:
            schema = contract['parameter_submissions'][method]
            self.assertEqual(schema['fixed_values'],{'currency':'CNY'})
            self.assertEqual(schema['registered_options']['amount_unit'],list(FINANCIAL_AMOUNT_UNITS))


class FinancialAmountWorkflowTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='financial-unit-workflow-',dir=ROOT/'result/acceptance')
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name),ROOT,offline=True)
        guard = patch('socket.socket.connect',side_effect=AssertionError('No network'))
        guard.start(); self.addCleanup(guard.stop)

    def test_raw_yuan_full_chain_saves_report_and_retains_input_units(self):
        task,host = unit_fixture(self.agent,'元'); final=self.agent.start(task['id'],host)
        self.assertEqual(final['status'],'已完成',final.get('error'))
        self.assertEqual(len(final['calculations']),2)
        self.assertTrue(all(item['unit']=='元' for item in final['evidence'] if item.get('value') is not None))
        self.assertTrue(all(item['parameters']['amount_unit']=='元' for item in final['calculations']))
        self.assertEqual(final['ledger']['used']['data_requests'],0)
        self.assertEqual(final['ledger']['used']['model_calls'],0)
        exported=self.agent.export(final['id'])
        self.assertIn('合成示例，非真实研究',exported['html'])

    def test_raw_ten_thousand_yuan_full_chain_keeps_negative_cash_ratio(self):
        task,host=unit_fixture(self.agent,'万元'); final=self.agent.start(task['id'],host)
        self.assertEqual(final['status'],'已完成',final.get('error'))
        cash=next(item for item in final['calculations'] if item['method']=='operating_cash_profit_ratio_v1')
        self.assertLess(cash['output']['value'],0)
        self.assertEqual(cash['inputs']['operating_cash_flow'],-8)
        self.assertEqual(cash['parameters']['amount_unit'],'万元')

    def test_parameter_unit_mismatch_delivers_missing_calculation_not_fabricated_result(self):
        task,host=unit_fixture(self.agent,'元')
        for spec in host['calculations']:spec['parameters']['amount_unit']='万元'
        final=self.agent.start(task['id'],host)
        self.assertEqual(final['status'],'部分完成',final.get('error'))
        self.assertFalse(final['calculations'])
        self.assertFalse(final['theses'])


if __name__=='__main__':unittest.main()
