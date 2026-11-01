"""Draft/App planning uses the same registered compiler as formal acquisition.

No production Store, paid endpoint or supplier transport. Existing tests stay
unchanged; invalid wire selections are rejected before any provider dispatch.
"""
import copy
from pathlib import Path
import json
import socket
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.draft_workflow import _plan, _planning_description, _acquire
from researchhelper.evidence import data_request_contract, validate_market_request
from researchhelper.market_series import validate_series_request, SERIES_FAMILIES, series_settings
from researchhelper.output_schema import (acquisition_output_schema, decode_acquisition_output,
    provider_request_schema, ACQUISITION_PROTOCOL, SERIES_PARAMETER_BINDING)
from researchhelper.model import ModelService, ModelSettings
from researchhelper.contracts import ContractError, canonical, charge


def request(task,code,field):
    return dict(id='selected_series',source='ifind',operation='date_sequence',
        parameters=dict(codes=code,startdate='2026-10-28',enddate='2026-10-28',
            functionpara=series_settings(task,code),indipara=[dict(indicator=field)]),
        question_ids=[task['questions'][0]['id']],selection_reason='用户明确要求的已登记指标')


class RegisteredModel(ModelService):
    """Keep the actual structured method, replace only the paid transport."""
    def __init__(self,selected):self.selected=selected;self.calls=[]
    def supports_structured_analysis(self):return True
    def reason(self,task,messages,**options):
        self.calls.append(options)
        assert options['phase']=='acquire'
        schema=options['output_schema'];description=options['schema_context']
        assert schema==acquisition_output_schema(task,description)
        payload=json.loads(messages[1]['content'])
        assert all('example' not in tool for tool in payload['data_request_contract']['tools'])
        wire=dict(version=1,binding_ref=payload['binding_ref'],data_requests=self.selected,stop_reason='')
        charge(task['ledger'],'model_calls')
        return canonical(decode_acquisition_output(wire,schema,description))


class DraftRegisteredPlanningTests(unittest.TestCase):
    def setUp(self):
        self.network=patch.object(socket.socket,'connect',side_effect=AssertionError('No paid calls'))
        self.network.start();self.addCleanup(self.network.stop)
        self.folder=tempfile.TemporaryDirectory(prefix='rh-registered-plan-');self.addCleanup(self.folder.cleanup)
        self.agent=ResearchAgent(ResearchArchive(self.folder.name),ROOT,offline=True,require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task=self.agent.create('比较600519.SH、510300.SH与000300.SH的登记指标',entry='混合',
            objects=['600519.SH','510300.SH','000300.SH'],research_mode='current',
            limits=dict(data_requests=12,model_calls=2,seconds=180))

    def schema(self):
        tools=data_request_contract(self.task)
        tools['tools']=[tool for tool in tools['tools'] if tool['source'] in ('ifind','official')]
        return _planning_description(self.task,tools)

    def test_draft_dispatches_one_registered_acquisition_and_keeps_delivery_reserve(self):
        model=RegisteredModel([request(self.task,'600519.SH','ths_pe_ttm_stock')]);self.agent.model=model
        selected=_plan(self.agent,self.task,1)
        self.assertEqual(len(model.calls),1)
        self.assertEqual(model.calls[0]['output_protocol'],ACQUISITION_PROTOCOL)
        self.assertEqual(self.task['ledger']['used']['model_calls'],1)
        self.assertEqual(self.task['ledger']['reserve']['model_calls'],1)
        self.assertEqual(selected[0]['parameters']['indipara'],[dict(indicator='ths_pe_ttm_stock',indiparams=['','100'])])
        validate_series_request(self.task,'date_sequence',selected[0]['parameters'])

    def test_one_sentence_code_needs_no_explicit_object_form(self):
        task=self.agent.create('研究贵州茅台600519.SH并取得PE(TTM)',research_mode='current')
        model=RegisteredModel([request(task,'600519.SH','ths_pe_ttm_stock')])
        selected=_plan(SimpleNamespace(model=model),task,1)
        validate_series_request(task,'date_sequence',selected[0]['parameters'])
        self.assertEqual(selected[0]['parameters']['codes'],'600519.SH')
        self.assertEqual(task['ledger']['used']['model_calls'],1)

    def test_all_registered_families_compile_their_locked_parameter_values(self):
        for family,code in [('个股','600519.SH'),('ETF','510300.SH'),('指数','000300.SH')]:
            for field,args in SERIES_FAMILIES[family].items():
                task=copy.deepcopy(self.task);model=RegisteredModel([request(task,code,field)])
                selected=_plan(SimpleNamespace(model=model),task,1)
                self.assertEqual(selected[0]['parameters']['indipara'][0]['indiparams'],args)
                validate_series_request(task,'date_sequence',selected[0]['parameters'])

    def test_scalar_map_empty_and_extra_argument_forms_cannot_reach_supplier(self):
        description,schema,_=self.schema();good=request(self.task,'600519.SH','ths_pe_ttm_stock')
        for bad in ('ths_pe_ttm_stock',{'indicator':'ths_pe_ttm_stock'},[],None,
                    [dict(indicator='ths_pe_ttm_stock',indiparams=[])],
                    [dict(indicator='invented_pe')]):
            proposal=copy.deepcopy(good);proposal['parameters']['indipara']=bad
            wire=dict(version=1,binding_ref=schema['properties']['binding_ref']['enum'][0],data_requests=[proposal],stop_reason='')
            with self.assertRaises(ContractError):decode_acquisition_output(wire,schema,description)
        self.assertEqual(self.task['ledger']['used']['data_requests'],0)

    def test_wrong_task_binding_or_question_never_becomes_a_request(self):
        description,schema,_=self.schema();good=request(self.task,'600519.SH','ths_pe_ttm_stock')
        for binding,question in [('different_task',good['question_ids']),
                                 (schema['properties']['binding_ref']['enum'][0],['other_question'])]:
            proposal=copy.deepcopy(good);proposal['question_ids']=question
            wire=dict(version=1,binding_ref=binding,data_requests=[proposal],stop_reason='')
            with self.assertRaises(ContractError):decode_acquisition_output(wire,schema,description)

    def test_wire_has_no_internal_compiler_keywords_and_prompt_does_not_repeat_examples(self):
        description,schema,prompt=self.schema();before=canonical(description)
        provider=provider_request_schema(self.task,schema,ACQUISITION_PROTOCOL)
        self.assertTrue(SERIES_PARAMETER_BINDING in canonical(schema))
        self.assertTrue(SERIES_PARAMETER_BINDING not in canonical(provider))
        self.assertTrue(all('example' not in tool and 'parameter_keys' not in tool for tool in prompt['tools']))
        self.assertEqual(canonical(description),before)

    def test_custom_non_structured_adapter_keeps_its_existing_json_channel(self):
        class Custom:
            def reason(inner,task,messages,**options):
                self.assertEqual(options['phase'],'draft_plan')
                return '{"data_requests":[]}'
        self.assertEqual(_plan(SimpleNamespace(model=Custom()),self.task,1),[])

    def test_actual_responses_transport_sends_one_schema_request_and_compiles_pe(self):
        description,schema,_=self.schema()
        wire=dict(version=1,binding_ref=schema['properties']['binding_ref']['enum'][0],
                  data_requests=[request(self.task,'600519.SH','ths_pe_ttm_stock')],stop_reason='')
        settings=ModelSettings(Path(self.folder.name)/'model')
        settings.save(dict(endpoint='https://api.deepseek.com/responses',model='deepseek-flash',
            api_key='UNIT_TEST_ONLY',reasoning_effort='low',extraction_mode='direct'))
        service=ModelService(settings,offline=False)
        payload=dict(object='response',status='completed',model='deepseek-flash',error=None,
            incomplete_details=None,output=[dict(type='message',role='assistant',status='completed',
                content=[dict(type='output_text',text=json.dumps(wire))])],
            usage=dict(input_tokens=10,output_tokens=30))
        with patch.object(service,'_request_bytes',return_value=json.dumps(payload).encode()) as transport:
            selected=_plan(SimpleNamespace(model=service),self.task,1)
        transport.assert_called_once()
        body=json.loads(transport.call_args.args[1].data)
        self.assertEqual(body['text']['format']['type'],'json_schema')
        self.assertTrue(body['text']['format']['strict'])
        self.assertNotIn('tools',body)
        self.assertEqual(self.task['ledger']['used']['model_calls'],1)
        self.assertEqual(selected[0]['parameters']['indipara'][0]['indiparams'],['','100'])
        validate_series_request(self.task,'date_sequence',selected[0]['parameters'])

    def test_name_or_macro_request_keeps_discovery_and_official_channels(self):
        task=self.agent.create('解释中国CPI变化',entry='混合',objects=['中国CPI'],research_mode='current')
        tools=data_request_contract(task)
        description,schema,prompt=_planning_description(task,tools)
        choices=schema['properties']['data_requests']['items']['anyOf']
        self.assertTrue(any(branch['properties']['source']['enum']==['official'] for branch in choices))
        self.assertEqual(description['data_request_contract']['callable_codes'],[])
        self.assertTrue(len(canonical(prompt))<len(canonical(tools)))

    def test_failed_optional_request_is_recorded_without_discarding_the_next(self):
        accepted=[]
        good=dict(id='price',source='ifind',operation='cmd_history_quotation',parameters=dict(codes='600519.SH',
            indicators='close',startdate='2026-10-28',enddate='2026-10-28',functionpara=dict(Currency='RMB',CPS='1',Fill='Omit')))
        bad=copy.deepcopy(good);bad['parameters']['indicators']='not_registered'
        def acquire(task,requests):
            for item in requests:
                validate_market_request(task,'ifind','cmd_history_quotation',item['parameters'])
                accepted.append(item)
        engine=SimpleNamespace(_check_run_control=lambda _:None,_acquire_sources=acquire)
        before=canonical([bad,good])
        with patch('researchhelper.draft_workflow._checkpoint'):_acquire(engine,self.task,[bad,good])
        self.assertEqual(len(accepted),1)
        self.assertEqual([r['status'] for r in self.task['draft_source_attempts']],['参数未通过','已提交'])
        self.assertEqual(canonical([bad,good]),before)
        self.assertEqual(self.task['ledger']['used']['model_calls'],0)


if __name__=='__main__':unittest.main()
