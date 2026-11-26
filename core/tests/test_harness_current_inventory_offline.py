"""Current executable inventory and version invariants, not supplier permissions.

Checks cover every registered family and the task-applicable planning subset.
Neither adapter registration nor documentation grants supplier permission or
approves a source as financial evidence.
"""
import copy
import socket
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, METHOD_REGISTRY, budget, fingerprint
from researchhelper.evidence import (
    CAPABILITIES, FIELD_REGISTRY, FILING_REQUEST_REGISTRY,
    IDENTITY_REQUEST_REGISTRY, active_source_capabilities, data_request_contract,
    validate_ifind_report_request,
)
from researchhelper import evidence as adapters
from researchhelper.economic_sources import ECONOMIC_REQUEST_REGISTRY
from researchhelper.financial_sources import FINANCIAL_REQUEST_REGISTRY
from researchhelper.knowledge import ENTRIES, RETAINED_VERSIONS, ResearchKnowledge
from tests import test_knowledge_market_interfaces_offline as app_fixture


class CurrentHarnessInventoryTests(unittest.TestCase):
    def setUp(self):
        connect = socket.socket.connect
        def local_only(connection, address):
            if isinstance(address, tuple) and address[0] not in ('127.0.0.1', 'localhost', '::1'):
                raise AssertionError('External network forbidden')
            return connect(connection, address)
        network = patch('socket.socket.connect', local_only)
        network.start()
        self.addCleanup(network.stop)

    @staticmethod
    def task(code='600000.SH'):
        question = '核对' + code + '原件，不推断收益'
        return {'id': 'current-inventory', 'version': 1, 'question': question,
                'objects': [code], 'cutoff': '2026-10-01T18:00:00+08:00',
                'input_hash': fingerprint(question), 'questions': [{'id': 'q1'}],
                'ledger': budget(), 'source_records': [], 'materials': []}

    def test_public_and_planning_operations_equal_all_five_registries(self):
        expected = set().union(*(getattr(adapters, name) for name in (
            'FIELD_REGISTRY', 'IDENTITY_REQUEST_REGISTRY', 'FILING_REQUEST_REGISTRY',
            'FINANCIAL_REQUEST_REGISTRY', 'ECONOMIC_REQUEST_REGISTRY', 'SERIES_REQUEST_REGISTRY',
            'INDEX_REQUEST_REGISTRY', 'FINANCIAL_QUERY_REGISTRY', 'IFIND_METADATA_REGISTRY',
            'CALENDAR_REQUEST_REGISTRY', 'OFFICIAL_REQUEST_REGISTRY')))
        task = self.task()
        before = fingerprint(task)
        public = [source + '.' + operation['operation']
                  for source, capability in active_source_capabilities().items()
                  for operation in capability['registered_operations']]
        planned = [tool['source'] + '.' + tool['operation']
                   for tool in data_request_contract(task)['tools']]
        self.assertEqual(set(public), expected)
        required = set().union(FIELD_REGISTRY, IDENTITY_REQUEST_REGISTRY,
                               FILING_REQUEST_REGISTRY, FINANCIAL_REQUEST_REGISTRY,
                               ECONOMIC_REQUEST_REGISTRY) - {'official.index_file', 'official.etf_pcf'}
        self.assertTrue(required.issubset(planned))
        self.assertTrue(set(planned).issubset(expected))
        self.assertEqual(len(public), len(expected))
        self.assertEqual(len(planned), len(set(planned)))
        self.assertIn('ifind.edb_service', expected)
        self.assertEqual(fingerprint(task), before)

    def test_hk_examples_use_ifind_but_never_mainland_tinyshare(self):
        task = self.task('00700.HK')
        before = fingerprint(task)
        tools = data_request_contract(task)['tools']
        for operation in ('cmd_history_quotation', 'report_query'):
            example = next(tool['example'] for tool in tools
                           if tool['source'] == 'ifind' and tool['operation'] == operation)
            self.assertEqual(example['parameters']['codes'], '00700.HK')
            self.assertEqual(example['question_ids'], ['q1'])
            self.assertIn('不是研究取数建议', example['selection_reason'])
            if operation == 'report_query':
                self.assertEqual(validate_ifind_report_request(task, example['parameters']),
                                 ['2026-10-01', '2026-10-01'])
                foreign = copy.deepcopy(example['parameters'])
                foreign['codes'] = '600000.SH'
                with self.assertRaises(ContractError):
                    validate_ifind_report_request(task, foreign)
                future = copy.deepcopy(example['parameters'])
                future['endrDate'] = '2026-10-02'
                with self.assertRaises(ContractError):
                    validate_ifind_report_request(task, future)
        self.assertTrue(all(tool['example'] is None for tool in tools
                            if tool['source'] == 'tinyshare'))
        self.assertEqual(fingerprint(task), before)

    def test_inventory_does_not_promote_permissions_or_documented_only_tools(self):
        inventory = active_source_capabilities()
        self.assertNotIn('wind', inventory)
        self.assertEqual(CAPABILITIES['wind']['registered_operations'], [])
        for source, capability in inventory.items():
            operations = capability['registered_operations']
            self.assertEqual(set(capability.get('endpoints', [])),
                             {item['operation'] for item in operations if not item['internal']})
            documented = {item['operation'] for item in capability.get('documented_only', [])}
            self.assertFalse(documented & {item['operation'] for item in operations})
            for item in operations:
                self.assertNotIn('approved', item)
                self.assertNotIn('access_token', item)
                self.assertNotIn('api_key', item)
        inventory['ifind']['registered_operations'].clear()
        self.assertTrue(active_source_capabilities()['ifind']['registered_operations'])
        implemented = {item['operation'] for item in CAPABILITIES['ifind']['registered_operations']}
        self.assertIn('basic_data_service', implemented)
        self.assertIn('basic_data_service', CAPABILITIES['ifind']['endpoints'])
        self.assertNotIn('basic_data_service', {item['operation'] for item in CAPABILITIES['ifind']['documented_only']})
        self.assertEqual(CAPABILITIES['ifind']['state'], '真实权限未验收')

    def test_current_knowledge_entries_match_their_exact_retained_versions(self):
        before = fingerprint(ENTRIES)
        self.assertEqual(len({entry['id'] for entry in ENTRIES}), len(ENTRIES))
        for entry in ENTRIES:
            with self.subTest(identifier=entry['id']):
                self.assertEqual(entry, RETAINED_VERSIONS[(entry['id'], entry['version'])])
                self.assertIn((entry['id'], 1), RETAINED_VERSIONS)
        knowledge = ResearchKnowledge()
        package = knowledge.read('混合', '现金流与基金费率', methods=tuple(METHOD_REGISTRY))
        knowledge.validate_package(package)
        for method, definition in METHOD_REGISTRY.items():
            self.assertIn(definition['knowledge_id'], package['index'])
            self.assertTrue(knowledge.method_binding(package, method))
        with self.assertRaises(ContractError):
            knowledge.publish(package)
        self.assertEqual(fingerprint(ENTRIES), before)

    def test_general_principles_have_sources_conditions_and_counterchannels(self):
        principles = [entry for entry in ENTRIES if entry.get('knowledge_role') == 'general_principle']
        self.assertTrue({'rh_k_cash_profit', 'rh_k_rate_transmission', 'rh_k_index_structure',
                         'rh_k_etf_exposure'}.issubset({entry['id'] for entry in principles}))
        for entry in principles:
            with self.subTest(identifier=entry['id']):
                self.assertIsNone(entry['publication_date'])
                self.assertIn('未知', entry['publication_date_status'])
                self.assertEqual(entry['access'], 'public')
                self.assertTrue(entry['source_reference']['sections'])
                self.assertTrue(entry['source_reference']['url'].startswith('https://'))
                self.assertTrue(entry['conditions'] and entry['counterexample']
                                and entry['verification_indicators'] and entry['dependencies'])
                self.assertIn('不是历史当时规则', entry['temporal_scope'])

    def test_required_financial_methods_retain_their_mechanism_dependencies(self):
        knowledge = ResearchKnowledge()
        for method in ('net_profit_margin_v1', 'operating_cash_profit_ratio_v1'):
            package = knowledge.read('个股', '公司研究', methods=[method])
            self.assertIn(METHOD_REGISTRY[method]['knowledge_id'], package['index'])
            self.assertIn('rh_k_cash_profit', package['index'])
            self.assertTrue(knowledge.method_binding(package, method))

    def test_authenticated_catalog_matches_current_store_without_task_adoption(self):
        fixture = app_fixture.KnowledgeMarketInterfacesTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        bootstrap = fixture.fixture.request('/api/bootstrap')[0]
        self.assertEqual({entry['id'] for entry in bootstrap['knowledge']},
                         {entry['id'] for entry in ResearchKnowledge().catalog()})
        self.assertEqual(bootstrap['field_registry']['tinyshare.daily_basic']['permission'], '未验收')
        task = fixture.app_task()
        self.assertEqual(task['status'], '部分完成', task.get('error'))
        self.assertIn('rh_k_cash_profit', task['knowledge']['index'])
        self.assertNotIn('rh_k_rate_transmission', task['knowledge']['index'])
        self.assertEqual(task['ledger']['used']['model_calls'], 0)
        self.assertEqual(task['ledger']['used']['data_requests'], 0)
        self.assertFalse(task['evidence'] or task['theses'])
