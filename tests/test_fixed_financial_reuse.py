"""Versioned metadata reuse, never reuse another task's business data."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.contracts import ContractError
from researchhelper.financial_sources import (basic_financial_definitions, metadata_indicator_choices,
    metadata_request_shapes, validate_metadata_request)
from researchhelper.planning import input_fingerprint
from researchhelper.reports import compose


class FixedFinancialReuseTests(unittest.TestCase):
    def setUp(self):
        guard = patch('socket.socket.connect', side_effect=AssertionError('Offline only'))
        guard.start(); self.addCleanup(guard.stop)
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        resources = Path(os.environ.get('RESEARCHHELPER_IFIND_METADATA_RESOURCES',
            str(Path(__file__).resolve().parents[1])))
        self.agent = ResearchAgent(ResearchArchive(folder.name), resources, offline=True, require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task = self.agent.create('研究600519.SH半年报盈利质量', objects=['600519.SH'], research_mode='current')

    def test_new_policy_has_no_implicit_requests(self):
        self.assertEqual(self.task['fixed_financial_catalog_version'], 1)
        self.assertEqual(self.task['ledger']['used']['data_requests'], 0)
        self.assertEqual(self.task['source_records'], [])

    def test_policy_is_fingerprinted_and_frozen(self):
        old = copy.deepcopy(self.task); old.pop('fixed_financial_catalog_version')
        self.assertNotEqual(input_fingerprint(old), input_fingerprint(self.task))
        self.agent._adopt_method_knowledge(self.task)
        self.task['gaps'] = ['仅检查冻结版本，未开展研究']
        self.assertEqual(compose(self.task)['fixed_financial_catalog_version'], 1)

    def test_legacy_tasks_still_require_their_actual_dictionary(self):
        task = dict(self.task); task.pop('fixed_financial_catalog_version')
        self.assertEqual(basic_financial_definitions(task), {})
        self.assertEqual(metadata_indicator_choices(task), [])

    def test_known_five_field_parameters_are_reusable_not_business_values(self):
        definitions = basic_financial_definitions(self.task)
        self.assertEqual(len(definitions), 5)
        for value in definitions.values():
            self.assertEqual([row['name'] for row in value], ['D', 'C'])
            self.assertFalse(any('defaultValue' in row for row in value))
        self.assertEqual(self.task['evidence'], [])

    def test_account_policy_cannot_be_replaced_by_static_definitions(self):
        request = dict(codes='600519.SH', indipara=[dict(indicator='ths_revenue_stock', indiparams=['20260630','1'])])
        with self.assertRaisesRegex(ContractError, '实际账号策略'):
            validate_metadata_request(self.task, 'basic_data_service', request)
        self.assertEqual(self.task['ledger']['used']['data_requests'], 0)

    def test_first_shape_discloses_reuse_not_permission(self):
        shapes = metadata_request_shapes(self.task)
        self.assertEqual([row['operation'] for row in shapes], ['get_limit_policy'])
        self.assertEqual(len(shapes[0]['reusable_financial_fields']), 5)
        self.assertTrue(shapes[0]['raw_only'])

    def test_historical_and_document_tasks_do_not_gain_catalogue(self):
        for change in (dict(research_mode='historical'), dict(intent='原件内容核对')):
            self.assertEqual(basic_financial_definitions(dict(self.task, **change)), {})

    def test_invalid_versions_fail_before_retrieval(self):
        for value in (True, '1', 2):
            with self.assertRaises(ContractError):
                basic_financial_definitions(dict(self.task, fixed_financial_catalog_version=value))
