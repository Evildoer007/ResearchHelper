"""App planning and Skill host calculations share one read-only contract.

The prices, materials and model HTTP responses are synthetic. No vendor or
real model is contacted; this is not a financial quality or permission gate.
"""

import copy
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from researchhelper.contracts import ContractError, METHOD_REGISTRY, context
from researchhelper.model import ModelService, ModelSettings
from researchhelper.planning import question_specs
from researchhelper.reasoning import (CALCULATION_PLAN_VERSION, PHASE_FIELDS, calculation_plan_contract,
                                     model_object, phase_output_example, validate_plan)
from tests import test_autonomous_reasoning_offline as models
from tests import test_method_dependency_workflow_offline as methods

ROOT = Path(__file__).resolve().parents[2]


class CalculationSharedContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = methods.MethodDependencyWorkflowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.task, self.host, _ = self.fixture.prepare("observed_price_return_v1")
        self.task = self.fixture.archive.get(self.task["id"])
        self.host["calculations"][0]["assumptions"] = {}
        self.host["answers"] = {"q1": {"refs": [s["id"] for s in self.host["evidence_specs"]],
            "calculation_refs": ["calc_main"], "numeric_segments": {"answer": [
                {"text": "合成材料的未复权价格变化为"},
                {"source": "calculation", "ref": "calc_main", "path": "value", "format": "signed", "digits": 2},
                {"text": "。公司行为状态未知，不代表基金净值变化、持有人总回报或买卖建议。"}]}}}

    def model_run(self):
        agent = self.fixture.agent
        agent.offline = False
        settings = ModelSettings(self.fixture.archive.root / "synthetic-model")
        settings.save({"endpoint": "https://example.invalid/model", "model": "synthetic-model",
                       "api_key": "SYNTHETIC_CALCULATION_TEST_NOT_A_REAL_KEY"})
        agent.model = ModelService(settings, offline=False, journal=agent._journal)
        proposal = {"questions": question_specs(self.task), "data_requests": [],
                    "calculations": self.host["calculations"]}
        model = models.FabricatedModel([proposal, {"evidence_specs": self.host["evidence_specs"]},
                                      {"answers": self.host["answers"], "theses": []}])
        with patch("researchhelper.model.urllib.request.urlopen", side_effect=model.respond):
            final = agent.start(self.task["id"])
        self.assertEqual(final["status"], "已完成", final.get("error") or final["gaps"])
        self.assertEqual(len(model.requests), 3)
        self.assertEqual(final["ledger"]["used"]["model_calls"], 3)
        self.assertEqual(final["ledger"]["used"]["data_requests"], 0)
        self.assertAlmostEqual(final["calculations"][0]["output"]["value"], 10)
        self.assertTrue(final["questions"][0]["numeric_integrity"])
        saved = self.fixture.archive.report(final["id"], final["version"])
        self.assertTrue(saved["html_integrity_verified"])
        return final, model, saved

    def skill_session(self):
        folder = tempfile.TemporaryDirectory(prefix="rh-shared-contract-", dir=ROOT / "result/acceptance")
        self.addCleanup(folder.cleanup)
        installation = Path(folder.name) / "skill"
        for original, relative in (("skill/researchhelper/scripts/session.py", "scripts/session.py"),
                                   ("app/web/report.css", "app/web/report.css"),
                                   ("assets/vendor/echarts.min.js", "assets/vendor/echarts.min.js")):
            target = installation / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / original, target)
        spec = importlib.util.spec_from_file_location("synthetic_calculation_skill", installation / "scripts/session.py")
        module = importlib.util.module_from_spec(spec)
        original_path, original_bytecode = list(sys.path), sys.dont_write_bytecode
        try:
            spec.loader.exec_module(module)
        finally:
            sys.path[:] = original_path
            sys.dont_write_bytecode = original_bytecode
        session = module.Session(Path(folder.name) / "project")
        return session

    def test_methods_are_exact_executable_registry_copies(self):
        contract = calculation_plan_contract(self.task)
        self.assertEqual(contract["methods"], METHOD_REGISTRY)
        contract["methods"]["observed_price_return_v1"]["parameters"]["currency"] = "USD"
        self.assertEqual(METHOD_REGISTRY["observed_price_return_v1"]["parameters"]["currency"], "CNY")

    def test_descriptor_is_bound_read_only_and_does_not_grant_execution(self):
        before = copy.deepcopy(self.task)
        with patch("urllib.request.urlopen") as network:
            contract = calculation_plan_contract(self.task)
        network.assert_not_called()
        self.assertEqual(contract["binding"], context(self.task))
        self.assertEqual(contract["question_ids"], ["q1"])
        self.assertEqual(self.fixture.archive.get(self.task["id"]), before)
        self.assertEqual(before["calculations"], [])
        self.assertEqual(before["ledger"]["dispatches"], [])

    def test_optional_empty_objects_and_required_fields_match_validator(self):
        contract = calculation_plan_contract(self.task)
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
            _, receipt = model_object(host, self.task, phase, {}, PHASE_FIELDS[phase])
            self.assertEqual(receipt["rules_version"], 25)
            if phase in ("plan", "supplement"):
                self.assertEqual(receipt["calculation_contract_version"], CALCULATION_PLAN_VERSION)
            else:
                self.assertNotIn("calculation_contract_version", receipt)

    def test_app_receives_contract_and_completes_calculation_report(self):
        final, model, saved = self.model_run()
        payload = json.loads(model.requests[0]["messages"][-1]["content"])
        self.assertEqual(payload["calculation_contract"]["methods"], METHOD_REGISTRY)
        self.assertEqual(payload["calculation_contract"]["binding"]["id"], self.task["id"])
        self.assertNotIn("calculation_methods", payload)  # No duplicated method catalog in prompts.
        self.assertIn("不代表基金净值变化", saved["html"])
        diagnostic = final["ledger"]["dispatches"][0]["output_contract"]
        self.assertEqual(diagnostic["calculation_contract_version"], CALCULATION_PLAN_VERSION)

    def test_skill_reads_same_contract_without_starting_second_model(self):
        session = self.skill_session()
        task = session.create(self.task["question"], objects=self.task["objects"],
                              cutoff=self.task["cutoff"], questions=question_specs(self.task))
        before = session.status(task["id"])
        tools = session.tools(task["id"])
        self.assertEqual(tools["calculations"], calculation_plan_contract(task))
        self.assertEqual(session.status(task["id"]), before)
        self.assertIsNone(session.agent.model)

    def test_app_and_skill_produce_same_price_change_and_limitations(self):
        app, _, app_saved = self.model_run()
        session = self.skill_session()
        task = session.create(self.task["question"], objects=self.task["objects"],
                              cutoff=self.task["cutoff"], questions=question_specs(self.task))
        specs = copy.deepcopy(self.host["evidence_specs"])
        for original, spec in zip(self.task["materials"], specs):
            text = original["snapshot"]["parts"][0]["text"]
            record = session.material(task["id"], original["name"], text.encode())
            spec["artifact_id"] = record["id"]
            anchor = {"kind": "material", "artifact_id": record["id"], "snapshot_hash": record["snapshot_hash"],
                      "part": "段落1", "quote": text}
            spec["publication"]["basis_ref"] = copy.deepcopy(anchor)
            spec["calculation_context_basis"] = {key: {"kind": "unverified"} if key == "corporate_action_status"
                                                  else copy.deepcopy(anchor) for key in spec["calculation_context"]}
        host = session.run(task["id"], evidence_specs=specs, calculations=copy.deepcopy(self.host["calculations"]),
                           answers=copy.deepcopy(self.host["answers"]))
        self.assertEqual(host["status"], "已完成", host.get("error") or host["gaps"])
        host_saved = session.archive.report(host["id"], host["version"])
        self.assertEqual(host["calculations"][0]["output"], app["calculations"][0]["output"])
        self.assertEqual(host["questions"][0]["answer"], app["questions"][0]["answer"])
        self.assertEqual(host_saved["content"]["questions"][0]["answer"], app_saved["content"]["questions"][0]["answer"])
        self.assertEqual(host["theses"], app["theses"])
        self.assertEqual(host["ledger"]["used"]["model_calls"], 0)
        self.assertEqual(host["ledger"]["used"]["data_requests"], 0)
        target = session.export(host["id"])
        self.assertEqual(target.read_text(), host_saved["html"])
        self.assertIsNone(session.agent.model)

    def test_descriptor_mutation_does_not_relax_actual_parameters(self):
        contract = calculation_plan_contract(self.task)
        contract["methods"]["observed_price_return_v1"]["parameters"]["currency"] = "USD"
        calculation = copy.deepcopy(self.host["calculations"][0])
        calculation["parameters"]["currency"] = "USD"
        with self.assertRaises(ContractError):
            validate_plan(self.task, {"questions": question_specs(self.task),
                                    "data_requests": [], "calculations": [calculation]})


if __name__ == "__main__":
    unittest.main()
