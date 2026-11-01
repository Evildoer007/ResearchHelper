"""Offline detached R160 replay; no old task writes, network or model calls."""
import copy
import unittest
from unittest.mock import patch

from researchhelper import acquisition as acquisition
from researchhelper.evidence import data_request_contract
from researchhelper.financial_sources import financial_evidence_path
try:
    from .test_fixed_financial_context_inheritance import retained_task
except ImportError:
    from test_fixed_financial_context_inheritance import retained_task


class StructuredFinancialNoPdfObligationTests(unittest.TestCase):
    def setUp(self):
        self.task = copy.deepcopy(retained_task())
        self.task['fixed_financial_context_version'] = 1
        self.task['materials'] = []
        self.task['source_records'] = [r for r in self.task['source_records'] if r.get('kind') != 'filing']
        guard = patch('socket.socket.connect', side_effect=AssertionError('Offline replay'))
        guard.start()
        self.addCleanup(guard.stop)

    def covered(self):
        return acquisition._financial_api_covered_questions(self.task, acquisition.extractable_source_views(self.task))

    def before_api(self):
        self.task['source_records'] = [r for r in self.task['source_records'] if r.get('operation') != 'basic_data_service']

    def test_real_three_cells_remove_directory_obligation_without_approval(self):
        before = copy.deepcopy(self.task)
        self.assertEqual(self.covered(), {'q1'})
        coverage = acquisition.initial_acquisition_coverage(self.task)
        self.assertEqual(coverage['pending_original_question_ids'], [])
        self.assertEqual(coverage['pending_method_inputs'], [])
        self.assertFalse(coverage['financial_approval'])
        self.assertEqual(self.task, before)

    def test_first_real_navigation_contract_offers_api_not_pdf(self):
        self.before_api()
        tools = acquisition._structured_navigation_tools(self.task, data_request_contract(self.task)['tools'])
        operations = {tool['operation'] for tool in tools}
        self.assertIn('basic_data_service', operations)
        self.assertNotIn('filing_original', operations)
        self.assertIn('report_query', operations)

    def test_legacy_and_boolean_preserve_pdf(self):
        for flag in (None, True, 2):
            self.task['fixed_financial_context_version'] = flag
            self.assertEqual(self.covered(), set())
            tools = [{'operation': 'filing_original'}]
            self.assertEqual(acquisition._structured_navigation_tools(self.task, tools), tools)
            self.assertEqual(financial_evidence_path(self.task)['version'], 1)

    def test_text_need_keeps_original(self):
        q = self.task['questions'][0]
        q['evidence_need_constraints'][q['evidence_needs'][0]]['value_kind'] = 'text'
        self.assertEqual(self.covered(), set())
        self.before_api()
        self.assertEqual(acquisition._financial_api_first_questions(self.task), set())

    def test_explicit_original_and_explanation_keep_original(self):
        original = self.task['questions'][0]['question']
        for addition in ('并核对原文', '并解释原因', '并核对公告内容', '母公司报表', '下载PDF原件', '核对原件'):
            self.task['questions'][0]['question'] = original + addition
            self.assertEqual(self.covered(), set())

    def test_wrong_entity_or_period_no_waiver(self):
        original = self.task['questions'][0]['question']
        for text in (original.replace('600519.SH', '000001.SZ'), original.replace('2026年上半年', '2025年上半年')):
            self.task['questions'][0]['question'] = text
            self.assertEqual(self.covered(), set())
            self.assertEqual(acquisition._financial_api_first_questions(self.task), set())

    def test_user_original_request_cannot_be_erased_by_decomposition(self):
        for request in ('下载PdF并核对数值', '下载原件并核对数值'):
            self.task['question'] = request
            self.assertEqual(self.covered(), set())
            self.assertEqual(acquisition._financial_api_first_questions(self.task), set())

    def test_missing_definitions_falls_back_to_pdf(self):
        with patch('researchhelper.evidence._controlled_financial_definitions', return_value={}):
            self.assertEqual(self.covered(), set())
            self.before_api()
            self.assertEqual(acquisition._financial_api_first_questions(self.task), set())

    def test_attempted_incomplete_api_allows_pdf(self):
        record = next(r for r in self.task['source_records'] if r.get('operation') == 'basic_data_service')
        record['fixed_financial_catalog_digest'] = 'invalid'
        self.assertEqual(self.covered(), set())
        tools = [{'operation': 'filing_original'}]
        self.assertEqual(acquisition._structured_navigation_tools(self.task, tools), tools)

    def test_cross_task_response_cannot_cover(self):
        for record in self.task['source_records']:
            if record.get('operation') == 'basic_data_service': record['task_id'] = 'foreign'
        self.assertEqual(self.covered(), set())

    def test_extra_uncovered_numeric_need_retains_obligation(self):
        q = self.task['questions'][0]
        q['evidence_needs'].append('额外金额')
        q['evidence_need_constraints']['额外金额'] = copy.deepcopy(next(iter(q['evidence_need_constraints'].values())))
        self.assertEqual(self.covered(), set())
        self.before_api()
        self.assertEqual(acquisition._financial_api_first_questions(self.task), set())

    def test_current_descriptor_does_not_change_legacy_protocol(self):
        self.assertEqual(financial_evidence_path()['version'], 1)
        self.assertFalse(financial_evidence_path()['raw_api_numeric_evidence_registered'])
        self.assertIn('basic_data_service', financial_evidence_path(self.task)['structured_operations'])
