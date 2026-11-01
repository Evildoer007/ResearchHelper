"""Offline candidate delivery using the existing build and installation gates.

Keep historical tests and receipts intact. Four pinned version-25 cases use
their explicit version-26 counterparts only in package execution. Full source
regression still records all eight historical/version assertion failures.
"""

from contextlib import ExitStack
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch

from tests import release_extraction_diagnostic as previous
from tests import packaged_runtime_tests as packaged
from tests import review_extraction_contract_release as review

ROOT = previous.ROOT
BASE = review.BASE
OUT, PUBLISHED = BASE / "final", BASE / "published"
PRIOR = review.PRIOR
COUNT, ADDED = 1331, 87
VERSION_REPLACEMENTS = {
    "tests.test_analysis_answer_types_offline.AnalysisAnswerTypesTests.test_analysis_text_version_does_not_change_other_phase_protocols":
        "tests.test_extraction_contract_version_offline.ExtractionContractVersionTests.test_analysis_text_version_does_not_change_other_phase_protocols_v26",
    "tests.test_calculation_shared_contract_offline.CalculationSharedContractTests.test_optional_empty_objects_and_required_fields_match_validator":
        "tests.test_extraction_contract_version_offline.ExtractionContractVersionTests.test_optional_empty_objects_and_required_fields_match_validator_v26",
    "tests.test_extraction_binding_guidance_offline.ExtractionGuidanceVersionTests.test_conversation_protocol_version_is_separate_and_recorded_v25":
        "tests.test_extraction_contract_version_offline.ExtractionContractVersionTests.test_conversation_protocol_version_is_separate_and_recorded_v26",
    "tests.test_extraction_binding_guidance_offline.ExtractionGuidanceVersionTests.test_knowledge_revision_preserves_original_entry_and_arithmetic_version_v25":
        "tests.test_extraction_contract_version_offline.ExtractionContractVersionTests.test_knowledge_revision_preserves_original_entry_and_arithmetic_version_v26",
}


def driver():
    value = previous.driver()
    value.update(BASE=BASE, OUT=OUT, PUBLISHED=PUBLISHED, PRIOR=PRIOR, COUNT=COUNT, ADDED=ADDED,
                 MODULES=[*value["MODULES"], *review.ADDITIONS], regression=regression)
    return value


def regression():
    value = previous.driver()
    checked = value["read"](BASE / "review.json")
    path = Path(checked["runtime_receipt"])
    assert value["digest"](path) == checked["runtime_sha256"]
    result = value["read"](path)
    value["final"].current(result)
    assert result["tests_run"] == len(result["unique_test_ids"]) == COUNT
    expected = set(checked["legacy_failures_retained"]) | set(VERSION_REPLACEMENTS)
    assert {item["id"] for item in result["failures"]} == expected
    assert not any(result[key] for key in ("errors", "skipped", "external_attempts"))
    assert result["sources_unchanged_during_run"] and result["frozen_live_runs_unchanged"]
    assert all(value["digest"](p) == h for p, h in checked["protected_real_runs"].items())
    host = value["read"](ROOT / "result/acceptance/host-boundary-release/final/host-delivery.json")
    old_runtime = value["read"](host["runtime_receipt"])
    return result | {"receipt": str(path), "obsolete_assertion": old_runtime["obsolete_assertion"]}


def production_boundary(*, finalize=False):
    return driver()["production_boundary"](finalize=finalize)


def build():
    regression()
    value = driver()
    previous_release = value["read"](PRIOR)
    release = value["RELEASE"]
    artifacts = {"ResearchHelper-0.10.0-arm64.dmg": previous_release["dmg_sha256"],
                 "ResearchHelper-Skill-0.10.0.zip": previous_release["skill_sha256"]}
    assert all(value["digest"](release / name) == h for name, h in artifacts.items())
    backup = ROOT / "result/release-backups/20261008-extraction-contract"
    assert not backup.exists(), "Never replace a recovery copy"
    backup.mkdir(parents=True)
    for name in (*artifacts, "release-manifest.json", "app-manifest.json"):
        shutil.copyfile(release / name, backup / name)
        assert value["digest"](backup / name) == value["digest"](release / name)
    shutil.copytree(ROOT / ".skills/researchhelper", backup / "installed-project-skill",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    record = {"status": "building", "backup": str(backup), "app_installed": False,
              "demo_synced": False, "full_harness_complete": False}
    value["save"](OUT / "build.json", record)
    try:
        record.update(status="built_candidate_not_installed", artifacts=value["dispatch"].build(),
                      source_hashes=value["read"](release / "release-manifest.json")["source_hashes"])
    except BaseException:
        record["status"] = "build_failed"
        raise
    finally:
        value["save"](OUT / "build.json", record)
    return {key: record[key] for key in ("status", "backup", "app_installed", "demo_synced")}


def packaged_versions(kind, location, root, output_directory, test_modules, expected_tests):
    loader = unittest.TestLoader()
    names = []
    def collect(suite):
        for item in suite:
            if isinstance(item, unittest.TestSuite):
                collect(item)
            else:
                names.append(item.id())
    collect(loader.loadTestsFromNames(test_modules))
    assert not loader.errors and len(names) == expected_tests
    routed = [VERSION_REPLACEMENTS.get(name, name) for name in names]
    result = packaged.run_packaged_tests(kind, location, root, output_directory, routed, expected_tests)
    result["superseded_version_tests"] = [{"prior": name, "replacement": VERSION_REPLACEMENTS[name]}
                                          for name in names if name in VERSION_REPLACEMENTS]
    result["unique_test_count"] = len(set(routed))
    return result


def package_checks():
    regression()
    value = driver()
    with ExitStack() as stack:
        stack.enter_context(patch.object(value["retained"], "regression", regression))
        # Imported aliases in retained delivery helpers use the same explicit
        # inventory routing. No assertion, rule constant, or receipt is patched.
        for name, module in list(sys.modules.items()):
            if name.startswith("tests.") and module is not packaged and \
                    getattr(module, "run_packaged_tests", None) is packaged.run_packaged_tests:
                stack.enter_context(patch.object(module, "run_packaged_tests", packaged_versions))
        with value["batch"]():
            result = value["retained"].package_checks()
    assert result["skill_test_executions"] == result["app_test_executions"] == 314
    return value["save"](PUBLISHED / "extraction-contract-package.json", result | {
        "version_replacements": VERSION_REPLACEMENTS, "release_driver_sha256": value["digest"](__file__),
        "prior_test_files_modified": False})


def publish():
    runtime = regression()
    value = driver()
    package = value["read"](PUBLISHED / "extraction-contract-package.json")
    value["final"].current(package)
    assert package["status"] == "passed" and package["release_driver_sha256"] == value["digest"](__file__)
    assert package["skill_test_executions"] == package["app_test_executions"] == 314
    gate = value["retained"].retained.retained.browser_gate
    with patch.object(gate, "BASE", BASE):
        browser = gate.browser_evidence()
    assert sum(item["checks"] for item in browser) == 152
    paths = {item["name"]: Path(item["receipt"]) for item in browser}
    reused = {}
    for folder, count in (("source-readiness-ui", 18), ("task-selection-cycle", 19)):
        candidates = [(p, value["read"](p)) for p in (ROOT / "result/acceptance" / folder).glob("browser-*/receipt.json")]
        candidates = [(p, r) for p, r in candidates if r["status"] == "passed" and len(r["checks"]) == count
                      and r["source_hashes"]["app/web/workspace.js"] == value["digest"](ROOT / "products/app/frontend/workspace.js")]
        assert candidates
        path, receipt = max(candidates, key=lambda item: item[0].stat().st_mtime)
        assert not any(receipt.get(key) for key in ("failures", "errors", "external_attempts", "external"))
        assert receipt["backend_stopped"] and (receipt.get("browser_stopped") or receipt.get("browser_closed"))
        reused[folder] = {"receipt": str(path), "sha256": value["digest"](path), "checks": count, "mode": "reused"}
    final = value["final"]
    installer = value["dispatch"].load_script("packaging/install_release.py")
    sync = value["dispatch"].load_script("packaging/sync_demo.py")
    with patch.object(final, "OUT", OUT), patch.object(final, "PUBLISHED", PUBLISHED):
        with final.exact_receipts(writes={final.ACCEPTANCE / "installation.json": OUT / "installation.json"}):
            installer.install()
        with final.exact_receipts(reads={
            final.ACCEPTANCE / "package-background.json": PUBLISHED / "package-background.json",
            final.ACCEPTANCE / "integrity.json": OUT / "integrity.json",
            final.ACCEPTANCE / "autonomous-composer/full-chat/acceptance.json": paths["chat"],
            final.ACCEPTANCE / "roles-workspace/acceptance.json": paths["roles"]},
            writes={final.ACCEPTANCE / "demo-sync-background.json": OUT / "demo-sync.json"}):
            sync.sync(background_candidate=True)
        installed = final.installed_readback(OUT / "installation.json", OUT / "demo-sync.json")
    release = value["read"](value["RELEASE"] / "release-manifest.json")
    record = {"status": "offline_candidate_with_legacy_and_version_assertion_conflicts",
        "batch": "extraction-contract-release", "offline_tests_run": COUNT,
        "offline_tests_passed": COUNT - len(runtime["failures"]),
        "legacy_assertion_failures": runtime["failures"], "runtime_receipt": runtime["receipt"],
        "runtime_receipt_sha256": value["digest"](runtime["receipt"]), "browser_evidence": browser,
        "browser_rerun_checks": 152, "reused_browser_checks": reused, "packaged_checks": package,
        "installed": installed, "source_hashes": release["source_hashes"], "prior_release_receipt": str(PRIOR),
        "dmg_sha256": release["artifacts"]["dmg"]["sha256"],
        "skill_sha256": release["artifacts"]["skill"]["sha256"],
        "active_providers": ["ifind", "tinyshare"], "foreground_app_launched": False,
        "production_store_touched": False, "real_model_calls": 0, "real_supplier_calls": 0,
        "native_ui_verified": False, "full_harness_complete": False}
    value["save"](OUT / "extraction-contract-delivery.json", record)
    return {key: record[key] for key in ("status", "offline_tests_run", "offline_tests_passed",
                                        "browser_rerun_checks", "dmg_sha256", "skill_sha256")}
