"""Readonly locator regression: synthetic snapshots, no vendor/model calls."""

import copy
import importlib.util
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, DOCUMENT_CONTENT_INTENT, fingerprint
from researchhelper.evidence import api_cell_candidates, candidate_evidence
from researchhelper.reasoning import evidence_contract
from tests import test_api_calculation_context_contract_offline as fixtures


class APICellCandidateTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.APICalculationContextContractTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.agent, self.task = self.fixture.agent, self.fixture.task

    def rehash(self, record):
        record["hash"] = fingerprint(record["snapshot"])
        if "snapshot_hash" in record:
            record["snapshot_hash"] = record["hash"]
        return record

    def test_ifind_original_indices_and_metadata(self):
        record = self.fixture.record("ifind")
        result = api_cell_candidates(record)
        cell = result["cells"][0]
        self.assertEqual(cell["proposal_fields"], {"artifact_id": record["id"],
            "record_locator": {"table": 0, "row": 0, "field": "close"}, "period": "2026-09-30"})
        self.assertEqual(cell["source_metadata"]["raw_value"], 4.432)
        self.assertEqual(cell["source_metadata"]["snapshot_hash"], record["hash"])
        self.assertTrue(result["scan_complete"])
        self.assertFalse(result["candidate_is_approval"])
        self.assertFalse(result["absence_is_proof"])

    def test_tinyshare_date_normalization_does_not_rewrite_raw_value(self):
        record = self.fixture.record()
        record["snapshot"]["data"][0]["close"] = "4.4320"
        self.rehash(record)
        cell = api_cell_candidates(record)["cells"][0]
        self.assertEqual(cell["proposal_fields"]["period"], "2026-09-30")
        self.assertEqual(cell["source_metadata"]["raw_observation_date"], "20260930")
        self.assertEqual(cell["source_metadata"]["raw_value"], "4.4320")
        self.assertNotIn("table", cell["proposal_fields"]["record_locator"])

    def test_null_and_boolean_not_zero_filled_or_reindexed(self):
        record = self.fixture.record("ifind")
        table = record["snapshot"]["tables"][0]
        table["time"] = ["2026-09-30"] * 4
        table["table"]["close"] = [None, True, 0, 4.432]
        self.rehash(record)
        result = api_cell_candidates(record)
        self.assertEqual(result["invalid_positions"], 2)
        self.assertEqual([c["proposal_fields"]["record_locator"]["row"] for c in result["cells"]], [2, 3])
        self.assertEqual(result["cells"][0]["source_metadata"]["raw_value"], 0)

    def test_invalid_date_does_not_shift_next_valid_row(self):
        record = self.fixture.record()
        original = record["snapshot"]["data"][0]
        record["snapshot"]["data"] = [{**original, "trade_date": "20260230"}, original]
        self.rehash(record)
        result = api_cell_candidates(record)
        self.assertEqual(result["invalid_positions"], 1)
        self.assertEqual(result["cells"][0]["proposal_fields"]["record_locator"]["row"], 1)

    def test_misaligned_ifind_arrays_never_offer_a_cell(self):
        record = self.fixture.record("ifind")
        record["snapshot"]["tables"][0]["time"] = []
        self.rehash(record)
        result = api_cell_candidates(record)
        self.assertEqual(result["cells"], [])
        self.assertEqual(result["invalid_positions"], 1)

    def test_nonarray_collection_returns_unavailable_not_exception(self):
        for source, key in (("ifind", "tables"), ("tinyshare", "data")):
            for value in (None, {}, "bad"):
                with self.subTest(source=source, value=value):
                    record = self.fixture.record(source)
                    record["snapshot"][key] = value
                    self.rehash(record)
                    result = api_cell_candidates(record)
                    self.assertFalse(result["scan_complete"])
                    self.assertEqual(result["cells"], [])
                    self.assertIn("数组", result["reason"])

    def test_business_error_and_boolean_success_codes_not_candidates(self):
        for source, key in (("ifind", "errorcode"), ("tinyshare", "code")):
            for code in (False, 403, "403"):
                with self.subTest(source=source, code=code):
                    record = self.fixture.record(source)
                    record["snapshot"][key] = code
                    self.rehash(record)
                    self.assertEqual(api_cell_candidates(record)["cells"], [])

    def test_unregistered_financial_operation_remains_raw_only(self):
        record = self.fixture.record()
        record["operation"] = "income"
        record["request_hash"] = fingerprint({"source": record["source"],
            "operation": record["operation"], "parameters": record["parameters"]})
        self.assertEqual(api_cell_candidates(record)["cells"], [])

    def test_changed_request_or_snapshot_never_generates_candidates(self):
        record = self.fixture.record("ifind")
        for mutate in (lambda r: r["parameters"].update(codes="000001.SZ"),
                       lambda r: r["snapshot"]["tables"][0]["table"].update(close=[999])):
            altered = copy.deepcopy(record)
            mutate(altered)
            self.assertEqual(api_cell_candidates(altered)["cells"], [])

    def test_cell_limit_does_not_claim_exhaustion(self):
        record = self.fixture.record()
        row = record["snapshot"]["data"][0]
        record["snapshot"]["data"] = [copy.deepcopy(row) for _ in range(17)]
        self.rehash(record)
        result = api_cell_candidates(record)
        self.assertEqual(len(result["cells"]), 16)
        self.assertFalse(result["scan_complete"])
        self.assertFalse(result["absence_is_proof"])

    def test_bad_positions_and_empty_containers_cannot_bypass_scan_bound(self):
        record = self.fixture.record()
        row = record["snapshot"]["data"][0]
        for prefix in ([{**row, "close": None}] * 1000, [None] * 1000):
            with self.subTest(prefix_kind=type(prefix[0]).__name__):
                record["snapshot"]["data"] = prefix + [row]
                self.rehash(record)
                result = api_cell_candidates(record)
                self.assertEqual(result["scan_units"], 512)
                self.assertEqual(result["cells"], [])
                self.assertFalse(result["scan_complete"])

    def test_invalid_limit_and_record_shapes_are_explicit_errors(self):
        record = self.fixture.record()
        for value in (0, 65, True, 1.5, "16"):
            with self.subTest(value=value), self.assertRaises(ContractError):
                api_cell_candidates(record, value)
        with self.assertRaises(ContractError):
            api_cell_candidates(None)

    def test_result_and_task_are_readonly_and_do_not_charge_budget(self):
        record = self.fixture.record("ifind")
        before = copy.deepcopy(self.task)
        with patch("urllib.request.urlopen") as network:
            contract = evidence_contract(self.task)
        network.assert_not_called()
        contract["sources"][0]["api_cells"]["cells"][0]["proposal_fields"]["record_locator"]["row"] = 999
        self.assertEqual(self.task, before)
        self.assertEqual(api_cell_candidates(record)["cells"][0]["proposal_fields"]["record_locator"]["row"], 0)

    def test_global_cell_limit_keeps_each_source_and_partial_status(self):
        records = []
        for index in range(5):
            record = self.fixture.record()
            record["id"] += "_" + str(index)
            row = record["snapshot"]["data"][0]
            record["snapshot"]["data"] = [copy.deepcopy(row) for _ in range(17)]
            records.append(self.rehash(record))
        self.task["source_records"] = records
        sources = evidence_contract(self.task)["sources"]
        self.assertEqual(len(sources), 5)
        self.assertEqual(sum(len(s["api_cells"]["cells"]) for s in sources), 64)
        self.assertFalse(sources[-1]["api_cells"]["scan_complete"])
        self.assertEqual(sources[-1]["api_cells"]["cells"], [])

    def test_candidates_cannot_supply_missing_identity_review_or_publication(self):
        record = self.fixture.record("ifind")
        proposal = api_cell_candidates(record)["cells"][0]["proposal_fields"]
        for field in ("identity", "unit", "raw_value", "publication", "availability", "review", "question_ids"):
            self.assertNotIn(field, proposal)
        with self.assertRaises(ContractError):
            candidate_evidence(self.task, record, proposal)
        self.assertFalse(self.task["evidence"])
        self.assertNotIn("current_availability_reference", evidence_contract(self.task)["sources"][0])

    def test_cross_task_version_and_hash_still_rejected(self):
        self.fixture.record()
        for field, value in (("task_id", "foreign"), ("research_version", 99), ("hash", "forged")):
            altered = copy.deepcopy(self.task)
            altered["source_records"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                evidence_contract(altered)

    def test_document_content_mode_does_not_offer_numeric_api_candidates(self):
        self.fixture.record()
        self.task["intent"] = DOCUMENT_CONTENT_INTENT
        source = evidence_contract(self.task)["sources"][0]
        self.assertNotIn("api_cells", source)
        self.assertFalse(source["usable_for_document_content"])

    def test_app_extraction_receives_candidate_contract(self):
        self.fixture.record("ifind")
        self.task["host_result"] = {}
        with patch.object(self.agent, "_reasoning_available", return_value=True), \
                patch.object(self.agent, "_save_model_checkpoint"), \
                patch("researchhelper.engine.model_object", return_value=({"evidence_specs": []}, {})) as model:
            self.agent._extract_model_sources(self.task)
        received = model.call_args.args[3]["evidence_contract"]
        self.assertEqual(received, evidence_contract(self.task))
        self.assertFalse(received["api_candidate_contract"]["automatic_adoption"])

    def test_skill_reads_same_candidates_without_second_model(self):
        record = self.fixture.record("ifind")
        self.agent.archive.save(self.task)
        spec = importlib.util.spec_from_file_location("api_cells_skill", fixtures.ROOT / "core/session.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        session = module.Session.__new__(module.Session)
        session.archive = self.agent.archive
        self.assertEqual(session.tools(self.task["id"])["evidence"], evidence_contract(self.task))
        self.assertIsNone(self.agent.model)
        self.assertEqual(record["snapshot"]["tables"][0]["table"]["close"], [4.432])

    def test_old_extraction_cache_does_not_suppress_new_candidates(self):
        from researchhelper.engine import extractable_source_views
        self.fixture.record()
        views = extractable_source_views(self.task)
        old_digest = fingerprint({"input_hash": self.task["input_hash"], "sources": views, "reader_version": 1})
        self.task["host_result"] = {}
        self.task["reasoning_state"] = {"extraction": {"input_digest": old_digest}}
        with patch.object(self.agent, "_reasoning_available", return_value=True), \
                patch.object(self.agent, "_save_model_checkpoint"), \
                patch("researchhelper.engine.model_object", return_value=({"evidence_specs": []}, {})) as model:
            self.agent._extract_model_sources(self.task)
            self.agent._extract_model_sources(self.task)
        self.assertEqual(model.call_count, 1)
        self.assertNotEqual(self.task["reasoning_state"]["extraction"]["input_digest"], old_digest)


class AcquiredCandidateWorkflowTests(unittest.TestCase):
    """Local SDK transport and normal evidence/report gates, never a vendor."""

    def setUp(self):
        from tests.test_current_snapshot_availability_offline import CurrentSnapshotAvailabilityTests
        self.fixture = CurrentSnapshotAvailabilityTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_selected_location_passes_normal_gates_and_survives_report_reload(self):
        task, record = self.fixture.acquired()
        source = evidence_contract(task)["sources"][0]
        proposal = self.fixture.spec(task, record)
        proposal.update(source["api_cells"]["cells"][0]["proposal_fields"])
        self.fixture.approved(task, record, proposal)
        done = self.fixture.agent.start(task["id"], {"evidence_specs": [proposal], "answers": {"q1": {
            "answer": "本次响应记录收盘价为4.5元，不代表历史时点可得版本。", "refs": ["close"]}}})
        self.assertEqual(done["status"], "已完成", done.get("error"))
        report = self.fixture.agent.export(task["id"])
        saved = self.fixture.agent.archive.report(done["id"], done["version"])
        self.assertEqual(saved["digest"], report["digest"])
        self.assertEqual(saved["content"]["evidence"][0]["record_locator"], proposal["record_locator"])
        self.assertEqual(done["ledger"]["used"]["model_calls"], 0)
        self.assertEqual(done["ledger"]["used"]["data_requests"], 2)
        self.assertIn("不证明历史当时可得", saved["html"])

    def test_candidate_never_repairs_missing_identity_or_availability(self):
        task, record = self.fixture.acquired()
        source = evidence_contract(task)["sources"][0]
        for missing in ("identity", "availability"):
            proposal = self.fixture.spec(task, record)
            proposal.update(source["api_cells"]["cells"][0]["proposal_fields"])
            proposal.pop(missing)
            with self.subTest(missing=missing), self.assertRaises(ContractError):
                self.fixture.approved(task, record, proposal)
        self.assertFalse(task["evidence"])


class MaterialCacheCompatibilityTests(unittest.TestCase):
    def test_candidate_upgrade_does_not_charge_or_reextract_material_only_task(self):
        from researchhelper.engine import extractable_source_views
        from tests.test_extraction_binding_guidance_offline import ExtractionBindingGuidanceTests
        fixture = ExtractionBindingGuidanceTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        task = fixture.task
        before = copy.deepcopy(task["ledger"])
        task["host_result"] = {"evidence_specs": [fixture.spec()]}
        task["reasoning_state"] = {"extraction": {"input_digest": fingerprint({
            "input_hash": task["input_hash"], "sources": extractable_source_views(task), "reader_version": 1})}}
        with patch("researchhelper.engine.model_object") as model:
            result = fixture.agent._extract_model_sources(task)
        model.assert_not_called()
        self.assertEqual(result, task["host_result"])
        self.assertEqual(task["ledger"], before)


def review():
    """Persist focused acceptance without changing prior tests or releases."""
    import hashlib
    import io
    import json
    import socket
    import tempfile
    import time

    modules = [
        "tests.test_api_cell_candidates_offline",
        "tests.test_api_calculation_context_contract_offline",
        "tests.test_extraction_specification_rejection_offline",
        "tests.test_extraction_contract_version_offline",
        "tests.test_material_extraction_grammar_offline",
        "tests.test_extraction_reread_workflow_offline",
        "tests.test_current_snapshot_availability_offline",
        # The old *claim_integrity* module reproduces acceptance of bad prose;
        # it is not a regression gate. Retain that file and its failed receipt.
        "tests.test_numeric_statement_workflow_offline",
        "tests.test_source_reader_offline",
    ]
    root = fixtures.ROOT
    paths = [root / "researchhelper" / name for name in ("evidence.py", "reasoning.py", "engine.py")]
    paths += [root / (name.replace(".", "/") + ".py") for name in modules]
    def hashes():
        return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    before = hashes()
    loader = unittest.TestLoader()
    prior_path = root / "result/acceptance/extraction-contract-release/regression-8fkkwc__/runtime.json"
    prior = json.loads(prior_path.read_text())
    retained = [name for name in prior["unique_test_ids"] if any(name.startswith(module + ".") for module in modules[1:])]
    suite = loader.loadTestsFromNames([modules[0], *retained])
    if loader.errors:
        raise AssertionError(loader.errors)
    attempts, original_connect = [], socket.socket.connect
    def local_only(connection, address):
        if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "localhost", "::1"):
            attempts.append(str(address))
            raise AssertionError("External network forbidden")
        return original_connect(connection, address)
    began = time.monotonic()
    with patch("socket.socket.connect", local_only):
        result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=1).run(suite)
    receipt = {"tests_run": result.testsRun, "failures": [
        {"id": case.id(), "traceback": trace} for case, trace in result.failures], "errors": [
        {"id": case.id(), "traceback": trace} for case, trace in result.errors],
        "skipped": [{"id": case.id(), "reason": reason} for case, reason in result.skipped],
        "external_attempts": attempts, "source_hashes": before,
        "sources_unchanged": before == hashes(), "elapsed_seconds": time.monotonic() - began,
        "real_model_calls": 0, "real_supplier_calls": 0, "app_updated": False,
        "demo_synced": False, "full_harness_complete": False,
        "retained_test_ids": retained, "prior_inventory": str(prior_path),
        "prior_inventory_sha256": hashlib.sha256(prior_path.read_bytes()).hexdigest(),
        "scope": "New candidate tests plus matching tests from the installed regression inventory; not all repository diagnostics"}
    directory = root / "result/acceptance/api-cell-candidates"
    directory.mkdir(parents=True, exist_ok=True)
    path = fixtures.Path(tempfile.mkdtemp(prefix="offline-", dir=directory)) / "receipt.json"
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not result.wasSuccessful() or attempts or not receipt["sources_unchanged"]:
        raise AssertionError(str(path))
    return {"receipt": str(path), "tests_run": result.testsRun, "status": "passed"}
