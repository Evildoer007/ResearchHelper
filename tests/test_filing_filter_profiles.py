"""Fund report discovery does not inherit an unregistered issuer filter."""
import copy
import json
import tempfile
import unittest

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import IFIND_REPORT_FIELDS, data_request_contract, validate_ifind_report_request
from researchhelper.output_schema import _matches, _planned_request_schemas
from researchhelper.planning import input_fingerprint


class FilingFilterProfileTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.agent=ResearchAgent(ResearchArchive(folder.name),ROOT,offline=True,require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task=self.agent.create('核对510300.SH、159919.SZ和600519.SH的正式报告')
        self.params=dict(codes='510300.SH',functionpara={},beginrDate='2026-07-01',
            endrDate='2026-10-01',outputpara=','.join(field+':Y' for field in IFIND_REPORT_FIELDS))

    def shape(self, params, task=None):
        request=dict(id='r1',source='ifind',operation='report_query',parameters=params,
            question_ids=[self.task['questions'][0]['id']],selection_reason='定位报告原件')
        return any(_matches(request,schema) for schema in _planned_request_schemas(task or self.task))

    def test_profiles_are_scoped_by_code_not_the_whole_task_entry(self):
        tool=next(item for item in data_request_contract(self.task)['tools'] if item['operation']=='report_query')
        profiles=tool['filing_filter_profiles']
        self.assertEqual(profiles[0]['available_codes'],['159919.SZ','510300.SH'])
        self.assertEqual(profiles[0]['functionpara_options'],[{}])
        self.assertEqual(profiles[1]['available_codes'],['600519.SH'])
        self.assertIn({'reportType':'901'},profiles[1]['functionpara_options'])
        self.assertTrue(all(item['routing_only'] and not item['financial_approval'] for item in profiles))

    def test_schema_and_validator_both_exclude_901_for_fund_namespace(self):
        for code in ('510300.SH','159919.SZ'):
            for filtering in ({'reportType':'901'},{'reportType':'901','keyWord':'报告'}):
                params=dict(self.params,codes=code,functionpara=filtering)
                with self.subTest(code=code,filtering=filtering):
                    self.assertFalse(self.shape(params))
                    with self.assertRaises(ContractError):validate_ifind_report_request(self.task,params)

    def test_untyped_and_explicit_title_remain_valid_without_automatic_rewrite(self):
        for filtering in ({},{'keyWord':'年度报告'}):
            params=dict(self.params,functionpara=filtering);before=copy.deepcopy(params)
            self.assertTrue(self.shape(params))
            validate_ifind_report_request(self.task,params)
            self.assertEqual(params,before)

    def test_registered_stock_filter_is_not_disabled_for_a_mixed_task(self):
        params=dict(self.params,codes='600519.SH',functionpara={'reportType':'901','keyWord':'半年报'})
        self.assertTrue(self.shape(params))
        validate_ifind_report_request(self.task,params)

    def test_unknown_code_multi_code_and_plate_scan_still_rejected(self):
        for changes in (dict(codes='510500.SH'),dict(codes='510300.SH,159919.SZ'),
                        dict(functionpara={'mode':'allFund'})):
            params=dict(self.params,**changes)
            self.assertFalse(self.shape(params))
            with self.assertRaises(ContractError):validate_ifind_report_request(self.task,params)

    def test_old_policy_keeps_both_filters_and_closed_failure_is_not_reclassified(self):
        legacy=copy.deepcopy(self.task);legacy.pop('filing_filter_profile_version')
        params=dict(self.params,functionpara={'reportType':'901'})
        self.assertTrue(self.shape(params,legacy))
        validate_ifind_report_request(legacy,params)
        path=ROOT/'result/acceptance/autonomous-data-phase-0_5yufc8/task.json'
        original=path.read_bytes();closed=json.loads(original)
        self.assertNotIn('filing_filter_profile_version',closed)
        self.assertEqual(closed['evidence'],[])
        self.assertEqual(path.read_bytes(),original)

    def test_policy_bound_and_not_applied_to_synthetic_or_document_tasks(self):
        self.assertEqual(self.task['filing_filter_profile_version'],1)
        legacy=copy.deepcopy(self.task);legacy.pop('filing_filter_profile_version')
        self.assertNotEqual(input_fingerprint(self.task),input_fingerprint(legacy))
        self.assertNotIn('filing_filter_profile_version',self.agent.create_case('mechanism'))
        self.assertNotIn('filing_filter_profile_version',self.agent.create('核对原文',intent='原件内容核对'))

    def test_returned_profiles_do_not_mutate_task_or_generic_registry(self):
        before=copy.deepcopy(self.task)
        tool=next(item for item in data_request_contract(self.task)['tools'] if item['operation']=='report_query')
        tool['filing_filter_profiles'][0]['functionpara_options'].append({'reportType':'901'})
        self.assertEqual(self.task,before)
        self.assertFalse(self.shape(dict(self.params,functionpara={'reportType':'901'})))
