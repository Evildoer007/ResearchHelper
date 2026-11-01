"""Invalid proposals become explicit gaps only under the new task policy.

Run the real extraction orchestration with local boundary doubles, including
the actual interval-date validator. No model, network or prior study is used.
"""

import copy
import unittest
from unittest.mock import Mock, patch

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import validate_current_snapshot


class ExtractionReadFailureIsolationTests(unittest.TestCase):
    def setUp(self):
        self.agent = ResearchAgent.__new__(ResearchAgent)
        self.agent.model = object()
        self.agent._active = Mock()
        self.agent._reasoning_available = Mock(return_value=True)
        self.agent._save_model_checkpoint = Mock()
        self.agent._retain_original_selection_diagnostics = Mock()
        self.views = [dict(id='original', kind='api', source='fixture', operation='fixture')]
        self.task = dict(id='task', version=1, batch=1, input_hash='input', question='财务期间',
            intent='综合研究', objects=['600519.SH'], cutoff='2026-10-23',
            original_duration_anchor_version=1, extraction_read_isolation_version=1,
            evidence_gap_lifecycle_version=1, calculation_specs=[],
            questions=[dict(id='q1', required=True, method_needs=[])],
            source_records=[dict(id='original')], evidence=[dict(id='verified', source_check='通过')],
            ledger=dict(dispatches=[dict(kind='model_calls', status='成功')], used=dict(model_calls=1)),
            host_result=dict(evidence_specs=[dict(id='previously_verified_proposal')]), gaps=[])
        self.availability = dict(kind='current_original', artifact_id='original', snapshot_hash='snapshot')
        self.spec = dict(id='rejected_amount', artifact_id='original', availability=self.availability,
            observation_date='2026-06-30', period='2026年半年度', published_at=None,
            publication=dict(at=None, basis_ref=None, precision='未知', timezone='未知', certainty='未知',
                             basis='当前原件不证明历史可得'),
            record_locator=dict(period_part='第4页', period_quote='2026年6月30日'))
        self.proposed = dict(evidence_specs=[self.spec])
        digest = fingerprint(dict(input_hash='input', sources=self.views, reader_version=1))
        bundle = dict(views=self.views, reading_hash='reading', coverage=[], limitations=[])
        self.task['reasoning_state'] = dict(reading=dict(input_digest=digest, bundle=bundle))
        overrides = {
            'extractable_source_views': Mock(return_value=self.views),
            'context_sources': Mock(return_value=dict(mode='selected')),
            'question_specs': Mock(return_value=self.task['questions']),
            'validate_reading': Mock(),
            'evidence_contract': Mock(return_value=dict(calculation_input_requirements=dict(proposal_requirements=[]))),
            'model_object': Mock(return_value=(self.proposed, {})),
            'validate_model_specs': Mock(),
            'record_extraction_validation': Mock(),
        }
        for name, replacement in overrides.items():
            control = patch('researchhelper.engine.' + name, replacement)
            control.start()
            self.addCleanup(control.stop)
        self.reader = patch('researchhelper.engine.validate_read_specs', side_effect=self.validate_dates).start()
        self.addCleanup(patch.stopall)
        patch('researchhelper.evidence.current_original_reference', return_value=self.availability).start()
        patch('researchhelper.evidence._original_download_profile', return_value=dict(material=dict(parts=[
            dict(locator='第4页', text='报告期指2026年1月1日至2026年6月30日')]))).start()
        patch('socket.socket.connect', side_effect=AssertionError('No network')).start()

    @staticmethod
    def validate_dates(task, specs, bundle):
        for spec in specs:
            validate_current_snapshot(spec, task)

    def test_new_policy_records_gap_without_adopting_or_changing_prior_evidence(self):
        prior = copy.deepcopy((self.task['evidence'], self.task['ledger'], self.task['host_result'], self.proposed))
        result = self.agent._extract_model_sources(self.task)
        self.assertEqual(result['evidence_specs'], [])
        self.assertEqual(prior, (self.task['evidence'], self.task['ledger'], self.task['host_result'], self.proposed))
        self.assertIn('不能充当单日观测', self.task['gaps'][0])
        gap = self.task['gap_records'][0]
        self.assertEqual(gap['category'], 'evidence_extraction')
        self.assertEqual(gap['question_ids'], ['q1'])
        self.assertEqual(gap['failure_dispatch'], 0)
        rejected = self.task['reasoning_state']['rejected_extraction']
        self.assertEqual(rejected['proposed_specs'], self.proposed['evidence_specs'])
        self.assertFalse(rejected['specification_accepted'])
        self.assertFalse(rejected['automatic_repair'])
        self.assertNotIn('extraction', self.task['reasoning_state'])
        self.agent._save_model_checkpoint.assert_called_once()

    def test_legacy_missing_flag_keeps_strict_failure(self):
        self.task.pop('extraction_read_isolation_version')
        with self.assertRaisesRegex(ContractError, '不能充当单日观测'):
            self.agent._extract_model_sources(self.task)
        self.assertEqual(self.task['gaps'], [])
        self.assertFalse(self.task['reasoning_state']['rejected_extraction']['specification_accepted'])

    def test_boolean_is_not_a_policy_version(self):
        self.task['extraction_read_isolation_version'] = True
        with self.assertRaises(ContractError):
            self.agent._extract_model_sources(self.task)
        self.assertEqual(self.task['gaps'], [])

    def test_date_validation_stays_strict_after_isolation(self):
        self.agent._extract_model_sources(self.task)
        with self.assertRaisesRegex(ContractError, '不能充当单日观测'):
            validate_current_snapshot(self.spec, self.task)
        future = copy.deepcopy(self.spec)
        future['observation_date'] = '2026-12-31'
        with self.assertRaisesRegex(ContractError, '观测窗口后的数据'):
            validate_current_snapshot(future, self.task)

    def test_untrusted_approval_injection_is_discarded_not_repaired(self):
        with patch('researchhelper.engine.validate_model_specs', side_effect=ContractError('未知批准字段')):
            result = self.agent._extract_model_sources(self.task)
        self.assertEqual(result['evidence_specs'], [])
        self.assertIn('未知批准字段', self.task['gaps'][0])
        self.reader.assert_not_called()

    def test_system_error_from_validation_is_not_swallowed(self):
        self.reader.side_effect = OSError('disk read failed')
        with self.assertRaisesRegex(OSError, 'disk read failed'):
            self.agent._extract_model_sources(self.task)
        self.assertEqual(self.task['gaps'], [])
        self.assertNotIn('rejected_extraction', self.task['reasoning_state'])

    def test_checkpoint_failure_is_not_swallowed(self):
        self.agent._save_model_checkpoint.side_effect = OSError('disk write failed')
        with self.assertRaisesRegex(OSError, 'disk write failed'):
            self.agent._extract_model_sources(self.task)
        self.assertEqual(len(self.task['evidence']), 1)
