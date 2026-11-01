"""The fixed API receives program-assembled parameters, not model arrays."""
import copy
import json
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError
from researchhelper.evidence import data_request_contract,validate_source_request
from researchhelper.financial_sources import financial_request_parameters,source_request_fields_enabled
from researchhelper.output_schema import _planned_request_schemas,acquisition_output_schema,decode_acquisition_output
from researchhelper.engine import ResearchAgent

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'result/acceptance/one-sentence-research-dl0nuvxy/task.json'
INDICATORS=['ths_revenue_stock','ths_operating_cost_stock','ths_np_stock','ths_ncf_from_oa_stock']


class SourceRequestFieldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw=SOURCE.read_bytes();cls.original=json.loads(cls.raw)

    def setUp(self):
        self.task=copy.deepcopy(self.original);self.task['source_request_fields_version']=1
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No external I/O'))
        guard.start();self.addCleanup(guard.stop)

    def tearDown(self):self.assertEqual(SOURCE.read_bytes(),self.raw)

    def params(self,period='20260630'):
        return dict(codes='002594.SZ',report_period=period,statement_type='1',indicators=list(INDICATORS))

    def request(self,period):
        return dict(id='financial_'+period,source='ifind',operation='basic_data_service',
            parameters=self.params(period),question_ids=['q2','q3'],selection_reason='核对指定两期合并财务原值')

    def test_two_periods_compile_to_eight_ordered_vendor_cells(self):
        before=copy.deepcopy(self.task['ledger'])
        for period in ('20260630','20250630'):
            incoming=self.params(period);original=copy.deepcopy(incoming)
            canonical=validate_source_request(self.task,'ifind','basic_data_service',incoming)
            self.assertEqual(canonical,dict(codes='002594.SZ',indipara=[dict(indicator=field,
                indiparams=[period,'1']) for field in INDICATORS]))
            self.assertEqual(incoming,original)
        self.assertEqual(self.task['ledger'],before)

    def test_model_schema_uses_named_period_type_and_unique_indicators(self):
        contract=data_request_contract(self.task)
        tool=next(tool for tool in contract['tools'] if tool['operation']=='basic_data_service')
        fields=tool['metadata_parameter_schema']['properties']
        self.assertEqual(set(fields),{'codes','report_period','statement_type','indicators'})
        self.assertTrue(fields['indicators']['uniqueItems'])
        self.assertTrue(set(INDICATORS).issubset(fields['indicators']['items']['enum']))
        self.assertNotEqual(tool['indicator_meanings']['ths_np_stock']['metric'],
                            tool['indicator_meanings']['ths_np_atoopc_stock']['metric'])

    def test_empty_catalogue_alternatives_have_no_required_null_placeholder(self):
        contract=data_request_contract(self.task);self.assertEqual(contract['catalogue_alternatives'],[])
        branches=_planned_request_schemas(self.task,contract)
        reports=[branch for branch in branches if branch['properties']['operation']['enum']==['report_query']]
        self.assertTrue(reports)
        self.assertTrue(all('catalogue_alternative_ref' not in branch['properties'] for branch in reports))

    def test_old_task_keeps_its_original_wire_contract(self):
        old=copy.deepcopy(self.task);old.pop('source_request_fields_version')
        contract=data_request_contract(old)
        tool=next(tool for tool in contract['tools'] if tool['operation']=='basic_data_service')
        self.assertEqual(set(tool['metadata_parameter_schema']['properties']),{'codes','indipara'})
        branches=_planned_request_schemas(old,contract)
        reports=[branch for branch in branches if branch['properties']['operation']['enum']==['report_query']]
        self.assertTrue(all(branch['properties']['catalogue_alternative_ref']=={'type':'null'} for branch in reports))

    def test_duplicate_fields_missing_selection_or_unregistered_scope_is_rejected(self):
        for changed in (dict(indicators=[INDICATORS[0],INDICATORS[0]]),dict(indicators=['unknown_field']),
                        dict(report_period='2026H1'),dict(statement_type='999'),dict(codes='600519.SH'),
                        dict(report_period='20270630'),dict(statement_type=1)):
            incoming=dict(self.params(),**changed)
            with self.subTest(changed=changed),self.assertRaises(ContractError):
                validate_source_request(self.task,'ifind','basic_data_service',incoming)
        missing=self.params();missing.pop('statement_type')
        with self.assertRaises(ContractError):validate_source_request(self.task,'ifind','basic_data_service',missing)

    def test_canonical_program_calls_remain_valid_without_widening_the_api(self):
        incoming=dict(codes='002594.SZ',indipara=[dict(indicator=field,indiparams=['20260630','1'])
            for field in INDICATORS])
        self.assertEqual(validate_source_request(self.task,'ifind','basic_data_service',incoming),incoming)

    def test_navigation_and_supplement_preflight_use_the_same_compiler(self):
        needs=[self.request(period) for period in ('20260630','20250630')]
        description=dict(data_request_contract=data_request_contract(self.task))
        schema=acquisition_output_schema(self.task,description)
        wire=dict(version=1,binding_ref=schema['properties']['binding_ref']['enum'][0],
                  data_requests=needs,stop_reason='')
        decoded=decode_acquisition_output(wire,schema,description)
        prepared=ResearchAgent._validated_data_needs(self.task,decoded['data_requests'])
        self.assertEqual(len(prepared),2)
        self.assertEqual([item[3]['indipara'][0]['indiparams'] for item in prepared],
                         [['20260630','1'],['20250630','1']])
        self.assertEqual(len(self.task['evidence']),0)

    def test_unknown_version_is_not_interpreted_as_enabled(self):
        for version in (True,2,'1',1.0):
            with self.assertRaises(ContractError):source_request_fields_enabled(dict(self.task,source_request_fields_version=version))

    def test_explicit_legacy_route_does_not_gain_unqueried_fixed_fields(self):
        legacy=copy.deepcopy(self.task);legacy.pop('fixed_financial_catalog_version')
        self.assertFalse(source_request_fields_enabled(legacy))
        contract=data_request_contract(legacy)
        self.assertFalse(any(tool['operation']=='basic_data_service' for tool in contract['tools']))

    def test_actual_directory_request_does_not_need_an_irrelevant_null_field(self):
        record=next(record for record in self.task['source_records'] if record.get('operation')=='report_query')
        request=dict(id='directory_control',source='ifind',operation='report_query',
            parameters=copy.deepcopy(record['parameters']),question_ids=['q2'],selection_reason='取得指定财报目录')
        description=dict(data_request_contract=data_request_contract(self.task))
        schema=acquisition_output_schema(self.task,description)
        wire=dict(version=1,binding_ref=schema['properties']['binding_ref']['enum'][0],
                  data_requests=[request],stop_reason='')
        self.assertEqual(decode_acquisition_output(wire,schema,description)['data_requests'],[request])


if __name__=='__main__':unittest.main()
