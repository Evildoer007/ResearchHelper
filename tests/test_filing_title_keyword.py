"""Explicit title filters narrow acquisition, never approve document relevance."""
import copy
import tempfile
import unittest

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import (IFIND_REPORT_FIELDS, data_request_contract,
    validate_source_request, snapshot_record)
from researchhelper.output_schema import _planned_request_schemas, _matches
from researchhelper.planning import input_fingerprint


class FilingTitleKeywordTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.agent = ResearchAgent(ResearchArchive(directory.name), ROOT, offline=True,
                                   require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task = self.agent.create('研究600519.SH2026年半年度报告的盈利质量', objects=['600519.SH'])
        self.params = dict(codes='600519.SH', functionpara={'keyWord': '半年度报告'},
            beginrDate='2026-08-01', endrDate='2026-08-31',
            outputpara=','.join(field + ':Y' for field in IFIND_REPORT_FIELDS))

    def validate(self, params=None, task=None):
        return validate_source_request(task or self.task, 'ifind', 'report_query', params or self.params)

    def test_new_formal_policy_bound_to_input(self):
        self.assertEqual(self.task['filing_keyword_filter_version'], 1)
        task = copy.deepcopy(self.task)
        task.pop('filing_keyword_filter_version')
        self.assertNotEqual(input_fingerprint(task), input_fingerprint(self.task))

    def test_exact_query_not_normalized(self):
        params = dict(self.params, functionpara={'keyWord': '半年度报告'})
        self.assertEqual(self.validate(params), params)
        self.assertEqual(params, self.params)

    def test_both_original_bases_plus_optional_keyword(self):
        for selected in ({}, {'reportType': '901'}, {'keyWord': '半年度报告'},
                         {'reportType': '901', 'keyWord': '半年度报告'}):
            self.validate(dict(self.params, functionpara=selected))

    def test_legacy_never_upgraded_silently(self):
        task = copy.deepcopy(self.task)
        task.pop('filing_keyword_filter_version')
        with self.assertRaises(ContractError):
            self.validate(task=task)
        self.validate(dict(self.params, functionpara={}), task)

    def test_invalid_keyword_not_repaired(self):
        for keyword in ('', ' 半年度报告', '半年度报告 ', '报\n告', '报\t告', '报\x7f告', '报' * 41, None, [], 1, True):
            with self.subTest(keyword=keyword), self.assertRaises(ContractError):
                self.validate(dict(self.params, functionpara={'keyWord': keyword}))

    def test_keyword_never_permits_unknown_filter_or_market_scan(self):
        for selected in ({'keyWord': '半年度报告', 'mode': 'allAStock'},
                         {'keyWord': '半年度报告', 'reportType': '999'},
                         {'keyword': '半年度报告'}, {'keyWord': '报告', 'page': '2'}):
            with self.subTest(selected=selected), self.assertRaises(ContractError):
                self.validate(dict(self.params, functionpara=selected))

    def test_object_dates_and_fields_still_bound(self):
        for change in (dict(codes='000300.SH'), dict(codes='600519.SH,600000.SH'),
                       dict(endrDate='2099-01-01'), dict(outputpara='reportTitle:Y')):
            with self.subTest(change=change), self.assertRaises(ContractError):
                self.validate(dict(self.params, **change))

    def test_contract_and_strict_schema_agree(self):
        tool = next(row for row in data_request_contract(self.task)['tools'] if row['operation'] == 'report_query')
        self.assertEqual(tool['parameter_options']['functionpara'], [{}, {'reportType': '901'}])
        self.assertEqual(tool['optional_title_filter']['parameter'], 'keyWord')
        choices = [row for row in _planned_request_schemas(self.task)
                   if row['properties']['operation']['enum'] == ['report_query']]
        request = dict(id='filing', source='ifind', operation='report_query', parameters=self.params,
                       question_ids=['q1'], selection_reason='按问题定位半年度报告')
        self.assertTrue(any(_matches(request, schema) for schema in choices))
        request['parameters'] = dict(self.params, functionpara={'keyWord': '报告', 'mode': 'allAStock'})
        self.assertFalse(any(_matches(request, schema) for schema in choices))

    def test_task_contract_and_parameters_not_mutated(self):
        before, params = fingerprint(self.task), copy.deepcopy(self.params)
        self.validate()
        data_request_contract(self.task)
        _planned_request_schemas(self.task)
        self.assertEqual(fingerprint(self.task), before)
        self.assertEqual(self.params, params)

    def test_empty_response_preserves_exact_filter_no_relevance_approval(self):
        source = snapshot_record(self.task, 'ifind', 'report_query', self.params, dict(errorcode=0, tables=[]))
        self.assertEqual(source['status'], '成功但为空')
        self.assertEqual(source['catalog_coverage']['requested']['functionpara'], {'keyWord': '半年度报告'})
        self.assertEqual(source['source_check'], '待核验')

    def test_offline_refuses_without_charge(self):
        before = copy.deepcopy(self.task['ledger'])
        with self.assertRaisesRegex(ContractError, '离线'):
            self.agent.collector.external(self.task, 'ifind', 'report_query', self.params, {})
        self.assertEqual(self.task['ledger'], before)
