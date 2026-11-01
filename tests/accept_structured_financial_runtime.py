"""Same financial-query contract in source, independent Skill and frozen App.

Keep all previous regression modules; no production Store or provider access.
The larger source cohort has a bounded90-second harness timeout, not a change
to any research, transport, model or user execution budget.
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from tests import accept_deepseek_stream_runtime as previous
from tests.accept_delivered_ifind_no_data import run_packaged_tests

MODULES = ["test_identity_period_current_guidance" if name == "test_identity_period_provenance" else name
           for name in previous.MODULES] + ["test_ifind_financial_query_contract"]
TEST_COUNT = previous.TEST_COUNT + 20


def run():
    output = Path(tempfile.mkdtemp(prefix="structured-financial-runtime-", dir=ROOT / "result/acceptance"))
    record = {"status": "failed", "model_calls": 0, "provider_calls": 0,
              "native_window_opened": False, "production_store_touched": False,
              "original_tests_modified": False, "installed": False,
              "source_and_skill_only": True, "app_accepted": False,
              "financial_quality_accepted": False}
    try:
        result = subprocess.run([sys.executable, "-B", "-m", "unittest",
            *["tests." + module for module in MODULES]], cwd=ROOT,
            capture_output=True, text=True, timeout=90)
        (output / "source-tests.log").write_text(result.stdout + result.stderr)
        assert result.returncode == 0, result.stderr
        assert "Ran " + str(TEST_COUNT) + " tests" in result.stderr
        record["source_tests"] = TEST_COUNT
        with tempfile.TemporaryDirectory(prefix="payload-", dir=output) as temporary:
            spec = importlib.util.spec_from_file_location("structured_financial_skill_builder", ROOT / "packaging/skill/build_skill.py")
            builder = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(builder)
            builder.skill(output=Path(temporary), install=False)
            record["skill"] = run_packaged_tests("skill", Path(temporary) / "researchhelper",
                                                 ROOT, output, MODULES, TEST_COUNT)
        record["temporary_payload_removed"] = not any(output.glob("payload-*"))
        record["status"] = "scoped_passed"
    except BaseException as error:
        record.update(error_type=type(error).__name__, error=str(error))
        raise
    finally:
        target = output / "receipt.json"
        target.write_text(json.dumps(record, ensure_ascii=False, indent=2))
        print(json.dumps({"status": record["status"], "receipt": str(target),
                          "error": record.get("error")}, ensure_ascii=False))
    return record


def run_frozen():
    original = previous.MODULES, previous.TEST_COUNT
    try:
        previous.MODULES, previous.TEST_COUNT = MODULES, TEST_COUNT
        return previous.run_frozen()
    finally:
        previous.MODULES, previous.TEST_COUNT = original


if __name__ == "__main__":
    run()
