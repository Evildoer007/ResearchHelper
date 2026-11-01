"""Safe rejected-selection diagnostics and one budgeted model re-selection."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, charge, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.source_reader import ReadingSelectionError, binding, catalog, read

ROOT = Path(__file__).resolve().parents[1]


class ReadingSelectionRecoveryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='reading-selection-', dir=ROOT/'result/acceptance')
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(Path(folder.name)/'store'), ROOT)
        self.task = self.agent.create('核对原文及适用条件', objects=['合成材料'])
        self.task = self.agent.add_material(self.task['id'], 'fixture.txt',
            '发布日期：2026年7月19日。\n\n原文：生效日期2026年7月21日。风险：存在适用限制。'.encode())
        self.task.update(status='运行中', batch=1)
        self.agent.archive.save(self.task)
        self.source = self.task['materials'][0]
        self.valid = dict(artifact_id=self.source['id'], snapshot_hash=self.source['snapshot_hash'],
            queries=['生效','风险'], parts=[], reason='核对原文及限制', question_ids=['q1'])
        self.payload = dict(question=self.task['question'], questions=[{'id':'q1'}],
                            source_catalog=catalog(self.task), budget=self.task['ledger'])
        self.digest = fingerprint(dict(source=self.source['snapshot_hash'], question=self.task['input_hash']))
        self.original_input = self.task['input_hash']
        self.outputs, self.calls = [], []

    def model(self, model, task, phase, payload, required):
        self.calls.append(copy.deepcopy(payload))
        charge(task['ledger'], 'model_calls')
        task['ledger']['dispatches'].append(dict(kind='model_calls', research_phase=phase,
            batch=task['batch'], status='成功'))
        value = self.outputs.pop(0)
        if isinstance(value, Exception): raise value
        return copy.deepcopy(value), dict(phase=phase, context_hash=fingerprint(payload), synthetic=True)

    def select(self):
        with patch('researchhelper.engine.model_object', side_effect=self.model):
            return self.agent._select_model_reading(self.task, self.digest, self.payload, [self.source['id']])

    def unknown(self):
        return dict(self.valid, artifact_id='SECRET_LIKE_UNKNOWN_LOCATOR_NOT_TO_BE_RECORDED')

    def test_catalog_supplies_exact_detached_bindings_without_repair_or_source_approval(self):
        description = catalog(self.task)
        offered = description['items'][0]['request_binding']
        self.assertEqual(offered, {key:self.valid[key] for key in ('artifact_id','snapshot_hash')})
        offered['artifact_id'] = 'changed'
        self.assertEqual(catalog(self.task)['items'][0]['request_binding']['artifact_id'], self.source['id'])
        self.assertTrue(description['selection_contract']['one_request_per_original'])
        self.assertFalse(description['selection_contract']['automatic_repair'])

    def test_unknown_source_has_a_distinct_safe_rejection(self):
        with self.assertRaises(ReadingSelectionError) as caught:
            read(self.task, [self.unknown()], binding(self.task))
        self.assertEqual(caught.exception.diagnostic['code'], 'unknown_artifact')
        self.assertNotIn(self.unknown()['artifact_id'], str(caught.exception)+str(caught.exception.diagnostic))

    def test_duplicate_source_names_the_repeated_slot_not_private_values(self):
        with self.assertRaises(ReadingSelectionError) as caught:
            read(self.task, [self.valid,self.valid], binding(self.task))
        self.assertEqual(caught.exception.diagnostic['code'], 'duplicate_artifact')
        self.assertEqual(caught.exception.diagnostic['request_index'], 1)
        self.assertEqual(caught.exception.diagnostic['duplicate_of'], 0)

    def test_valid_first_selection_costs_one_call_and_approves_no_evidence(self):
        self.outputs = [{'requests':[self.valid]}]
        bundle = self.select()
        self.assertTrue(bundle['views'])
        self.assertEqual(self.task['ledger']['used']['model_calls'], 1)
        self.assertFalse(self.task['reasoning_state']['reading_rejections'])
        self.assertFalse(self.task['evidence'])

    def test_invalid_then_valid_selection_preserves_diagnostics_and_costs_two_calls(self):
        self.outputs = [{'requests':[self.unknown()]},{'requests':[self.valid]}]
        bundle = self.select()
        rejection = self.task['reasoning_state']['reading_rejections'][0]
        self.assertEqual(rejection['issue']['code'], 'unknown_artifact')
        self.assertNotIn(self.unknown()['artifact_id'], str(rejection))
        self.assertEqual(bundle['selection'], [self.valid])
        self.assertEqual(self.task['ledger']['used']['model_calls'], 2)
        self.assertEqual(self.task['ledger']['used']['data_requests'], 0)
        self.assertIn('selection_feedback', self.calls[1])
        self.assertEqual(self.task['input_hash'], self.original_input)
        self.assertFalse(self.task['ledger']['dispatches'][0]['source_selection_review']['selection_accepted'])
        self.assertTrue(self.task['ledger']['dispatches'][1]['source_selection_review']['selection_accepted'])
        self.assertFalse(self.task['evidence'])

    def test_duplicate_then_combined_selection_is_new_model_choice_not_json_merging(self):
        self.outputs = [{'requests':[self.valid,self.valid]},{'requests':[self.valid]}]
        self.select()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.task['reasoning_state']['reading_rejections'][0]['issue']['code'], 'duplicate_artifact')

    def test_second_rejection_stops_and_resume_cannot_reset_the_correction_slot(self):
        self.outputs = [{'requests':[self.unknown()]},{'requests':[self.unknown()]}]
        with self.assertRaises(ReadingSelectionError): self.select()
        spent = copy.deepcopy(self.task['ledger'])
        with self.assertRaisesRegex(ReadingSelectionError, '额度已用完'): self.select()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.task['ledger'], spent)
        self.assertNotIn('reading', self.task['reasoning_state'])

    def test_not_enough_calls_for_both_correction_and_extraction_means_no_retry(self):
        self.task['ledger']['limits']['model_calls'] = 3
        self.outputs = [{'requests':[self.unknown()]}]
        with self.assertRaises(ReadingSelectionError): self.select()
        self.assertEqual(len(self.calls), 1)

    def test_delivery_time_reserve_is_never_used_for_reselection(self):
        self.task['ledger']['used']['seconds'] = self.task['ledger']['limits']['seconds']-self.task['ledger']['reserve']['seconds']
        self.outputs = [{'requests':[self.unknown()]}]
        with self.assertRaises(ReadingSelectionError): self.select()
        self.assertEqual(len(self.calls), 1)

    def test_wrong_question_is_a_hard_scope_error_without_correction(self):
        self.outputs = [{'requests':[dict(self.valid, question_ids=['foreign_question'])]}]
        with self.assertRaisesRegex(ContractError, '越过'): self.select()
        self.assertEqual(len(self.calls), 1)

    def test_snapshot_mismatch_is_not_retried(self):
        self.outputs = [{'requests':[dict(self.valid, snapshot_hash='not_this_original')]}]
        with self.assertRaisesRegex(ContractError, '哈希'): self.select()
        self.assertEqual(len(self.calls), 1)

    def test_unknown_fields_cannot_be_repaired_or_approved(self):
        self.outputs = [{'requests':[dict(self.valid, approval=True)]}]
        with self.assertRaisesRegex(ContractError, '只接受'): self.select()
        self.assertEqual(len(self.calls), 1)

    def test_transport_failure_during_correction_preserves_the_spent_slot(self):
        self.outputs = [{'requests':[self.unknown()]}, ContractError('合成传输失败')]
        with self.assertRaisesRegex(ContractError, '传输失败'): self.select()
        self.assertTrue(self.task['reasoning_state']['reading_rejections'][0]['correction_reserved'])
        with self.assertRaisesRegex(ReadingSelectionError, '额度已用完'): self.select()
        self.assertEqual(len(self.calls), 2)

    def test_scope_binding_change_is_never_a_locator_correction(self):
        stale = dict(binding(self.task), research_version=999)
        with self.assertRaisesRegex(ContractError, '已过期'): read(self.task, [self.valid], stale)
        self.assertFalse(self.task['reasoning_state'] if 'reasoning_state' in self.task else {})

    def test_cancel_before_corrected_dispatch_keeps_the_booked_slot(self):
        self.outputs = [{'requests':[self.unknown()]}]
        checks = 0
        def active(*args):
            nonlocal checks
            checks += 1
            # Initial check, rejection checkpoint, pre-correction check,
            # booked-slot checkpoint, then the next dispatch boundary.
            if checks == 5: raise ContractError('合成取消信号')
            return self.task
        with patch.object(self.agent, '_active', side_effect=active):
            with self.assertRaisesRegex(ContractError, '取消'): self.select()
        self.assertEqual(len(self.calls), 1)
        with self.assertRaisesRegex(ReadingSelectionError, '额度已用完'): self.select()
        self.assertEqual(len(self.calls), 1)

    def test_actual_large_source_pipeline_reaches_extraction_once_after_selection_correction(self):
        self.task['status'] = '待开始'
        self.agent.archive.save(self.task)
        self.task = self.agent.add_material(self.task['id'], 'large_fixture.txt',
            ('背景不构成核验事实。'*12_000+'\n\n风险：存在适用限制。').encode())
        self.task.update(status='运行中', batch=1, host_result={})
        self.agent.archive.save(self.task)
        self.source = self.task['materials'][-1]
        self.valid.update(artifact_id=self.source['id'], snapshot_hash=self.source['snapshot_hash'], queries=['风险'])
        self.outputs = [{'requests':[self.unknown()]},{'requests':[self.valid]},{'evidence_specs':[]}]
        with patch('researchhelper.engine.model_object', side_effect=self.model):
            result = self.agent._extract_model_sources(self.task)
            self.agent._extract_model_sources(self.task)
        self.assertEqual(result['evidence_specs'], [])
        self.assertEqual(len(self.calls), 3)
        self.assertTrue(self.task['reasoning_state']['reading']['selection_correction_used'])
        self.assertIn('extraction', self.task['reasoning_state'])
        self.assertEqual(self.task['ledger']['used']['model_calls'], 3)
        self.assertEqual(self.task['ledger']['used']['data_requests'], 0)
        self.assertFalse(self.task['evidence'])


if __name__ == '__main__':
    unittest.main()
