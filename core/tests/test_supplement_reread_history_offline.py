"""Reread candidates and actual registration history are distinct, without calls."""

import copy
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.reasoning import (PHASE_FIELDS, PHASE_RULES, RULES_VERSION, SUPPLEMENT_RULES_VERSION,
                                      model_object, supplement_reread_contract,
                                      supplement_reread_pairs, validate_supplement)
from tests import test_extraction_reread_workflow_offline as fixtures


class SupplementRereadHistoryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ExtractionRereadWorkflowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.agent, self.task = self.fixture.agent, self.fixture.task

    def test_initial_rejected_extraction_does_not_consume_pair(self):
        self.assertTrue(self.task["host_result"]["evidence_specs"])
        self.assertTrue(self.task["rejected_evidence"])
        self.assertEqual(supplement_reread_pairs(self.task), set())
        validate_supplement(self.task, self.fixture.proposal())

    def test_candidates_do_not_mutate_history_or_approve_source(self):
        before = fingerprint(self.task)
        options = self.agent._supplement_options(self.task)
        self.assertTrue(options["extraction_repairs"])
        contract = supplement_reread_contract(self.task)
        self.assertEqual(contract["registered_pairs"], [])
        self.assertFalse(contract["initial_extraction_counts_as_reread"])
        self.assertFalse(contract["repair_candidate_counts_as_reread"])
        self.assertEqual(fingerprint(self.task), before)
        self.assertFalse(self.task["evidence"])
        self.assertEqual(self.agent.model.calls, [])

    def test_registered_round_consumes_only_selected_pair(self):
        proposal = self.fixture.proposal()
        self.task["supplement_history"] = [{"proposal": proposal, "status": "已完成"}]
        expected = {(proposal["artifact_ids"][0], "rates")}
        self.assertEqual(supplement_reread_pairs(self.task), expected)
        self.assertEqual(supplement_reread_contract(self.task)["registered_pairs"], [
            {"artifact_id": proposal["artifact_ids"][0], "question_id": "rates"}])
        with self.assertRaisesRegex(ContractError, "没有新的重读增量"):
            validate_supplement(self.task, proposal)
        self.assertEqual(self.agent._supplement_extraction_repairs(self.task), [])

    def test_interrupted_registered_round_remains_consumed(self):
        proposal = self.fixture.proposal()
        self.task["supplement_history"] = [{"proposal": proposal, "status": "运行中"}]
        self.assertTrue(supplement_reread_pairs(self.task))
        with self.assertRaisesRegex(ContractError, "没有新的重读增量"):
            validate_supplement(self.task, proposal)

    def test_legacy_direct_proposal_has_same_deduplication(self):
        proposal = self.fixture.proposal()
        self.task["supplement_history"] = [copy.deepcopy(proposal)]
        self.assertEqual(self.agent._supplement_extraction_repairs(self.task), [])
        with self.assertRaisesRegex(ContractError, "没有新的重读增量"):
            validate_supplement(self.task, proposal)

    def test_other_question_registration_does_not_block_rates(self):
        proposal = self.fixture.proposal()
        proposal["question_ids"] = ["margin"]
        self.task["supplement_history"] = [{"proposal": proposal}]
        self.assertEqual(self.agent._supplement_extraction_repairs(self.task)[0]["question_ids"], ["rates"])
        validate_supplement(self.task, self.fixture.proposal())

    def test_history_is_deduplicated_and_stable(self):
        self.task["supplement_history"] = [
            {"proposal": {"artifact_ids": ["b", "a", "b"], "question_ids": ["z", "a"]}},
            {"proposal": {"artifact_ids": ["a"], "question_ids": ["a"]}}]
        expected = [{"artifact_id": ident, "question_id": question}
                    for ident, question in (("a", "a"), ("a", "z"), ("b", "a"), ("b", "z"))]
        self.assertEqual(supplement_reread_contract(self.task)["registered_pairs"], expected)

    def test_model_receives_empty_history_before_first_supplement(self):
        self.task.update(status="运行中", run_mode="model")
        self.task["ledger"]["used"]["model_calls"] = 2
        self.agent.archive.save(self.task)
        self.agent.model = fixtures.ScriptedModel([("supplement", self.fixture.proposal())])
        with patch.object(self.agent.collector, "external") as supplier:
            self.assertTrue(self.agent._maybe_supplement(self.task["id"], self.task["batch"]))
        supplier.assert_not_called()
        payload = self.agent.model.calls[0][1]
        self.assertEqual(payload["extraction_repair_contract"]["version"], 2)
        self.assertEqual(payload["extraction_repair_contract"]["registered_pairs"], [])
        self.assertEqual(payload["previous_rounds"], [])
        self.assertTrue(payload["executable_actions"]["extraction_repairs"])
        saved = self.agent.archive.get(self.task["id"])
        self.assertEqual(saved["ledger"]["used"]["model_calls"], 3)
        self.assertEqual(saved["ledger"]["supplements"], 1)
        self.assertEqual(len(supplement_reread_pairs(saved)), 1)

    def test_model_may_still_stop_without_an_automatic_retry(self):
        proposal = self.fixture.proposal()
        proposal.update(artifact_ids=[], stop_reason="现有候选不能解决剩余必要范围")
        self.task.update(status="运行中", run_mode="model")
        self.agent.archive.save(self.task)
        self.agent.model = fixtures.ScriptedModel([("supplement", proposal)])
        self.assertFalse(self.agent._maybe_supplement(self.task["id"], self.task["batch"]))
        saved = self.agent.archive.get(self.task["id"])
        self.assertEqual(saved["ledger"]["supplements"], 0)
        self.assertEqual(saved.get("supplement_history", []), [])
        self.assertEqual(len(self.agent.model.calls), 1)
        self.assertEqual(saved["supplement_stop_reason"], proposal["stop_reason"])

    def test_phase_instructions_distinguish_candidates_and_history(self):
        rules = PHASE_RULES["supplement"]
        self.assertIn("registered_pairs", rules)
        self.assertIn("首次抽取、被拒绝的证据", rules)
        self.assertIn("候选id也不是补证执行记录", rules)

    def test_supplement_revision_is_recorded_separately(self):
        proposal = self.fixture.proposal()
        model = fixtures.ScriptedModel([("supplement", proposal)])
        with patch("researchhelper.reasoning._record_output_contract") as record:
            _, receipt = model_object(model, self.task, "supplement", {}, PHASE_FIELDS["supplement"])
        self.assertEqual(SUPPLEMENT_RULES_VERSION, 2)
        self.assertEqual(receipt["rules_version"], RULES_VERSION)
        self.assertEqual(receipt["supplement_rules_version"], 2)
        self.assertEqual(record.call_args.args[3]["supplement_rules_version"], 2)

    def test_other_phase_receipts_do_not_claim_supplement_revision(self):
        model = fixtures.ScriptedModel([("extract", {"evidence_specs": []})])
        with patch("researchhelper.reasoning._record_output_contract") as record:
            _, receipt = model_object(model, self.task, "extract", {}, PHASE_FIELDS["extract"])
        self.assertNotIn("supplement_rules_version", receipt)
        self.assertNotIn("supplement_rules_version", record.call_args.args[3])


if __name__ == "__main__":
    unittest.main()
