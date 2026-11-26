"""Phase protocols follow their current revisions independently of retained knowledge."""

import copy
import json
import unittest
from unittest.mock import patch

from researchhelper.contracts import fingerprint
from researchhelper.reasoning import (ANALYSIS_TEXT_RULES_VERSION, CALCULATION_PLAN_VERSION,
                                     CONVERSATION_RULES_VERSION, PHASE_FIELDS, PHASE_RULES,
                                     RULES_VERSION, calculation_plan_contract, model_object,
                                     phase_output_example)
from tests import test_extraction_binding_guidance_offline as retained
from tests import test_analysis_answer_types_offline as answer_tests
from tests import test_calculation_shared_contract_offline as calculation_tests


class ExtractionContractVersionTests(unittest.TestCase):
    def test_analysis_text_version_does_not_change_other_phase_protocols_v26(self):
        fixture = answer_tests.fixtures.AnalysisOutputStructureTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for phase in PHASE_FIELDS:
            with self.subTest(phase=phase):
                model = fixture.model(phase_output_example(phase))
                _, receipt = model_object(model, fixture.task, phase, {}, PHASE_FIELDS[phase])
                self.assertEqual(receipt["rules_version"], RULES_VERSION)
                if phase == "analyze":
                    self.assertEqual(receipt["analysis_text_rules_version"], ANALYSIS_TEXT_RULES_VERSION)
                else:
                    self.assertNotIn("analysis_text_rules_version", receipt)

    def test_optional_empty_objects_and_required_fields_match_validator_v26(self):
        fixture = calculation_tests.CalculationSharedContractTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        contract = calculation_plan_contract(fixture.task)
        self.assertEqual(contract["empty_assumptions"], {})
        self.assertEqual(set(contract["required_fields"]), {"id", "method", "inputs", "parameters", "question_ids"})
        for field in ("inputs", "parameters", "assumptions", "assumption_sources"):
            self.assertEqual(contract["field_types"][field], "object")
        self.assertEqual(contract["field_types"]["question_ids"], "array<string>")
        class Host:
            def reason(self, task, messages):
                return json.dumps(self.output)
        host = Host()
        for phase in ("plan", "supplement", "extract", "analyze"):
            host.output = phase_output_example(phase)
            _, receipt = model_object(host, fixture.task, phase, {}, PHASE_FIELDS[phase])
            self.assertEqual(receipt["rules_version"], RULES_VERSION)
            if phase in ("plan", "supplement"):
                self.assertEqual(receipt["calculation_contract_version"], CALCULATION_PLAN_VERSION)
            else:
                self.assertNotIn("calculation_contract_version", receipt)

    def test_conversation_protocol_version_is_separate_and_recorded_v26(self):
        fixture = retained.followup_tests.FollowUpContextTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        parent = fixture.parent()
        child = fixture.follow(parent)
        for task in (parent, child):
            with patch("researchhelper.reasoning._record_output_contract") as record:
                _, receipt = model_object(fixture.agent.model, task, "extract", {}, PHASE_FIELDS["extract"])
            diagnostic = record.call_args.args[3]
            self.assertEqual(receipt["rules_version"], RULES_VERSION)
            if task is child:
                self.assertEqual(receipt["conversation_rules_version"], CONVERSATION_RULES_VERSION)
                self.assertEqual(diagnostic["conversation_rules_version"], CONVERSATION_RULES_VERSION)
            else:
                self.assertNotIn("conversation_rules_version", receipt)
                self.assertNotIn("conversation_rules_version", diagnostic)

    def test_knowledge_revision_preserves_original_entry_and_arithmetic_version_v26(self):
        fees = retained.fee_tests
        identifier = fees.METHOD_REGISTRY[fees.METHOD]["knowledge_id"]
        old = fees.RETAINED_VERSIONS[(identifier, 1)]
        self.assertEqual(old["version"], 1)
        self.assertNotIn("previous_version", old)
        latest = fees.ResearchKnowledge().read("ETF", "比较费率", [fees.METHOD])
        self.assertEqual(latest["index"][identifier], 2)
        historical = copy.deepcopy(latest)
        historical["entries"] = [copy.deepcopy(old) if entry["id"] == identifier else entry
                                 for entry in historical["entries"]]
        historical["index"][identifier] = 1
        fees.ResearchKnowledge().validate_package(historical)
        self.assertEqual(fingerprint(old), fingerprint(fees.RETAINED_VERSIONS[(identifier, 1)]))
        self.assertEqual(fees.METHOD_REGISTRY[fees.METHOD]["implementation_version"], 1)
        self.assertIn("raw_unit与unit仍填%", PHASE_RULES["extract"])
