"""Retained real observations; pure wire projections, no API or Store writes."""
import copy
import hashlib
import json
from pathlib import Path
import unittest
import threading
from researchhelper.contracts import ContractError
from researchhelper.contracts import canonical
from researchhelper.engine import ResearchAgent
from researchhelper.reasoning import analysis_contract, calculation_plan_contract, MAX_CONTEXT_CHARACTERS

ORIGINAL=Path(__file__).resolve().parents[1]/'result/acceptance/default-app-investment-35fx2htd/task.json'


class LongSeriesReasoningPayloadTests(unittest.TestCase):
    def setUp(self):
        self.digest=hashlib.sha256(ORIGINAL.read_bytes()).hexdigest()
        self.task=json.loads(ORIGINAL.read_text())

    def tearDown(self):
        self.assertEqual(hashlib.sha256(ORIGINAL.read_bytes()).hexdigest(),self.digest)

    def restored(self,payload):
        lookup={field:{entry['ref']:entry['value'] for entry in entries}
                for field,entries in payload['evidence_shared_metadata'].items()}
        records=copy.deepcopy(payload['evidence'])
        for record in records:
            for field,entries in lookup.items():
                if field+'_ref' in record:
                    record[field]=entries[record.pop(field+'_ref')]
        return records

    def test_complete_business_values_and_metadata_reconstruct_exactly(self):
        before=copy.deepcopy(self.task)
        ordinary=ResearchAgent._reasoning_evidence(self.task)
        projected=ResearchAgent._reasoning_evidence_payload(self.task)
        for left,right in zip(ordinary,self.restored(projected)):
            if 'calculation_context_basis' in left:
                left.pop('calculation_context_basis')
                reference=right.pop('calculation_basis_archive_ref')
                self.assertEqual(reference['evidence_id'],left['id'])
                self.assertEqual(reference['snapshot_hash'],left['snapshot_hash'])
            self.assertEqual(left,right)
        self.assertEqual(len(projected['evidence']),61)
        self.assertEqual(self.task,before)

    def test_full_analysis_and_binding_fit_without_increasing_context_limit(self):
        ordinary=ResearchAgent._reasoning_evidence(self.task)
        self.assertGreater(len(canonical(ordinary)),MAX_CONTEXT_CHARACTERS)
        projected=ResearchAgent._reasoning_evidence_payload(self.task)
        self.assertLess(len(canonical(dict(projected,analysis_contract=analysis_contract(self.task),
            questions=self.task['questions'],knowledge=self.task['knowledge']))),MAX_CONTEXT_CHARACTERS)
        self.assertLess(len(canonical(dict(projected,calculation_plan_contract=calculation_plan_contract(self.task),
            questions=self.task['questions']))),MAX_CONTEXT_CHARACTERS)

    def test_unknown_publication_and_action_limits_remain_available(self):
        records=self.restored(ResearchAgent._reasoning_evidence_payload(self.task))
        for record in records:
            self.assertEqual(record['publication']['certainty'],'未知')
            self.assertIsNone(record['published_at'])
            self.assertEqual(record['calculation_context']['corporate_action_status'],'unknown')

    def test_equal_looking_different_metadata_is_not_merged(self):
        self.task['evidence'][0]['publication']['basis']='不同的原件说明'
        projected=ResearchAgent._reasoning_evidence_payload(self.task)
        self.assertEqual(self.restored(projected)[0]['publication']['basis'],'不同的原件说明')
        self.assertEqual(projected['evidence'][0]['publication']['basis'],'不同的原件说明')

    def test_selection_does_not_leak_evidence_from_other_questions(self):
        projected=ResearchAgent._reasoning_evidence_payload(self.task,['q3'])
        self.assertEqual(projected['evidence'],[])
        self.assertEqual(projected['evidence_shared_metadata'],{})

    def test_projection_does_not_choose_inputs_approve_facts_or_change_ledger(self):
        before=copy.deepcopy(self.task['ledger'])
        result=ResearchAgent._reasoning_evidence_payload(self.task)
        self.assertFalse(result['evidence_projection']['financial_approval'])
        self.assertFalse(result['evidence_projection']['automatic_input_selection'])
        self.assertEqual(self.task['ledger'],before)

    def test_public_read_rejects_foreign_empty_and_duplicate_question_scope(self):
        agent=ResearchAgent.__new__(ResearchAgent);agent.lock=threading.RLock()
        agent._get=lambda ident:self.task
        for selected in ([],['foreign'],['q2','q2'],True):
            with self.subTest(selected=selected),self.assertRaises(ContractError):
                agent.reasoning_inputs(self.task['id'],selected)
        before=copy.deepcopy(self.task)
        self.assertEqual(agent.reasoning_inputs(self.task['id'],['q2']),
            ResearchAgent._reasoning_evidence_payload(self.task,['q2']))
        self.assertEqual(self.task,before)

    def test_projection_binds_task_version_batch_and_cutoff_without_reset(self):
        payload=ResearchAgent._reasoning_evidence_payload(self.task)
        binding=payload['evidence_projection']['binding']
        self.assertEqual(binding['id'],self.task['id'])
        self.assertEqual(binding['version'],self.task['version'])
        self.assertEqual(binding['batch'],self.task['batch'])
        self.assertEqual(binding['cutoff'],self.task['cutoff'])


if __name__=='__main__':
    unittest.main()
