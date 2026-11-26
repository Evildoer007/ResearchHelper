"""Current PE/PB observations use actual loopback transport, never vendor calls."""
import copy
import json
import unittest
from unittest.mock import patch
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.evidence import candidate_evidence, direct_code_reference
from tests import test_market_background_sources_offline as market


class CurrentMarketIndicatorTests(unittest.TestCase):
    def setUp(self):
        self.fixture = market.MarketBackgroundSourceTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.record, self.checked = self.fixture.approved_current('pe_ttm', 12.5)
        self.task, self.spec = self.fixture.current_task, self.fixture.current_spec

    def candidate(self, task=None, spec=None):
        return candidate_evidence(task or self.task, self.record, spec or self.spec)

    def test_current_ratio_has_two_receipts_without_fabricated_identity_dates(self):
        self.assertEqual(self.checked['value'], 12.5)
        self.assertEqual(self.checked['unit'], '倍')
        self.assertEqual(self.checked['source_check'], '通过')
        self.assertEqual(self.checked['applicability_check'], '通过')
        self.assertIsNone(self.checked['published_at'])
        self.assertNotIn('valid_from', self.checked['identity'])
        self.assertNotIn('valid_to', self.checked['identity'])
        self.assertEqual(self.task['evidence'], [])
        self.assertEqual(self.task['ledger']['used']['model_calls'], 0)

    def test_historical_task_cannot_relabel_current_ratio_as_past_information(self):
        historical = copy.deepcopy(self.task);historical['research_mode'] = 'historical'
        self.assertIsNone(direct_code_reference(historical, self.record))
        with self.assertRaises(ContractError):self.candidate(historical)

    def test_removed_explicit_code_cannot_reuse_the_original_identity_reference(self):
        changed = copy.deepcopy(self.task)
        changed['original_input']['objects'] = ['600001.SH']
        changed['original_input']['question'] = '核对另一只证券'
        self.assertIsNone(direct_code_reference(changed, self.record))
        with self.assertRaises(ContractError):self.candidate(changed)

    def test_missing_transport_or_sdk_send_receipt_cannot_become_a_current_observation(self):
        for key, value in (('network_worker_reaped', False), ('sdk_wire_requests_started', 0)):
            with self.subTest(key=key):
                changed = copy.deepcopy(self.task)
                changed['ledger']['dispatches'][0][key] = value
                self.assertIsNone(direct_code_reference(changed, self.record))
                with self.assertRaises(ContractError):self.candidate(changed)

    def test_ratio_cannot_be_relabelled_as_currency(self):
        wrong = copy.deepcopy(self.spec);wrong.update(raw_unit='元', unit='元')
        with self.assertRaises(ContractError):self.candidate(spec=wrong)
        self.assertEqual(self.spec['unit'], '倍')

    def test_original_date_or_value_change_invalidates_the_held_reference(self):
        for field, value in (('pe_ttm', 99), ('trade_date', '20260929')):
            with self.subTest(field=field):
                changed = copy.deepcopy(self.task)
                record = changed['source_records'][0]
                record['snapshot']['data'][0][field] = value
                record['hash'] = fingerprint(record['snapshot'])
                with self.assertRaises(ContractError):
                    candidate_evidence(changed, record, self.spec)

    def test_new_indicator_policy_expires_legacy_extraction_once_without_resetting_budget(self):
        from researchhelper import engine
        agent = self.fixture.current_agent
        task = self.task;task.update(status='运行中', run_mode='model', host_result={})
        agent.archive.save(task)
        class Host:
            def __init__(self):self.calls = 0
            def reason_phase(self, task, messages, phase):
                assert phase == 'extract'
                self.calls += 1
                return json.dumps({'evidence_specs': []})
        host = Host();agent.model = host;inputs = []
        def capture(value):
            if isinstance(value, dict) and 'current_market_observation_version' in value:
                inputs.append(copy.deepcopy(value))
            return fingerprint(value)
        ledger = copy.deepcopy(task['ledger'])
        with patch.object(agent, '_reasoning_available', return_value=True), \
             patch.object(agent, '_save_model_checkpoint'), patch.object(engine, 'fingerprint', side_effect=capture):
            agent._extract_model_sources(task)
            self.assertTrue(inputs)
            original = inputs[-1]
            previous = {key: value for key, value in original.items() if key != 'current_market_observation_version'}
            task['reasoning_state']['extraction']['input_digest'] = fingerprint(previous)
            agent._extract_model_sources(task)
            agent._extract_model_sources(task)
        self.assertEqual(host.calls, 2)
        self.assertEqual(task['ledger'], ledger)
        self.assertEqual(task['evidence'], [])
