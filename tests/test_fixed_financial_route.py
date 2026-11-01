"""Fixed financial APIs do not expose a duplicate semantic-finance route."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(os.environ.get('RESEARCHHELPER_IFIND_METADATA_RESOURCES',str(Path(__file__).resolve().parents[1])))
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.contracts import ContractError
from researchhelper.evidence import data_request_contract
from researchhelper.financial_sources import financial_query_shapes, validate_financial_acquisition_role
from researchhelper.planning import input_fingerprint

class FixedFinancialRouteTests(unittest.TestCase):
    def setUp(self):
        guard=patch('socket.socket.connect',side_effect=AssertionError('No network'))
        guard.start();self.addCleanup(guard.stop)
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.agent=ResearchAgent(ResearchArchive(folder.name),ROOT,offline=True,require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task=self.agent.create('研究600519.SH半年报盈利质量',research_mode='current')

    def test_default_current_route_is_bound_without_calls(self):
        self.assertEqual(self.task['fixed_financial_route_version'],1)
        old=copy.deepcopy(self.task);old.pop('fixed_financial_route_version')
        self.assertNotEqual(input_fingerprint(old),input_fingerprint(self.task))
        self.assertEqual(self.task['ledger']['used']['data_requests'],0)

    def test_no_duplicate_semantic_finance_route(self):
        for shape in financial_query_shapes(self.task):
            self.assertEqual(shape['available_codes'],[])
            self.assertNotIn('financial_batch',shape)
            self.assertEqual(shape['query_field_names'],[])
        self.assertTrue(any(t['operation']=='get_limit_policy' for t in data_request_contract(self.task)['tools']))

    def test_dispatch_cannot_bypass_route(self):
        need=dict(source='ifind',operation='smart_stock_picking',parameters=dict(
            searchstring='600519.SH2026年半年报营业收入、净利润',searchtype='stock'),
            question_ids=[self.task['questions'][0]['id']])
        with self.assertRaisesRegex(ContractError,'固定字段'):
            validate_financial_acquisition_role(self.task,need)
        self.assertEqual(self.task['ledger']['used']['data_requests'],0)

    def test_old_task_keeps_original_route(self):
        self.task.pop('fixed_financial_route_version')
        self.assertIn('600519.SH',financial_query_shapes(self.task)[0]['available_codes'])

    def test_historical_and_explicit_legacy_plans_are_not_upgraded(self):
        self.assertNotIn('fixed_financial_route_version',self.agent.create('历史研究',research_mode='historical'))
        self.assertNotIn('fixed_financial_route_version',self.agent.create('旧宿主',questions=[
            dict(id='q1',kind='fact',question='旧宿主问题',evidence_needs=['营业收入'])]))
