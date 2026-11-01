"""API-first availability, not automatic adoption or a mandatory request round."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.acquisition import original_navigation_tools
from researchhelper.contracts import ContractError
from researchhelper.evidence import data_request_contract
from researchhelper.financial_sources import (STRUCTURED_SOURCE_POLICY, financial_query_shapes,
    structured_source_preference, validate_financial_acquisition_role)
from researchhelper.output_schema import acquisition_output_schema
from researchhelper.planning import input_fingerprint, research_brief
from researchhelper.reports import compose


class StructuredSourcePriorityTests(unittest.TestCase):
    def setUp(self):
        guard = patch('socket.socket.connect', side_effect=AssertionError('Offline test only'))
        guard.start(); self.addCleanup(guard.stop)
        folder = tempfile.TemporaryDirectory(prefix='rh-structured-source-')
        self.addCleanup(folder.cleanup)
        resources = Path(os.environ.get('RESEARCHHELPER_IFIND_METADATA_RESOURCES',
            str(Path(__file__).resolve().parents[1])))
        self.agent = ResearchAgent(ResearchArchive(folder.name),resources,offline=True,require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task = self.agent.create('核对公司半年报营业收入与净利润', entry='个股',
            objects=['000166.SZ'],research_mode='current',questions=[dict(id='q1',kind='fact',
                question='核对公司半年报营业收入与净利润',evidence_needs=['营业收入','净利润'])])
        self.need = dict(id='financial_1',source='ifind',operation='smart_stock_picking',
            parameters=dict(searchstring='000166.SZ2026年半年报营业收入、净利润',searchtype='stock'),
            question_ids=['q1'],selection_reason='离线API路径检查')
        self.task[STRUCTURED_SOURCE_POLICY] = 1
        self.task['input_hash'] = input_fingerprint(self.task)

    def test_new_default_current_studies_enable_preference_without_requests(self):
        task = self.agent.create('研究比亚迪盈利质量', research_mode='current')
        self.assertEqual(task[STRUCTURED_SOURCE_POLICY], 1)
        self.assertEqual(task['ledger']['used']['data_requests'], 0)
        self.assertEqual(task['source_records'], [])

    def test_explicit_legacy_tasks_are_not_auto_upgraded(self):
        self.assertNotIn(STRUCTURED_SOURCE_POLICY, self.agent.create('旧宿主计划',
            research_mode='current', questions=[dict(id='q1',kind='fact',question='旧宿主计划',evidence_needs=['收入'])]))

    def test_policy_is_bound_to_input_and_frozen_in_report(self):
        self.agent._adopt_method_knowledge(self.task)
        self.task['gaps'] = ['离线检查未开展研究']
        self.assertEqual(compose(self.task)[STRUCTURED_SOURCE_POLICY], 1)
        old = copy.deepcopy(self.task); old.pop(STRUCTURED_SOURCE_POLICY)
        self.assertNotEqual(input_fingerprint(old), input_fingerprint(self.task))

    def test_planning_and_dispatch_share_the_same_preference(self):
        preference = structured_source_preference(self.task)
        self.assertEqual(data_request_contract(self.task)['source_preference'], preference)
        self.assertEqual(research_brief(self.task)['source_preference'], preference)
        self.assertFalse(preference['financial_approval'])
        self.assertFalse(preference['automatic_download'])
        self.assertFalse(preference['mandatory_api_round'])

    def test_numeric_questions_may_request_raw_financial_data_without_approval(self):
        need = copy.deepcopy(self.need)
        validate_financial_acquisition_role(self.task, need)
        self.agent._validated_data_needs(self.task, [need])
        shape = financial_query_shapes(self.task)[0]
        self.assertEqual(shape['raw_query_question_ids'], ['q1'])
        self.assertEqual(set(shape['available_codes']), set(self.task['objects']))
        self.assertTrue(shape['raw_only'])
        self.assertFalse(shape['numeric_evidence_registered'])
        self.assertEqual(self.task['evidence'], [])

    def test_unrelated_questions_cannot_authorize_financial_queries(self):
        for refs in ([], ['q_other']):
            with self.assertRaises(ContractError):
                validate_financial_acquisition_role(self.task, dict(self.need, question_ids=refs))

    def test_original_navigation_keeps_existing_metadata_and_basic_tools(self):
        tools = [dict(source='ifind',operation=operation) for operation in (
            'get_thscode','report_query','filing_original','get_limit_policy','get_indicators',
            'get_indicator_params','basic_data_service','smart_stock_picking','invented_fulltext_api')]
        kept = original_navigation_tools(self.task, tools)
        self.assertEqual({tool['operation'] for tool in kept}, {tool['operation'] for tool in tools[:-1]})
        self.assertEqual(len(tools), 9)

    def test_legacy_navigation_keeps_its_original_tool_boundary(self):
        old = copy.deepcopy(self.task); old.pop(STRUCTURED_SOURCE_POLICY)
        tools = [dict(source='ifind',operation=operation) for operation in (
            'get_indicators','basic_data_service','filing_original')]
        self.assertEqual(original_navigation_tools(old,tools), [tools[-1]])
        with self.assertRaises(ContractError): validate_financial_acquisition_role(old,self.need)

    def test_schema_keeps_financial_and_discovery_patterns_for_explicit_choice(self):
        contract = data_request_contract(self.task)
        schema = acquisition_output_schema(self.task,dict(data_request_contract=contract))
        smart = [branch for branch in schema['properties']['data_requests']['items']['anyOf']
            if branch['properties']['operation']['enum'] == ['smart_stock_picking']]
        patterns = smart[0]['properties']['parameters']['properties']['searchstring']['anyOf']
        self.assertTrue(any('半年报' in item.get('pattern','') for item in patterns))

    def test_no_preference_upgrade_for_historical_or_document_only_use(self):
        for change in (dict(research_mode='historical'),dict(intent='原件内容核对')):
            task = dict(self.task,**change)
            self.assertIsNone(structured_source_preference(task))

    def test_invalid_policy_is_rejected_not_treated_as_legacy(self):
        for value in (True,2,'1'):
            task = dict(self.task,**{STRUCTURED_SOURCE_POLICY:value})
            with self.assertRaises(ContractError): structured_source_preference(task)


if __name__ == '__main__':
    unittest.main()
