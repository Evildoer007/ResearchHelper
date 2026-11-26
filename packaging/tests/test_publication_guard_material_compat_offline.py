"""Independent compatibility fixtures for the shared publication gate.

Old test files remain unchanged. Source dates are supplied in newly generated
bytes or in the same retained filing directory, never by bypassing validation.
All HTTP/Skill stores are temporary and provider responses are fabricated.
"""
import base64
import hashlib
import importlib
import unittest

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.evidence import (
    candidate_evidence, extract_material, material_record, validate_provenance,
)


_CHAIN = "tests.test_evidence_chain_offline.EvidenceChainTests."
_INTERFACES = "tests.test_evidence_chain_interfaces_offline.EvidenceInterfaces."
_NEW = "tests.test_publication_guard_material_compat_offline.MaterialPublicationCompatibilityTests."
REPLACED_EXPECTATIONS = {
    _CHAIN + "test_material_keeps_original_bytes_and_exact_paragraph": _NEW + "test_material_original_bytes",
    _CHAIN + "test_duplicate_material_import_has_stable_content_identity": _NEW + "test_material_duplicate_identity",
    _CHAIN + "test_filing_original_numeric_revenue_is_recomputable": _NEW + "test_filing_numeric_revenue",
    _CHAIN + "test_unavailable_optional_acquisition_does_not_block_independent_answer": _NEW + "test_optional_acquisition",
    _CHAIN + "test_verified_material_cannot_answer_an_unreviewed_other_question": _NEW + "test_question_scope",
    _CHAIN + "test_correction_before_cutoff_blocks_old_record": _NEW + "test_correction_before_cutoff",
    _CHAIN + "test_new_version_requires_fresh_receipts_and_rechecks_correction": _NEW + "test_new_version_correction",
    _CHAIN + "test_date_only_not_available_intraday": _NEW + "test_day_only_intraday",
    _CHAIN + "test_forecast_cannot_pass_realized_only_review": _NEW + "test_forecast_review",
    _INTERFACES + "test_authenticated_original_import_and_source_checks": _NEW + "test_http_original_import",
    _INTERFACES + "test_skill_original_import_run_export_is_independent_and_read_only": _NEW + "test_skill_original_import",
    "tests.test_calculation_context_sources_offline.CalculationContextSourcesTests.test_material_candidate_entry_requires_retained_anchors": _NEW + "test_calculation_context_anchor",
    "tests.test_filing_acquisition_offline.FilingAcquisitionTests.test_pdf_original_is_bound_but_not_automatically_approved": _NEW + "test_pdf_original_binding",
    "tests.test_html_material_offline.HTMLMaterialTests.test_minute_time_cannot_be_invented_as_seconds": _NEW + "test_minute_not_seconds",
}
ADAPTATION_NOTES = {
    _CHAIN + "test_material_keeps_original_bytes_and_exact_paragraph": "Invoke the current original unchanged; it owns retained publication paragraphs, locators, byte-retention and completion assertions.",
    _CHAIN + "test_duplicate_material_import_has_stable_content_identity": "Invoke the current original unchanged; identical anchored bytes retain one material identity and the original completion checks.",
    _CHAIN + "test_filing_original_numeric_revenue_is_recomputable": "Invoke the already directory-bound original without adding a second date anchor; retain raw-value and exported-source checks.",
    _CHAIN + "test_unavailable_optional_acquisition_does_not_block_independent_answer": "Use the base anchored material unchanged; retain optional finding, completion and zero additional acquisition checks.",
    _CHAIN + "test_verified_material_cannot_answer_an_unreviewed_other_question": "Use the base anchored material unchanged; preserve q1 answered and unrelated q2 unanswered.",
    _CHAIN + "test_correction_before_cutoff_blocks_old_record": "Use the current directory-bound original; preserve correction rejection.",
    _CHAIN + "test_new_version_requires_fresh_receipts_and_rechecks_correction": "Use the current directory-bound original; preserve correction rejection and frozen prior-version export.",
    _CHAIN + "test_date_only_not_available_intraday": "Use the base dated material unchanged; source passes and same-day intraday applicability still fails.",
    _CHAIN + "test_forecast_cannot_pass_realized_only_review": "Use the base anchored material unchanged; forecast-versus-realized applicability still fails.",
    _INTERFACES + "test_authenticated_original_import_and_source_checks": "Invoke the current authenticated document-content test unchanged; unknown first-release time remains unknown and no historical use is granted.",
    _INTERFACES + "test_skill_original_import_run_export_is_independent_and_read_only": "Invoke the current independent document-content Skill test unchanged; retain installation hashes, exact originals, export, raw value, both receipts and zero model/provider calls.",
    "tests.test_calculation_context_sources_offline.CalculationContextSourcesTests.test_material_candidate_entry_requires_retained_anchors": "New dated material keeps original first four paragraphs; preserve context anchors, source-pass/applicability-pending and removal rejection.",
    "tests.test_filing_acquisition_offline.FilingAcquisitionTests.test_pdf_original_is_bound_but_not_automatically_approved": "Add same-filing directory publication anchor; retain every PDF acquisition, request hash, timeout, close, source stage, applicability, provenance and catalog-tamper assertion, plus byte hash and budget checks.",
    "tests.test_html_material_offline.HTMLMaterialTests.test_minute_time_cannot_be_invented_as_seconds": "Same original HTML/minute observation; candidate now rejects the invented seconds claim directly, while the original daily publication remains valid.",
}


def publication(record, at, *, part=None, quote=None):
    reference = {"artifact_id": record["id"], "snapshot_hash": record.get("snapshot_hash", record.get("hash"))}
    if part is None:
        reference.update(kind="directory", field="published_at")
    else:
        reference.update(kind="material", part=part, quote=quote)
    return {"at": at, "basis": "同一留存原件的明确发布日期", "precision": "日" if len(at) == 10 else "秒",
            "timezone": "Asia/Shanghai", "certainty": "已确认", "basis_ref": reference}


class MaterialPublicationCompatibilityTests(unittest.TestCase):
    def original(self, qualified_name):
        module_name, class_name, method = qualified_name.rsplit(".", 2)
        module = importlib.import_module(module_name)
        case = getattr(module, class_name)(method)
        self.addCleanup(case.doCleanups)
        case.setUp()
        self.addCleanup(case.tearDown)
        return module, case

    def chain(self, name):
        # The current originals already retain publication paragraphs and anchors.
        # Rewrapping them changes the locators and no longer tests the same bytes.
        _, case = self.original(_CHAIN + name)
        getattr(case, name)()

    def test_material_original_bytes(self):
        self.chain("test_material_keeps_original_bytes_and_exact_paragraph")

    def test_material_duplicate_identity(self):
        self.chain("test_duplicate_material_import_has_stable_content_identity")

    def test_filing_numeric_revenue(self):
        self.chain("test_filing_original_numeric_revenue_is_recomputable")

    def test_optional_acquisition(self):
        self.chain("test_unavailable_optional_acquisition_does_not_block_independent_answer")

    def test_question_scope(self):
        self.chain("test_verified_material_cannot_answer_an_unreviewed_other_question")

    def test_correction_before_cutoff(self):
        self.chain("test_correction_before_cutoff_blocks_old_record")

    def test_new_version_correction(self):
        self.chain("test_new_version_requires_fresh_receipts_and_rechecks_correction")

    def test_day_only_intraday(self):
        self.chain("test_date_only_not_available_intraday")

    def test_forecast_review(self):
        self.chain("test_forecast_cannot_pass_realized_only_review")

    def test_http_original_import(self):
        name = "test_authenticated_original_import_and_source_checks"
        _, case = self.original(_INTERFACES + name)
        getattr(case, name)()

    def test_skill_original_import(self):
        name = "test_skill_original_import_run_export_is_independent_and_read_only"
        _, case = self.original(_INTERFACES + name)
        getattr(case, name)()

    def test_calculation_context_anchor(self):
        parent = "tests.test_calculation_context_sources_offline.CalculationContextSourcesTests.test_material_candidate_entry_requires_retained_anchors"
        _, case = self.original(parent)
        text = "\n\n".join(part["text"] for part in case.material["parts"]) + "\n\n发布日期：2026年9月30日。"
        payload = text.encode()
        case.material = material_record(case.task, extract_material("context.txt", payload), payload)
        case.task["materials"] = [case.material]
        spec = {"id": "material_context", "object": "示例公司", "record_locator": {"part": "段落1"},
                "text": case.material["parts"][0]["text"], "published_at": "2026-09-30", "period": "2026-09-30",
                "nature": "已实现事实", "unit": "不适用", "calculation_context": case.context,
                "calculation_context_basis": case.item()["calculation_context_basis"],
                "publication": publication(case.material, "2026-09-30", part="段落5", quote="发布日期：2026年9月30日。")}
        result = candidate_evidence(case.task, case.material, spec)
        self.assertEqual(result["source_check"], "通过")
        self.assertEqual(result["applicability_check"], "待核验")
        case.check(result)
        case.task["materials"] = []
        with self.assertRaises(ContractError):
            candidate_evidence(case.task, case.material, spec)

    def test_pdf_original_binding(self):
        parent = "tests.test_filing_acquisition_offline.FilingAcquisitionTests.test_pdf_original_is_bound_but_not_automatically_approved"
        _, case = self.original(parent)
        record = case.catalog()
        factory, connection = case.transport()
        filing = case.collector.external(case.task, "ifind", "filing_original", case.selection(record), {})
        case.task["source_records"].append(filing)
        self.assertEqual(filing["status"], "成功待核验")
        self.assertEqual(filing["stages"]["source_check"], "待核验")
        self.assertEqual(filing["stages"]["applicability_check"], "待核验")
        self.assertEqual(filing["parameters"], case.selection(record))
        self.assertEqual(filing["request_hash"], fingerprint({"source": "ifind", "operation": "filing_original", "parameters": case.selection(record)}))
        self.assertIn("original_base64", filing["snapshot"]["material"])
        retained_pdf = filing["snapshot"]["material"]
        self.assertEqual(hashlib.sha256(base64.b64decode(retained_pdf["original_base64"])).hexdigest(), retained_pdf["hash"])
        self.assertEqual(fingerprint(filing["snapshot"]), filing["hash"])
        self.assertEqual(case.task["ledger"]["used"]["data_requests"], 1)
        connection.close.assert_called_once()
        self.assertLessEqual(factory.call_args.kwargs["timeout"], 30)
        spec = {"id": "e1", "object": "600000.SH", "published_at": case.row["ctime"], "period": case.row["reportDate"],
                "nature": "已实现事实", "unit": "不适用", "record_locator": {"part": "第1页"}, "text": "Synthetic filing only.", "value": None,
                "publication": publication(filing, case.row["ctime"])}
        candidate = candidate_evidence(case.task, filing, spec)
        self.assertEqual(candidate["source_check"], "通过")
        self.assertEqual(candidate["applicability_check"], "待核验")
        with self.assertRaises(ContractError):
            validate_provenance(candidate, case.task)
        record["catalog_entries"][0]["title"] = "篡改标题"
        with self.assertRaises(ContractError):
            candidate_evidence(case.task, filing, spec)

    def test_minute_not_seconds(self):
        parent = "tests.test_html_material_offline.HTMLMaterialTests.test_minute_time_cannot_be_invented_as_seconds"
        module, case = self.original(parent)
        payload = module.SAMPLE + b"<p>2025-05-20 09:00</p>"
        task = case.task(payload=payload)
        spec = case.proposal(task)
        material = task["materials"][0]
        self.assertEqual(base64.b64decode(material["snapshot"]["original_base64"]), payload)
        self.assertTrue(any("2025-05-20 09:00" in part["text"] for part in material["parts"]))
        daily = candidate_evidence(task, material, spec)
        self.assertEqual(daily["source_check"], "通过")
        spec.update(published_at="2025-05-20T09:00:00+08:00")
        spec["publication"].update(at=spec["published_at"], precision="秒")
        with self.assertRaisesRegex(ContractError, "不能补造盘中时间|原文日期"):
            candidate_evidence(task, material, spec)
        self.assertEqual(task["ledger"]["used"]["model_calls"], 0)
        self.assertEqual(task["ledger"]["used"]["data_requests"], 0)


if __name__ == "__main__":
    unittest.main()
