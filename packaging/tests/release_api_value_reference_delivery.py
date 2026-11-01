"""Deliver the verified value decoder without altering retained test assertions.

Reuse existing package/browser/install gates with explicit isolated output
routes. Source regression and late delivery execution remain separate evidence.
No foreground App, shared credentials or real external requests.
"""

import hashlib
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from zipfile import ZipFile

from tests import release_document_delivery as retained
from tests import release_prompt_reader as package_reader
from tests import review_api_value_reference as source_review
from tests.packaged_runtime_tests import run_packaged_tests

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "result/acceptance/api-value-reference-release"
OUT = BASE / "final"
PRIOR = ROOT / "result/acceptance/document-delivery-release/final/document-delivery-delivery.json"
GATE = ROOT / "result/acceptance/api-value-reference/source-gate-ilvph2h5/receipt.json"
RELEASE = ROOT / "dist/0.10.0"


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, record):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    return record


def source_gate():
    gate = read(GATE)
    path = Path(gate["runtime_receipt"])
    assert digest(path) == gate["runtime_sha256"]
    runtime = read(path)
    assert runtime["tests_run"] == len(runtime["unique_test_ids"]) == 1504
    assert {item["id"] for item in runtime["failures"]} == {item["id"] for item in read(PRIOR)["legacy_assertion_failures"]}
    assert not any(runtime[key] for key in ("errors", "skipped", "external_attempts"))
    assert runtime["sources_unchanged_during_run"] and runtime["frozen_live_runs_unchanged"]
    assert all(digest(ROOT / name) == value for name, value in runtime["source_hashes"].items())
    assert all(digest(ROOT / name) == value for name, value in gate["additional_inputs"].items())
    host = read(ROOT / "result/acceptance/host-boundary-release/final/host-delivery.json")
    return runtime | {"receipt": str(path), "obsolete_assertion": read(host["runtime_receipt"])["obsolete_assertion"]}


def namespace():
    value = retained.driver()
    value.update(BASE=BASE, OUT=OUT, PUBLISHED=BASE / "published", PRIOR=PRIOR, regression=source_gate)
    return value


def production_files():
    directory = Path("/Users/haoranxu/Library/Application Support/ResearchHelper")
    return {str(path): digest(path) for path in directory.rglob("*") if path.is_file()}


def build():
    source_gate()
    assert not (OUT / "build.json").exists(), "Keep previous stage evidence; do not overwrite a build outcome"
    save(OUT / "production-before.json", production_files())
    return namespace()["build"]()


def package_checks():
    original_driver = package_reader.driver

    def reader_four():
        value = original_driver()
        value["VERSION_REPLACEMENTS"][retained.review.REVISION_TEST] = (
            "tests.test_pdf_reader_revision_4_offline.PDFReaderRevision4Tests."
            "test_reader_revision_changes_no_frozen_content_template")
        return value

    with patch.object(package_reader, "driver", reader_four):
        baseline = namespace()["package_checks"]()
    assert baseline["app_test_executions"] == baseline["skill_test_executions"] == 459
    with tempfile.TemporaryDirectory(prefix="value-package-", dir=BASE) as folder:
        with ZipFile(RELEASE / "ResearchHelper-Skill-0.10.0.zip") as archive:
            assert all(not Path(name).is_absolute() and ".." not in Path(name).parts for name in archive.namelist())
            archive.extractall(folder)
        backend = RELEASE / "ResearchHelper.app/Contents/Resources/backend/researchhelper-server/researchhelper-server"
        contexts = {kind: run_packaged_tests(kind, location, ROOT, BASE, ["tests.test_api_value_reference_offline"], 22)
                    for kind, location in (("skill", Path(folder) / "researchhelper"), ("app", backend))}
    result = read(BASE / "published/document-delivery-package.json") | {
        "batch": "api-value-reference-release", "new_value_contexts": contexts,
        "retained_executions_per_kind": 459, "app_test_executions": 481, "skill_test_executions": 481,
        "late_delivery_helper": str(Path(__file__).resolve()), "late_delivery_helper_sha256": digest(__file__),
        "late_helper_counted_in_source_inventory": False}
    save(BASE / "published/api-value-reference-package.json", result)
    return {"status": "passed", "app_test_executions": 481, "skill_test_executions": 481}


def publish():
    source_gate()
    value = namespace()["namespace"]()
    value.update(BASE=BASE, OUT=OUT, PUBLISHED=BASE / "published", PRIOR=PRIOR,
                 COUNT=1504, ADDED=260, regression=source_gate)
    # The retained publisher reads its retained gate filename. The separate
    # current receipt preserves the additional 22 checks and provenance.
    package = read(BASE / "published/api-value-reference-package.json")
    assert package["status"] == "passed" and package["app_test_executions"] == package["skill_test_executions"] == 481
    value["publish"]()
    result = read(OUT / "document-delivery-delivery.json") | {
        "batch": "api-value-reference-release", "packaged_checks": package,
        "late_delivery_helper_sha256": digest(__file__), "late_helper_counted_in_source_inventory": False}
    before = read(OUT / "production-before.json")
    assert before == production_files(), "Production store changed during delivery"
    result["production_files_unchanged"] = True
    result["production_file_count"] = len(before)
    save(OUT / "api-value-reference-delivery.json", result)
    return {"status": result["status"], "source_tests": result["offline_tests_run"],
            "installed": result["installed"], "dmg_sha256": result["dmg_sha256"],
            "skill_sha256": result["skill_sha256"]}
