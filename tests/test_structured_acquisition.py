"""Actual strict transport, filtered tools, explicit profiles and no repair."""
import copy
import json
from unittest.mock import patch
import unittest

from tests import test_planning_identity_profile as fixture
from researchhelper.contracts import ContractError
from researchhelper.evidence import data_request_contract, identity_query_parameters
from researchhelper.output_schema import (acquisition_output_schema, decode_acquisition_output,
    ACQUISITION_PROTOCOL, ACQUISITION_FUNCTION_NAME, _matches)
from researchhelper.reasoning import model_object, PHASE_FIELDS


class StructuredAcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.base = fixture.PlanningIdentityProfileTests()
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.task, self.service = self.base.task, self.base.service
        contract = data_request_contract(self.task)
        contract['tools'] = [tool for tool in contract['tools'] if tool['operation'] == 'get_thscode']
        self.description = dict(data_request_contract=contract)
        self.schema = acquisition_output_schema(self.task, self.description)
        self.wire = dict(version=1, binding_ref=self.schema['properties']['binding_ref']['enum'][0],
                         data_requests=[copy.deepcopy(self.base.request)], stop_reason='')

    def decode(self, wire=None):
        return decode_acquisition_output(wire or self.wire, self.schema, self.description)

    def test_explicit_profile_decodes_to_existing_exact_request(self):
        before = copy.deepcopy(self.wire)
        actual = self.decode()
        self.assertEqual(actual['data_requests'][0]['parameters'], identity_query_parameters('secname', '贵州茅台', profile=fixture.IDENTITY_CANDIDATE_PROFILE))
        self.assertEqual(self.wire, before)

    def test_filtered_out_tools_cannot_return(self):
        wire = copy.deepcopy(self.wire)
        wire['data_requests'][0]['operation'] = 'smart_stock_picking'
        with self.assertRaises(ContractError): self.decode(wire)

    def test_unknown_question_binding_and_extra_fields_not_repaired(self):
        for case in ('question', 'binding', 'extra'):
            wire = copy.deepcopy(self.wire)
            if case == 'question': wire['data_requests'][0]['question_ids'] = ['unknown']
            if case == 'binding': wire['binding_ref'] = 'wrong'
            if case == 'extra': wire['data_requests'][0]['parameters']['functionpara'] = {}
            with self.subTest(case=case), self.assertRaises(ContractError): self.decode(wire)

    def test_stop_and_request_are_mutually_exclusive(self):
        for wire in (dict(self.wire, stop_reason='停止'), dict(self.wire, data_requests=[])):
            with self.assertRaises(ContractError): self.decode(wire)
        result = self.decode(dict(self.wire, data_requests=[], stop_reason='实际来源缺失'))
        self.assertEqual(result, dict(data_requests=[], stop_reason='实际来源缺失'))

    def test_source_scope_change_changes_binding(self):
        description = copy.deepcopy(self.description)
        description['data_request_contract']['question_ids'] = ['other']
        schema = acquisition_output_schema(self.task, description)
        with self.assertRaises(ContractError): decode_acquisition_output(self.wire, schema, description)

    def test_semantic_financial_search_uses_registered_syntax_and_actual_codes(self):
        # This legacy route remains readable, but is not the default fixed API.
        task = self.base.agent.create('研究贵州茅台600519.SH半年报盈利质量', objects=['600519.SH'])
        task.pop('fixed_financial_route_version', None)
        task.pop('financial_acquisition_route_version', None)
        contract = data_request_contract(task)
        contract['tools'] = [tool for tool in contract['tools'] if tool['operation'] == 'smart_stock_picking']
        description = dict(data_request_contract=contract)
        schema = acquisition_output_schema(task, description)
        selection = dict(query_kind='financial_single', code='600519.SH', year=2026,
                         report_period='半年报', fields=['营业收入', '净利润'])
        request = dict(id='financial', source='ifind', operation='smart_stock_picking', question_ids=['q1'],
                       selection_reason='核对原始数据', parameters=selection)
        wire = dict(version=1, binding_ref=schema['properties']['binding_ref']['enum'][0],
                    data_requests=[request], stop_reason='')
        self.assertTrue(_matches(wire, schema))
        result = decode_acquisition_output(wire, schema, description)
        self.assertEqual(result['data_requests'][0]['parameters'], dict(searchtype='stock',
                         searchstring='600519.SH2026年半年报营业收入、净利润'))
        for invalid in (dict(selection, code='贵州茅台'), dict(selection, code='600000.SH'),
                        dict(selection, report_period='H1'), dict(selection, fields=['自由现金流'])):
            candidate = copy.deepcopy(wire)
            candidate['data_requests'][0]['parameters'] = invalid
            self.assertFalse(_matches(candidate, schema), invalid)

    def test_fixed_financial_default_does_not_advertise_the_legacy_semantic_detour(self):
        from researchhelper.financial_sources import financial_query_shapes
        task = self.base.agent.create('研究贵州茅台600519.SH盈利质量', objects=['600519.SH'], research_mode='current')
        self.assertEqual(task['fixed_financial_route_version'], 1)
        for shape in financial_query_shapes(task):
            self.assertEqual(shape['available_codes'], [])
            self.assertEqual(shape['query_field_names'], [])
            self.assertNotIn('financial_batch', shape)
            self.assertEqual(shape['model_request_encoding']['kinds'], ['object_discovery'])
        contract = data_request_contract(task)
        self.assertTrue(any(tool['operation'] == 'get_limit_policy' for tool in contract['tools']))

    def test_actual_transport_charges_once_and_decodes_only_registered_profile(self):
        reply = dict(model='deepseek-flash', choices=[dict(index=0, finish_reason='tool_calls',
            message=dict(role='assistant', content=None, tool_calls=[dict(id='synthetic', type='function',
                function=dict(name=ACQUISITION_FUNCTION_NAME, arguments=json.dumps(self.wire)))]))])
        with patch.object(self.service, '_request_bytes', return_value=json.dumps(reply).encode()) as transport:
            result = json.loads(self.service.reason_structured_acquisition(self.task, [], self.description))
        body = json.loads(transport.call_args.args[1].data)
        self.assertEqual(body['tools'][0]['function']['name'], ACQUISITION_FUNCTION_NAME)
        self.assertEqual(result, self.decode())
        self.assertEqual(self.task['ledger']['used']['model_calls'], 1)
        self.assertEqual(self.task['ledger']['dispatches'][0]['output_protocol'], ACQUISITION_PROTOCOL)

    def test_model_object_selects_acquisition_not_analysis_or_plain_json(self):
        with patch.object(self.service, 'reason_structured_acquisition', return_value=json.dumps(self.decode())) as submit:
            result, receipt = model_object(self.service, self.task, 'acquire', self.description, PHASE_FIELDS['acquire'])
        self.assertEqual(submit.call_count, 1)
        self.assertEqual(result, self.decode())
        self.assertEqual(receipt['output_protocol'], ACQUISITION_PROTOCOL)
        self.assertIn(ACQUISITION_FUNCTION_NAME, json.dumps(submit.call_args.args[1], ensure_ascii=False))

    def test_legacy_task_keeps_plain_acquisition(self):
        self.task.pop('acquisition_request_encoding')
        with patch.object(self.service, 'reason_structured_acquisition') as strict, \
                patch.object(self.service, 'reason_phase', return_value=json.dumps(dict(data_requests=[], stop_reason='停止'))) as plain:
            model_object(self.service, self.task, 'acquire', self.description, PHASE_FIELDS['acquire'])
        strict.assert_not_called()
        plain.assert_called_once()

    def test_current_planning_v3_still_decodes_the_exact_identity_profile(self):
        from researchhelper.output_schema import planning_output_schema, decode_planning_output
        schema = planning_output_schema(self.task)
        self.assertEqual(schema['properties']['version']['enum'], [3])
        wire = dict(version=3, main_question=copy.deepcopy(self.base.wire['main_question']),
                    additional_questions=[], data_requests=[copy.deepcopy(self.base.request)], calculations=[])
        result = decode_planning_output(wire, schema)
        self.assertEqual(result['data_requests'][0]['parameters'], identity_query_parameters('secname', '贵州茅台', profile=fixture.IDENTITY_CANDIDATE_PROFILE))
