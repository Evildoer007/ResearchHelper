"""Batch the shared App/Skill calculation contract with retained release gates.

Only change explicit output scopes, inventory counts and added test modules.
Do not repeat real research, alter old tests or interact with foreground UI.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "result/acceptance/calculation-contract-release"
PRIOR = ROOT / "result/acceptance/delivery-integrity-release/final/delivery-integrity-delivery.json"
ADDITIONS = ("tests.test_calculation_plan_shape_offline", "tests.test_calculation_shared_contract_offline")


def driver():
    original = ROOT / "packaging/tests/release_delivery_integrity.py"
    source = original.read_text()
    replacements = {
        "delivery-integrity": "calculation-contract",
        "COUNT, ADDED = 1293, 49": "COUNT, ADDED = 1308, 64",
        '"== 276"': '"== 291"',
        "'tests.test_authorized_tinyshare_sample_offline']":
            "'tests.test_authorized_tinyshare_sample_offline', "
            "'tests.test_calculation_plan_shape_offline', 'tests.test_calculation_shared_contract_offline']",
        "for folder in ('current-authorized-source-permissions', 'current-authorized-tinyshare-sample') ":
            "for folder in ('current-authorized-source-permissions', 'current-authorized-tinyshare-sample', "
            "'current-authorized-ifind-etf') ",
    }
    for before, after in replacements.items():
        assert before in source, "Retained release routing changed"
        source = source.replace(before, after)
    prior_before = '"\'result/acceptance/source-readiness-release/final/source-readiness-delivery.json\'",'
    assert source.count(prior_before) == 1
    source = source.replace(prior_before, '"\'result/acceptance/delivery-integrity-release/final/delivery-integrity-delivery.json\'",')
    namespace = {"__file__": __file__, "__name__": "tests.calculation_contract_release_in_memory"}
    exec(compile(source, str(original), "exec"), namespace)
    routed = namespace["_driver"]()
    assert routed["BASE"] == BASE and routed["PRIOR"] == PRIOR
    assert routed["OUT"] == BASE / "final" and routed["PUBLISHED"] == BASE / "published"
    return routed


def review():
    routed = driver()
    before = routed["real_records"]()
    original = ROOT / "packaging/tests/review_workflow_release.py"
    source = original.read_text()
    guard = "assert len(prior['unique_test_ids']) == 1201"
    assert source.count(guard) == 1
    namespace = {"__file__": str(original), "__name__": "tests.calculation_contract_review_in_memory"}
    exec(compile(source.replace(guard, "assert len(prior['unique_test_ids']) == 1293"), str(original), "exec"), namespace)
    namespace.update(BASE=BASE, PRIOR=PRIOR, ADDITIONS=ADDITIONS)
    result = namespace["main"]()
    prior = json.loads(PRIOR.read_text())
    assert result["tests_run"] == len(result["unique_test_ids"]) == 1308
    assert {item["id"] for item in result["failures"]} == {item["id"] for item in prior["legacy_assertion_failures"]}
    assert not any(result[key] for key in ("errors", "skipped", "external_attempts"))
    assert result["sources_unchanged_during_run"] and result["frozen_live_runs_unchanged"]
    assert routed["real_records"]() == before
    path = max(BASE.glob("regression-*/runtime.json"), key=lambda item: item.stat().st_mtime)
    return routed["save"](BASE / "review.json", {
        "runtime_receipt": str(path), "runtime_sha256": routed["digest"](path),
        "protected_real_runs": before, "tests_run": 1308, "tests_passed": 1304, "new_real_calls": 0})


def build():
    return driver()["build"]()


def package_checks():
    return driver()["package_checks"]()


def publish():
    return driver()["publish"]()


def production_boundary(*, finalize=False):
    return driver()["production_boundary"](finalize=finalize)
