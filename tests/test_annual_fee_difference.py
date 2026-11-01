"""Source notation, fee meaning and annual frequency are independent checks.

All end-to-end fixtures below are explicitly synthetic local documents. They
do not use models, supplier calls or constitute real financial acceptance.
"""
import copy
import hashlib
import json
import math
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import (ContractError, calculate, validate_calculation_context,
    valid_calculation_input_unit, methods_for_task)
from researchhelper.engine import ResearchAgent
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import calculation_plan_contract
from researchhelper.reports import compose

METHOD = 'fee_difference_v2'
PERIOD = '2026年度规则'
PARAMETERS = dict(unit='%/年', period=PERIOD, fee_kind='management_fee')


class AnnualFeeDifferenceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True)
        self.addCleanup(self.agent.close_runtime)
        network = patch('socket.socket.connect', side_effect=AssertionError('Offline fee fixture'))
        network.start(); self.addCleanup(network.stop)

    def prepare(self, *, metric='management_fee', units=('%', '%'), frequency='年费率'):
        label = {'management_fee':'管理费', 'custody_fee':'托管费'}[metric]
        task = self.agent.create('比较ETF-A与ETF-B的'+label+'年度费率差，仅核对合成规则。',
            entry='ETF', intent='比较对象', objects=['ETF-A', 'ETF-B'], cutoff='2026-07-20T18:00:00+08:00')
        task['synthetic'] = True
        task['fee_method_catalogue_version'] = 2
        task['questions'][0].update(kind='comparison', method_needs=[METHOD])
        task['input_hash'] = input_fingerprint(task)
        self.agent.archive.save(task)
        specs = []
        for index, (value, unit) in enumerate(zip((.15, .5), units)):
            side = 'a' if index==0 else 'b'; fund = 'ETF-'+side.upper()
            text = f'合成测试规则，非真实产品。{fund}，{fund}份额，{PERIOD}。{label}按基金资产净值计提，{frequency}为{value}{unit}。发布于2026-07-19。'
            task = self.agent.add_material(task['id'], side+'.txt', text.encode())
            material = task['materials'][-1]
            anchor = dict(kind='material', artifact_id=material['id'], snapshot_hash=material['snapshot_hash'], part='段落1', quote=text)
            context = dict(fund_id=fund, share_class=fund+'份额', period=PERIOD,
                fee_basis='net_assets', rate_period='annual', metric=metric)
            specs.append(dict(id='rate_'+side, artifact_id=material['id'], object=fund,
                record_locator=dict(part='段落1'), text=text, period=PERIOD,
                nature='合同政策条款', raw_token=str(value), raw_value=value, value=value,
                raw_unit=unit, unit=unit, conversion_factor=1,
                published_at='2026-07-19', publication=dict(at='2026-07-19', basis='原文发布日期',
                    precision='日', timezone='Asia/Shanghai', certainty='已确认', basis_ref=anchor),
                calculation_context=context, calculation_context_basis={key:copy.deepcopy(anchor) for key in context},
                question_ids=['q1'], review=dict(question_ids=['q1'], allowed_natures=['合同政策条款'],
                    conditions=['同期间同类合成规则，不推断收益'], reason='逐项核对两份合成原文的费率和年度口径')))
        host = dict(evidence_specs=specs, calculations=[dict(id='single_fee', method=METHOD,
            inputs=dict(fee_a='rate_a', fee_b='rate_b'), parameters=dict(PARAMETERS, fee_kind=metric), question_ids=['q1'])],
            answers=dict(q1=dict(answer='合成规则的同类年度费率差值为-0.35个百分点/年，不是收益差。',
                refs=['rate_a', 'rate_b'], calculation_refs=['single_fee'])))
        return task, host

    def bindings(self, host):
        return dict(zip(('fee_a','fee_b'),host['evidence_specs']))

    def test_percent_source_delivers_without_rewriting_unit_or_value(self):
        task, host = self.prepare()
        done = self.agent.start(task['id'], host)
        self.assertEqual(done['status'], '已完成', done.get('error'))
        self.assertEqual([item['unit'] for item in done['evidence']], ['%', '%'])
        self.assertEqual([item['raw_value'] for item in done['evidence']], [.15, .5])
        self.assertAlmostEqual(done['calculations'][0]['output']['value'], -.35)
        self.assertEqual(done['calculations'][0]['output']['unit'], '百分点/年')
        self.assertEqual(done['calculations'][0]['implementation_version'], 2)
        self.assertEqual(compose(done)['fee_method_catalogue_version'], 2)
        self.assertEqual(done['ledger']['used']['model_calls'], 0)

    def test_mixed_source_notation_and_custody_keep_original_units(self):
        for metric in ('management_fee','custody_fee'):
            task, host = self.prepare(metric=metric, units=('%/年','%'))
            done = self.agent.start(task['id'],host)
            with self.subTest(metric=metric):
                self.assertEqual(done['status'],'已完成',done.get('error'))
                self.assertEqual([item['unit'] for item in done['evidence']],['%/年','%'])

    def test_wrong_or_missing_annual_source_is_rejected(self):
        for frequency in ('费率', '月费率'):
            task, host = self.prepare(frequency=frequency)
            done = self.agent.start(task['id'],host)
            with self.subTest(frequency=frequency):
                self.assertNotEqual(done['status'],'已完成')
                self.assertFalse(done['calculations'])
                self.assertTrue(done['rejected_evidence'])

    def test_different_fee_kind_period_basis_or_incomplete_context_rejected(self):
        _, host = self.prepare()
        bindings = self.bindings(host)
        validate_calculation_context(METHOD,bindings,PARAMETERS)
        for field, value in (('metric','custody_fee'),('period','另一期间'),
                             ('fee_basis','gross_assets'),('rate_period','monthly'),('share_class','')):
            changed = copy.deepcopy(bindings)
            changed['fee_b']['calculation_context'][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                validate_calculation_context(METHOD,changed,PARAMETERS)

    def test_annual_frequency_cannot_be_borrowed_from_another_original(self):
        task, host = self.prepare()
        host['evidence_specs'][0]['calculation_context_basis']['rate_period'] = copy.deepcopy(
            host['evidence_specs'][1]['calculation_context_basis']['rate_period'])
        done = self.agent.start(task['id'],host)
        self.assertNotEqual(done['status'],'已完成')
        self.assertFalse(done['calculations'])

    def test_legacy_method_units_and_task_vocabulary_do_not_change(self):
        self.assertFalse(valid_calculation_input_unit('fee_difference_v1','fee_a','%'))
        self.assertTrue(valid_calculation_input_unit(METHOD,'fee_a','%'))
        for version in (1,2):
            methods = methods_for_task(dict(method_catalogue_version=version))
            self.assertIn('fee_difference_v1',methods)
            self.assertNotIn(METHOD,methods)
        for value in (True, 0, 3):
            with self.assertRaises(ContractError):
                methods_for_task(dict(fee_method_catalogue_version=value))

    def test_new_planning_and_explicit_legacy_obligation_use_separate_catalogues(self):
        agent = ResearchAgent(self.agent.archive,ROOT,offline=True,require_argument_support=True)
        self.addCleanup(agent.close_runtime)
        new = agent.create('核对基金费用规则', entry='ETF')
        self.assertEqual(new['fee_method_catalogue_version'],2)
        self.assertIn(METHOD,calculation_plan_contract(new)['methods'])
        self.assertNotIn('fee_difference_v1',calculation_plan_contract(new)['methods'])
        old = agent.create('核对既有年度费用差', entry='ETF',questions=[dict(id='q1',question='核对既有年度费用差',kind='comparison',method_needs=['fee_difference_v1'])])
        self.assertEqual(old['fee_method_catalogue_version'],1)
        self.assertIn('fee_difference_v1',calculation_plan_contract(old)['methods'])
        detached = dict(new); detached.pop('fee_method_catalogue_version')
        self.assertNotEqual(input_fingerprint(new),input_fingerprint(detached))

    def test_arithmetic_rejects_nonfinite_negative_wrong_units_and_unknown_fee(self):
        self.assertEqual(calculate(METHOD,dict(fee_a=0,fee_b=0),PARAMETERS)['value'],0)
        for value in (True,math.inf,math.nan,-1,'0.15'):
            with self.subTest(value=value), self.assertRaises(ContractError):
                calculate(METHOD,dict(fee_a=value,fee_b=.5),PARAMETERS)
        for params in (dict(PARAMETERS,unit='%'),dict(PARAMETERS,fee_kind='total_fee'),
                       {key:value for key,value in PARAMETERS.items() if key!='fee_kind'}):
            with self.assertRaises(ContractError):
                calculate(METHOD,dict(fee_a=.15,fee_b=.5),params)

    def test_readiness_requires_annual_context_not_just_two_percent_values(self):
        from tests.accept_autonomous_data_phase import method_input_inventory
        task, host = self.prepare()
        done = self.agent.start(task['id'],host)
        self.assertEqual(done['status'],'已完成')
        self.assertTrue(method_input_inventory(done)[0]['compatible_financial_roles_available'])
        for change in ('missing','metric','period'):
            changed=copy.deepcopy(done)
            if change=='missing':changed['evidence'][0].pop('calculation_context')
            else:changed['evidence'][0]['calculation_context'][change]='not_the_same'
            inventory=method_input_inventory(changed)[0]
            with self.subTest(change=change):
                self.assertTrue(inventory['units_available'])
                self.assertFalse(inventory['compatible_financial_roles_available'])
                self.assertFalse(inventory['financial_calculation_approved'])


class AnnualFeeSourceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.probe_etf_extraction_context import RETAINED, payload_for
        from researchhelper.output_schema import original_span_output_schema
        cls.retained = RETAINED
        cls.original_hash = hashlib.sha256(RETAINED.read_bytes()).hexdigest()
        cls.task = json.loads(RETAINED.read_text())
        cls.task.update(fee_method_catalogue_version=2,original_prompt_projection_version=2,calculation_specs=[])
        cls.task['questions'][0]['method_needs'] = [METHOD]
        cls.task['input_hash'] = input_fingerprint(cls.task)
        cls.payload = payload_for(cls.task)
        cls.views = cls.payload['untrusted_sources']
        cls.schema = original_span_output_schema(cls.task,cls.views)

    @classmethod
    def tearDownClass(cls):
        if hashlib.sha256(cls.retained.read_bytes()).hexdigest() != cls.original_hash:
            raise AssertionError('Closed ETF study changed')

    def test_fee_context_does_not_hide_tracking_index_or_other_text_requirements(self):
        from researchhelper.output_schema import fee_original_output_schema
        self.assertIsNone(fee_original_output_schema(self.task,self.views))
        self.assertIsNotNone(self.schema)
        variants = self.schema['properties']['selections']['items']['anyOf']
        refs = {variant['properties']['source_ref']['enum'][0] for variant in variants}
        self.assertEqual(len(refs),len(self.views))
        for ref in refs:
            kinds = {variant['properties']['kind']['enum'][0] for variant in variants
                if variant['properties']['source_ref']['enum']==[ref]}
            self.assertEqual(kinds,{'text','number'})
        for variant in variants:
            if variant['properties']['kind']['enum']!=['number']:
                continue
            contexts = variant['properties']['contexts']['items']['anyOf']
            fields = {choice['properties']['field']['enum'][0] for choice in contexts}
            self.assertEqual(fields,{'fund_id','share_class','period','fee_basis','rate_period','metric'})

    def test_complete_two_original_context_stays_within_unchanged_budget(self):
        from researchhelper.contracts import canonical
        from researchhelper.reasoning import MAX_CONTEXT_CHARACTERS
        from researchhelper.output_schema import original_span_prompt
        prompt = original_span_prompt(self.payload,self.task)
        self.assertEqual(len(prompt['untrusted_sources']),len(self.views))
        self.assertLessEqual(len(canonical(prompt))+len(canonical(self.schema)),MAX_CONTEXT_CHARACTERS)

    def test_real_adapter_schema_transport_once_without_actual_model_or_data_calls(self):
        from researchhelper.model import ModelService, ModelSettings
        from researchhelper.reasoning import model_object
        wire = dict(version=self.schema['properties']['version']['enum'][0],
            binding_ref=self.schema['properties']['binding_ref']['enum'][0],selections=[])
        response = dict(object='response',status='completed',error=None,incomplete_details=None,
            output=[dict(type='message',role='assistant',status='completed',
                         content=[dict(type='output_text',text=json.dumps(wire))])],usage={})
        task = copy.deepcopy(self.task)
        with tempfile.TemporaryDirectory() as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses',model='deepseek-flash',
                api_key='FAKE_OFFLINE_FEE',extraction_mode='direct'))
            with (patch.object(ModelService,'_request_bytes',return_value=json.dumps(response).encode()) as transport,
                  patch('socket.socket.connect',side_effect=AssertionError('Offline fee transport fixture'))):
                result, receipt = model_object(ModelService(settings,offline=False),task,'extract',self.payload,{'evidence_specs'})
        self.assertEqual(transport.call_count,1)
        self.assertEqual(result['evidence_specs'],[])
        self.assertEqual(receipt['original_period_selection_version'],1)
        self.assertTrue(task['ledger']['dispatches'][-1]['output_contract']['shape_accepted'])
