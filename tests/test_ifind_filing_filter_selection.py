"""Explicit filing filters without market scans, widening windows or approval."""
import copy
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if importlib.util.find_spec('researchhelper') is None:
    from core.bootstrap import configure_imports
    configure_imports()
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.contracts import ContractError
from researchhelper.evidence import (validate_ifind_report_request, data_request_contract,
    IFIND_REPORT_FIELDS, FILING_REQUEST_REGISTRY)


class FilingFilterSelectionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.agent = ResearchAgent(ResearchArchive(directory.name), ROOT, offline=True)
        self.task = self.agent.create('核对510300.SH的公告目录，不作正文或投资判断', entry='ETF',
            objects=['510300.SH'], cutoff='2026-10-13T18:00:00+08:00')
        self.params = dict(codes='510300.SH', functionpara={}, beginrDate='2026-10-01', endrDate='2026-10-13',
            outputpara=','.join(field+':Y' for field in IFIND_REPORT_FIELDS))

    def test_both_documented_explicit_filters_are_allowed(self):
        for selected in ({}, {'reportType': '901'}):
            with self.subTest(selected=selected):
                params = dict(self.params, functionpara=selected)
                self.assertEqual(validate_ifind_report_request(self.task, params), ['2026-10-01', '2026-10-13'])

    def test_missing_functionpara_is_not_implicitly_repaired(self):
        self.params.pop('functionpara')
        with self.assertRaises(ContractError):
            validate_ifind_report_request(self.task, self.params)

    def test_no_arbitrary_type_plate_scanning_or_scalar_filter(self):
        for selected in ({'reportType':'999'}, {'mode':'allFund'}, {'mode':'allAStock'},
                         {'reportType':'901','mode':'allHKStock'}, None, [], ''):
            with self.subTest(selected=selected):
                with self.assertRaises(ContractError):
                    validate_ifind_report_request(self.task, dict(self.params, functionpara=selected))

    def test_single_object_and_cutoff_still_apply(self):
        for changes in ({'codes':'600000.SH'}, {'codes':'510300.SH,510500.SH'},
                        {'endrDate':'2026-10-14'}, {'beginrDate':'2026-10-14'}, {'outputpara':'thscode:Y'}):
            with self.subTest(changes=changes):
                with self.assertRaises(ContractError):
                    validate_ifind_report_request(self.task, dict(self.params, **changes))

    def test_contract_does_not_claim_functionpara_is_fixed(self):
        shape = next(item for item in data_request_contract(self.task)['tools'] if item['operation']=='report_query')
        self.assertNotIn('functionpara', shape['fixed_parameters'])
        self.assertEqual(shape['parameter_options']['functionpara'], [{}, {'reportType':'901'}])
        self.assertEqual(shape['example']['parameters']['functionpara'], {})
        validate_ifind_report_request(self.task, shape['example']['parameters'])

    def test_registry_and_task_are_not_changed_by_selection(self):
        before, registry = copy.deepcopy(self.task), copy.deepcopy(FILING_REQUEST_REGISTRY)
        contract = data_request_contract(self.task)
        next(item for item in contract['tools'] if item['operation']=='report_query')['parameter_options']['functionpara'].clear()
        self.assertEqual(self.task, before)
        self.assertEqual(FILING_REQUEST_REGISTRY, registry)

    def test_offline_execution_still_blocks_both_filters_before_charging(self):
        before = copy.deepcopy(self.task['ledger'])
        for selected in ({}, {'reportType':'901'}):
            with self.assertRaisesRegex(ContractError, '离线'):
                self.agent.collector.external(self.task, 'ifind', 'report_query',
                    dict(self.params, functionpara=selected), {})
        self.assertEqual(self.task['ledger'], before)


if __name__ == '__main__':
    unittest.main()
