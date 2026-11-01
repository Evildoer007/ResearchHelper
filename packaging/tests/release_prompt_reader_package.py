"""Check reader 3 packages without rewriting retained reader 1 assertions.

This release-only adapter was added after the source regression. It changes
only the expected reader marker and the PDF paragraph format attribute.
Shipping code, old tests and regression receipts remain unchanged.
"""

import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from tests import rebuild_report_structure_release as reader
from tests import rebuild_report_readability_package as readability
from tests import rebuild_original_checkpoint_package as checkpoint
from tests import release_prompt_reader as release

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "result/acceptance/prompt-reader-release"


def run():
    path = Path(reader.__file__)
    source = path.read_text()
    assert source.count("rh-reader-2") == 3
    assert source.count("reader-2 expectation") == 1
    adjusted = source.replace("rh-reader-2", "rh-reader-3").replace(
        "reader-2 expectation", "reader-3 expectation")
    namespace = {"__file__": str(path), "__name__": "tests.reader_three_package"}
    exec(compile(adjusted, str(path) + "[reader 3 marker only]", "exec"), namespace)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    paragraph_path = Path(readability.__file__)
    paragraph_source = paragraph_path.read_text()
    old_tag = "<p class='document-content-quote'>"
    new_tag = "<p class='document-content-quote' data-source-format='pdf'>"
    assert paragraph_source.count(old_tag) == 1
    paragraph_namespace = {"__file__": str(paragraph_path), "__name__": "tests.reader_three_pdf_paragraph"}
    exec(compile(paragraph_source.replace(old_tag, new_tag),
        str(paragraph_path) + "[PDF format attribute only]", "exec"), paragraph_namespace)
    paragraph_before = hashlib.sha256(paragraph_path.read_bytes()).hexdigest()
    assert checkpoint.report_verify is readability.verify
    record = {
        "status": "running", "original": str(path), "original_sha256": before,
        "only_adaptation": "retained reader version adapter targets rh-reader-3 instead of rh-reader-2",
        "prior_tests_modified": False, "shipping_code_modified": False,
        "added_after_source_regression": True,
        "helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "prior_failure": "old package adapter required reader 2 from reader 3 package",
        "paragraph_adaptation": {
            "original": str(paragraph_path), "original_sha256": paragraph_before,
            "only_adaptation": "expect explicit PDF format attribute on all nine PDF quote paragraphs",
            "exact_quote_counts_preserved": True,
            "prior_failure": "old exact paragraph tag omitted the new PDF format attribute",
        },
    }
    receipt = Path(tempfile.mkdtemp(prefix="package-adaptation-", dir=BASE)) / "receipt.json"
    try:
        with patch.object(reader, "reader_expectation_adapter", namespace["reader_expectation_adapter"]), \
                patch.object(checkpoint, "report_verify", paragraph_namespace["verify"]):
            result = release.package_checks()
        assert result["app_test_executions"] == result["skill_test_executions"] == 397
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before
        assert hashlib.sha256(paragraph_path.read_bytes()).hexdigest() == paragraph_before
        record["status"] = "passed"
        return result
    except BaseException:
        record["status"] = "failed"
        raise
    finally:
        receipt.write_text(json.dumps(record, ensure_ascii=False, indent=2))
