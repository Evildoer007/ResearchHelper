"""Publish the combined candidate through retained package and installation gates.

Created after the source inventory: this release helper is not counted among
the initial 1433 executed tests. Existing reader adaptations and prior tests stay intact.
"""

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
from zipfile import ZipFile

from tests import release_prompt_reader as prior
from tests.packaged_runtime_tests import run_packaged_tests

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "result/acceptance/scope-submission-release"
OUT, PUBLISHED = BASE / "final", BASE / "published"
PRIOR = ROOT / "result/acceptance/prompt-reader-release/final/prompt-reader-delivery.json"
PACKAGE_ADDITIONS = ("tests.test_question_obligation_policy_offline", "tests.test_numeric_price_measure_scope_offline")


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    return value


def regression():
    candidates = [(path, read(path)) for path in BASE.glob("source-gate-*/receipt.json")]
    candidates = [(path, value) for path, value in candidates if value.get("status") == "passed_with_retained_failures"
                  and value.get("tests_run") == 1434]
    assert candidates, "No current 1434-test source gate"
    _, gate = max(candidates, key=lambda item: item[0].stat().st_mtime)
    path = Path(gate["runtime_receipt"])
    assert digest(path) == gate["runtime_sha256"]
    record = read(path)
    previous = read(PRIOR)
    assert record["tests_run"] == len(record["unique_test_ids"]) == 1434
    assert {item["id"] for item in record["failures"]} == {
        item["id"] for item in previous["legacy_assertion_failures"]}
    assert not any(record[key] for key in ("errors", "skipped", "external_attempts"))
    assert record["sources_unchanged_during_run"] and record["frozen_live_runs_unchanged"]
    assert all(digest(ROOT / name) == expected for name, expected in record["source_hashes"].items())
    assert all(digest(name) == expected for name, expected in gate["protected_records"].items())
    host = read(ROOT / "result/acceptance/host-boundary-release/final/host-delivery.json")
    return record | {"receipt": str(path), "obsolete_assertion": read(host["runtime_receipt"])["obsolete_assertion"]}


def namespace():
    template = ROOT / "packaging/tests/release_extraction_contract.py"
    source = template.read_text()
    routes = {
        "from tests import release_extraction_diagnostic as previous": "from tests import release_extraction_contract as previous",
        "from tests import review_extraction_contract_release as review": "from tests import review_prompt_reader_release as review",
        "COUNT, ADDED = 1331, 87": "COUNT, ADDED = 1434, 190",
        "*review.ADDITIONS": "*review.PACKAGE_ADDITIONS",
        "extraction-contract-package.json": "scope-submission-package.json",
        '"batch": "extraction-contract-release"': '"batch": "scope-submission-release"',
        "extraction-contract-delivery.json": "scope-submission-delivery.json",
        "== 314": "== 412",
    }
    for before, after in routes.items():
        assert before in source, before
        source = source.replace(before, after)
    value = {"__file__": __file__, "__name__": "tests.scope_submission_delivery"}
    exec(compile(source, str(template), "exec"), value)
    value.update(BASE=BASE, OUT=OUT, PUBLISHED=PUBLISHED, PRIOR=PRIOR, regression=regression)
    return value


def build():
    regression()
    previous = read(PRIOR)
    release = ROOT / "dist/0.10.0"
    expected = {"ResearchHelper-0.10.0-arm64.dmg": previous["dmg_sha256"],
                "ResearchHelper-Skill-0.10.0.zip": previous["skill_sha256"]}
    assert all(digest(release / name) == value for name, value in expected.items())
    backup = Path(tempfile.mkdtemp(prefix="20261008-scope-submission-", dir=ROOT / "result/release-backups"))
    for name in (*expected, "release-manifest.json", "app-manifest.json"):
        shutil.copyfile(release / name, backup / name)
        assert digest(backup / name) == digest(release / name)
    shutil.copytree(ROOT / ".skills/researchhelper", backup / "installed-project-skill",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    record = {"status": "building", "backup": str(backup), "app_installed": False, "demo_synced": False}
    try:
        from tests.rebuild_market_dispatch_delivery import build as build_release
        record.update(status="built_candidate_not_installed", artifacts=build_release(),
                      source_hashes=read(release / "release-manifest.json")["source_hashes"])
    except BaseException:
        record["status"] = "build_failed"
        raise
    finally:
        save(OUT / "build.json", record)
    return {key: record[key] for key in ("status", "backup", "app_installed", "demo_synced")}


def package_checks():
    regression()
    BASE.mkdir(parents=True, exist_ok=True)
    legacy = prior.driver()
    legacy.update(BASE=BASE, OUT=OUT, PUBLISHED=PUBLISHED, PRIOR=PRIOR, regression=regression)
    # Retain all 397 checks and the two documented reader-only adaptations.
    adapter = ROOT / "packaging/tests/release_prompt_reader_package.py"
    adapted = {"__file__": str(adapter), "__name__": "tests.scope_submission_reader_adapter"}
    exec(compile(adapter.read_text(), str(adapter), "exec"), adapted)
    adapted.update(BASE=BASE, release=SimpleNamespace(package_checks=legacy["package_checks"]))
    with (BASE / "package-execution.log").open("x") as log, redirect_stdout(log), redirect_stderr(log):
        retained = adapted["run"]()
        with tempfile.TemporaryDirectory(prefix="added-package-", dir=BASE) as folder:
            installation = Path(folder)
            with ZipFile(ROOT / "dist/0.10.0/ResearchHelper-Skill-0.10.0.zip") as archive:
                assert all(not Path(name).is_absolute() and ".." not in Path(name).parts for name in archive.namelist())
                archive.extractall(installation)
            backend = ROOT / "dist/0.10.0/ResearchHelper.app/Contents/Resources/backend/researchhelper-server/researchhelper-server"
            contexts = {kind: run_packaged_tests(kind, location, ROOT, BASE, PACKAGE_ADDITIONS, 15)
                        for kind, location in (("skill", installation / "researchhelper"), ("app", backend))}
    record = retained | {"status": "passed", "retained_package_executions_per_kind": 397,
                         "additional_contexts": contexts, "skill_test_executions": 412, "app_test_executions": 412,
                         "release_driver_sha256": digest(__file__),
                         "source_hashes": read(ROOT / "dist/0.10.0/release-manifest.json")["source_hashes"],
                         "helper_created_after_source_inventory": True, "full_harness_complete": False}
    save(PUBLISHED / "scope-submission-package.json", record)
    return {key: record[key] for key in ("status", "skill_test_executions", "app_test_executions")}


def publish():
    return namespace()["publish"]()


def complete_seal():
    """Resume the failed signing gate without repeating a successful runtime build."""
    regression()
    failed = read(OUT / "build.json")
    assert failed["status"] == "build_failed"
    backup = Path(failed["backup"])
    previous = read(PRIOR)
    for name, expected in (("ResearchHelper-0.10.0-arm64.dmg", previous["dmg_sha256"]),
                           ("ResearchHelper-Skill-0.10.0.zip", previous["skill_sha256"])):
        assert digest(backup / name) == expected
    save(OUT / "failed-build-before-seal-resume.json", failed)
    from tests.rebuild_market_dispatch_delivery import load_script
    builder = load_script("packaging/assemble_release.py")
    release = ROOT / "dist/0.10.0"
    skill = release / "ResearchHelper-Skill-0.10.0.zip"
    demo = release / "demo"
    # These resources were generated before the failed read-only image mount.
    assert all(digest(demo / name) == value for name, value in read(demo / "manifest.json")["files"].items())
    assert all(digest(ROOT / "core/src/researchhelper" / name) == digest(ROOT / ".skills/researchhelper/researchhelper" / name)
               for name in ("planning.py", "engine.py", "reasoning.py", "numeric_integrity.py"))
    disk = builder.seal(release / "ResearchHelper.app")
    artifacts = builder.release_record({"skill": str(skill), "demo": str(demo), "dmg": str(disk)})
    record = failed | {"status": "built_candidate_not_installed", "artifacts": artifacts,
                       "source_hashes": read(release / "release-manifest.json")["source_hashes"],
                       "resume_scope": "Seal existing runtime and verify read-only DMG; no repeated PyInstaller build",
                       "retained_failure": str(OUT / "failed-build-before-seal-resume.json")}
    save(OUT / "build.json", record)
    return {key: record[key] for key in ("status", "backup", "app_installed", "demo_synced")}
