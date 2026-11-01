"""Complete literal financial scales without changing numbers or old bindings."""
import copy
import gzip
import json
from pathlib import Path
import tempfile
import unittest

from tests import ROOT
from tests import test_financial_amount_representations as fixtures
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.contracts import (ContractError, FINANCIAL_PERIOD_METHODS,
    FINANCIAL_AMOUNT_UNITS, LEGACY_FINANCIAL_DEFINITIONS, calculate, fingerprint,
    calculation_input_units_available, validate_calculation_context,
    validate_financial_method_scope, methods_for_task)
from researchhelper.knowledge import ResearchKnowledge, RETAINED_VERSIONS


class FinancialOriginalUnitTests(unittest.TestCase):
    def test_all_registered_amount_scales_preserve_ratios_without_conversion(self):
        for method in FINANCIAL_PERIOD_METHODS:
            for unit in FINANCIAL_AMOUNT_UNITS:
                with self.subTest(method=method, unit=unit):
                    items=fixtures.bindings(method,unit);before=copy.deepcopy(items)
                    params=fixtures.parameters(method,unit)
                    validate_calculation_context(method,items,params)
                    output=calculate(method,{key:item['value'] for key,item in items.items()},params)
                    self.assertAlmostEqual(output['value'],{'gross_margin_v1':20,
                        'net_profit_margin_v1':12,'operating_cash_profit_ratio_v1':-200/3}[method])
                    self.assertEqual(items,before)
                    self.assertTrue(calculation_input_units_available(method,[unit,unit]))

    def test_mixed_scales_or_undeclared_scale_are_not_converted(self):
        for unit in ('千元','千万元','亿元'):
            items=fixtures.bindings('net_profit_margin_v1',unit)
            items['revenue']['unit']='万元'
            with self.assertRaises(ContractError):
                validate_calculation_context('net_profit_margin_v1',items,
                    fixtures.parameters('net_profit_margin_v1',unit))

    def test_maintained_knowledge_explicitly_registers_new_units(self):
        knowledge=ResearchKnowledge()
        package=knowledge.read('个股','财务比率',methods=FINANCIAL_PERIOD_METHODS)
        for method in FINANCIAL_PERIOD_METHODS:
            binding=knowledge.method_binding(package,method)
            self.assertEqual(binding['amount_units'],list(FINANCIAL_AMOUNT_UNITS))
            validate_financial_method_scope(method,fixtures.parameters(method,'千元'),binding)
            self.assertEqual(binding['implementation_version'],2)

    def test_old_locked_knowledge_keeps_definition_hash_and_rejects_new_units(self):
        knowledge=ResearchKnowledge()
        package=knowledge.read('个股','财务比率',methods=FINANCIAL_PERIOD_METHODS)
        for method in FINANCIAL_PERIOD_METHODS:
            legacy=copy.deepcopy(package)
            ident=LEGACY_FINANCIAL_DEFINITIONS[method]['knowledge_id']
            current=next(e for e in legacy['entries'] if e['id']==ident)
            old=copy.deepcopy(RETAINED_VERSIONS[(ident,current['previous_version'])])
            legacy['entries']=[old if e['id']==ident else e for e in legacy['entries']]
            legacy['index'][ident]=old['version']
            binding=knowledge.method_binding(legacy,method)
            self.assertEqual(binding['definition_hash'],fingerprint(LEGACY_FINANCIAL_DEFINITIONS[method]))
            with self.assertRaises(ContractError):
                validate_financial_method_scope(method,fixtures.parameters(method,'千元'),binding)
            advertised=methods_for_task(dict(knowledge=legacy))[method]
            self.assertNotIn('千元',advertised['parameter_variants']['amount_unit'])

    def test_previous_real_calculation_bindings_are_byte_equivalent(self):
        retained=ROOT/'result/acceptance/one-sentence-research-khgjpz2w/task.json.gz'
        with gzip.open(retained,'rt',encoding='utf-8') as incoming:task=json.load(incoming)
        self.assertTrue(task['calculations'])
        for calculation in task['calculations']:
            self.assertEqual(ResearchKnowledge().method_binding(task['knowledge'],calculation['method']),
                             calculation['method_binding'])

    def test_thousand_yuan_full_offline_chain_keeps_units_in_report(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'result/acceptance') as directory:
            agent=ResearchAgent(ResearchArchive(Path(directory)),ROOT,offline=True)
            try:
                task,host=fixtures.unit_fixture(agent,'千元')
                final=agent.start(task['id'],host)
                self.assertEqual(final['status'],'已完成',final.get('error'))
                self.assertEqual(len(final['calculations']),2)
                self.assertTrue(all(c['parameters']['amount_unit']=='千元' for c in final['calculations']))
                self.assertIn('均为千元',agent.export(final['id'])['html'])
                self.assertEqual(final['ledger']['used']['data_requests'],0)
                self.assertEqual(final['ledger']['used']['model_calls'],0)
            finally:agent.close_runtime()
