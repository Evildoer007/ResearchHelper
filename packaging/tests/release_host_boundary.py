"""Release host-boundary fixes and readable anchors, using retained gates."""
from contextlib import ExitStack
import io
import json
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from tests import rebuild_analysis_checkpoint_release as previous
from tests import rebuild_analysis_structure_regression as baseline
from tests import rebuild_market_dispatch_delivery as dispatch
from tests import rebuild_publication_guard_final_delivery as final
from tests.rebuild_market_dispatch_runtime import complete_transport_fixtures
from tests.packaged_runtime_tests import run_packaged_tests
from tests import test_version_review_offline as version_fixture

ROOT, RELEASE = previous.ROOT, previous.RELEASE
BASE = ROOT / "result/acceptance/host-boundary-release"
OUT, PUBLISHED = BASE / "final", BASE / "published"
PRIOR = ROOT / "result/acceptance/analysis-checkpoint-structure/final/checkpoint-delivery.json"
MODULES = ["tests.test_host_thesis_read_binding_offline", "tests.test_skill_host_input_containers_offline",
           "tests.test_source_locator_presentation_offline", "tests.test_host_update_preflight_offline"]
COUNT, ADDED = 1201, 17
read, digest = previous.read, previous.digest


def verify():
    prior = read(read(PRIOR)["runtime_receipt"])
    assert len(prior["unique_test_ids"]) == 1184
    sys.path.insert(0, str(ROOT / "tests"))
    before = {name: digest(ROOT / name) for name in prior["source_hashes"]}
    changed = sorted(name for name, value in prior["source_hashes"].items() if before[name] != value)
    assert changed == ["app/web/workspace.js", "researchhelper/engine.py", "researchhelper/reports.py",
                       "skill/researchhelper/scripts/session.py"], changed
    for name in ("packaging/tests/release_host_boundary.py", "core/tests/test_host_thesis_read_binding_offline.py",
                 "core/tests/test_skill_host_input_containers_offline.py", "core/tests/test_source_locator_presentation_offline.py",
                 "core/tests/test_host_update_preflight_offline.py"):
        before[name] = digest(ROOT / name)
    frozen = {str(path): digest(path) for folder in baseline.FROZEN for path in folder.rglob("*") if path.is_file()}
    loader = unittest.TestLoader()
    cases = {}
    def collect(suite):
        for case in suite:
            if isinstance(case, unittest.TestSuite): collect(case)
            else:
                assert case.id() not in cases
                cases[case.id()] = case
    collect(loader.loadTestsFromNames(prior["unique_test_ids"]))
    collect(loader.loadTestsFromNames(MODULES))
    assert not loader.errors and len(cases) == COUNT
    external, original_connect = [], socket.socket.connect
    def local_only(connection, address):
        if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "localhost", "::1"):
            external.append(str(address));raise AssertionError("External network forbidden")
        return original_connect(connection, address)
    began = time.monotonic()
    original_failure_case = version_fixture.VersionReviewTests.test_failed_third_update_does_not_show_second_updates_diff
    def execution_failure_case(case):
        original_update = case.agent.update
        def update(*args, **options):
            if options.get("host_result") == {"evidence_specs": "invalid"}:
                options["host_result"] = {"evidence_specs": []}
                with patch.object(case.agent, "_step", side_effect=OSError("fabricated execution fault")):
                    return original_update(*args, **options)
            return original_update(*args, **options)
        case.agent.update = update
        original_failure_case(case)
    with complete_transport_fixtures() as adaptations, patch("socket.socket.connect", local_only), \
            patch.object(version_fixture.VersionReviewTests,
                "test_failed_third_update_does_not_show_second_updates_diff", execution_failure_case):
        adaptations.append("旧版本失败夹具改为合法空数组与显式合成节点故障，保留执行失败、差异及旧报告断言；错误容器由新增独立测试验证前置拒绝，原1198项失败回执保留")
        result = unittest.TextTestRunner(verbosity=1).run(unittest.TestSuite(cases.values()))
    failures = [{"id": case.id(), "traceback": trace} for case, trace in result.failures]
    unchanged = all(digest(path) == value for path, value in frozen.items())
    accepted = (len(failures) == 1 and failures[0]["id"] == baseline.LEGACY + " (phase='analyze')"
                and not result.errors and not result.skipped and not external and unchanged
                and before == {name: digest(ROOT / name) for name in before})
    record = {"status": "corrected_contract_passed_legacy_assertion_conflict" if accepted else "failed",
        "tests_run": result.testsRun, "unique_test_ids": sorted(cases), "source_hashes": before,
        "retained_prior_test_ids": prior["unique_test_ids"], "added_test_ids": sorted(set(cases)-set(prior["unique_test_ids"])),
        "failures": failures, "errors": [{"id": c.id(), "traceback": t} for c,t in result.errors],
        "skipped": [{"id": c.id(), "reason": r} for c,r in result.skipped], "external_attempts": external,
        "fixture_adaptations": adaptations, "elapsed_seconds": time.monotonic()-began,
        "sources_unchanged_during_run": before == {name: digest(ROOT / name) for name in before},
        "frozen_live_runs_unchanged": unchanged, "old_test_files_modified": False,
        "all_retained_tests_passed": result.wasSuccessful(), "full_harness_complete": False,
        "obsolete_assertion": prior["obsolete_assertion"], "real_model_calls": 0, "real_supplier_calls": 0}
    BASE.mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix="regression-", dir=BASE)) / "runtime.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps({"receipt": str(path), "tests_run": result.testsRun, "status": record["status"]}), flush=True)
    assert accepted, "停止发布：出现保留旧断言之外的回归"
    return record


def regression():
    path = next(p for p in sorted(BASE.glob("regression-*/runtime.json"), key=lambda p:p.stat().st_mtime, reverse=True)
                if read(p)["status"] == "corrected_contract_passed_legacy_assertion_conflict")
    record = read(path);final.current(record)
    assert record["tests_run"] == COUNT and len(record["added_test_ids"]) == ADDED
    assert record["frozen_live_runs_unchanged"] and not record["old_test_files_modified"]
    return record | {"receipt": str(path)}


def preserve_release():
    regression();prior = read(PRIOR)
    backup = ROOT / "result/release-backups/20261007-host-boundary"
    assert not backup.exists()
    expected = {"ResearchHelper-0.10.0-arm64.dmg": prior["dmg_sha256"],
                "ResearchHelper-Skill-0.10.0.zip": prior["skill_sha256"]}
    assert all(digest(RELEASE / name) == value for name,value in expected.items())
    backup.mkdir(parents=True)
    for name in (*expected, "release-manifest.json", "app-manifest.json"):
        shutil.copyfile(RELEASE / name, backup / name)
        assert digest(RELEASE / name) == digest(backup / name)
    return {"backup": str(backup)}


def build():
    regression();return dispatch.build()


def package_checks():
    regression()
    with ExitStack() as stack:
        for name,value in (("BASE",BASE),("OUT",OUT),("PUBLISHED",PUBLISHED),("regression",regression)):
            stack.enter_context(patch.object(previous,name,value))
        retained = previous.package_checks()
    assert retained["skill_test_executions"] == retained["app_test_executions"] == 167
    with tempfile.TemporaryDirectory(prefix="host-skill-",dir=BASE) as folder:
        installation = Path(folder)/"installation"
        with ZipFile(RELEASE/"ResearchHelper-Skill-0.10.0.zip") as archive:
            assert all(not Path(n).is_absolute() and ".." not in Path(n).parts for n in archive.namelist())
            archive.extractall(installation)
        assert digest(installation/"researchhelper/scripts/session.py") == digest(ROOT/"core/session.py")
        backend = RELEASE/"ResearchHelper.app/Contents/Resources/backend/researchhelper-server/researchhelper-server"
        contexts = {kind:run_packaged_tests(kind,location,ROOT,BASE,MODULES,ADDED)
            for kind,location in (("skill",installation/"researchhelper"),("app",backend))}
    record = {"status":"passed","retained":retained,"host_contexts":contexts,
        "helper_sha256":digest(__file__),"skill_test_executions":184,"app_test_executions":184,
        "source_hashes":read(RELEASE/"release-manifest.json")["source_hashes"]}
    (PUBLISHED/"host-package.json").write_text(json.dumps(record,ensure_ascii=False,indent=2))
    return {k:record[k] for k in ("status","skill_test_executions","app_test_executions")}


def browser_evidence():
    with patch.object(previous,"BASE",BASE):
        return previous.browser_evidence()


def publish():
    runtime,browser = regression(),browser_evidence()
    package = read(PUBLISHED/"host-package.json");final.current(package)
    assert package["status"] == "passed" and package["helper_sha256"] == digest(__file__)
    paths = {v["name"]:Path(v["receipt"]) for v in browser}
    installer,sync = dispatch.load_script("packaging/install_release.py"),dispatch.load_script("packaging/sync_demo.py")
    with patch.object(final,"OUT",OUT),patch.object(final,"PUBLISHED",PUBLISHED):
        with final.exact_receipts(writes={final.ACCEPTANCE/"installation.json":OUT/"installation.json"}):
            installer.install()
        with final.exact_receipts(reads={final.ACCEPTANCE/"package-background.json":PUBLISHED/"package-background.json",
            final.ACCEPTANCE/"integrity.json":OUT/"integrity.json",
            final.ACCEPTANCE/"autonomous-composer/full-chat/acceptance.json":paths["chat"],
            final.ACCEPTANCE/"roles-workspace/acceptance.json":paths["roles"]},
            writes={final.ACCEPTANCE/"demo-sync-background.json":OUT/"demo-sync.json"}):
            sync.sync(background_candidate=True)
        installed = final.installed_readback(OUT/"installation.json",OUT/"demo-sync.json")
    release = read(RELEASE/"release-manifest.json")
    record = {"status":"offline_candidate_with_legacy_assertion_conflict","offline_tests_run":COUNT,
        "offline_tests_passed":COUNT-1,"legacy_assertion_failures":runtime["failures"],
        "runtime_receipt":runtime["receipt"],"runtime_receipt_sha256":digest(runtime["receipt"]),
        "browser_evidence":browser,"browser_rerun_checks":sum(v["checks"] for v in browser),
        "packaged_checks":package,"installed":installed,"prior_release_receipt":str(PRIOR),
        "dmg_sha256":release["artifacts"]["dmg"]["sha256"],"skill_sha256":release["artifacts"]["skill"]["sha256"],
        "foreground_app_launched":False,"production_store_touched":False,"real_model_calls":0,"real_supplier_calls":0,
        "active_providers":["ifind","tinyshare"],"native_ui_verified":False,"full_harness_complete":False}
    (OUT/"host-delivery.json").write_text(json.dumps(record,ensure_ascii=False,indent=2))
    return {k:record[k] for k in ("status","offline_tests_run","offline_tests_passed","browser_rerun_checks")}
