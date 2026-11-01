"""Batch checkpoint fixes and document reader 4 through retained release gates.

No original tests, supplier authorization or privacy boundary is weakened.
Reader-only package expectations follow the declared reader revision.
"""
from pathlib import Path
from unittest.mock import patch
from tests import release_scope_submission as previous
from tests import release_prompt_reader as package_reader
from tests import review_document_delivery_release as review

ROOT = Path(__file__).resolve().parents[2]
BASE = review.BASE


def regression():
    original = Path(previous.__file__)
    source = original.read_text()
    anchor = 'item["id"] for item in previous["legacy_assertion_failures"]}'
    assert source.count(anchor) == 1
    source = source.replace(anchor, anchor + ' | {REVISION_TEST}')
    source = source.replace("1434", "1481")
    namespace = {"__file__": str(original), "__name__": "tests.document_delivery_inventory"}
    exec(compile(source, str(original), "exec"), namespace)
    namespace.update(BASE=BASE, PRIOR=review.PRIOR, REVISION_TEST=review.REVISION_TEST)
    return namespace["regression"]()


def driver():
    original = Path(previous.__file__)
    source = original.read_text()
    replacements = (
        ("scope-submission", "document-delivery"),
        ("prompt-reader-release/final/prompt-reader-delivery.json",
         "financial-filing-release/final/financial-filing-delivery.json"),
        ("1434", "1481"), ("412", "459"),
        ("COUNT, ADDED = 1481, 190", "COUNT, ADDED = 1481, 237"),
        ("PACKAGE_ADDITIONS, 15", "PACKAGE_ADDITIONS, 62"),
        ('compile(adapter.read_text(), str(adapter), "exec")',
         'compile(READER_ADAPTER_SOURCE, str(adapter), "exec")'),
        ('"package-execution.log"', '"package-reader4-execution.log"'),
    )
    for before, after in replacements:
        assert before in source, before
        source = source.replace(before, after)
    namespace = {"__file__": __file__, "__name__": "tests.document_delivery_release"}
    exec(compile(source, str(original), "exec"), namespace)
    adapter_path = ROOT / "packaging/tests/release_prompt_reader_package.py"
    adapter = adapter_path.read_text()
    assert '"rh-reader-3"' in adapter
    namespace.update(regression=regression,
        READER_ADAPTER_SOURCE=adapter.replace("rh-reader-3", "rh-reader-4").replace("reader-3", "reader-4"))
    namespace["PACKAGE_ADDITIONS"] = (*namespace["PACKAGE_ADDITIONS"],
        "tests.test_extraction_registry_scope_offline", "tests.test_financial_filing_bridge_offline",
        "tests.test_financial_metric_binding_offline", "tests.test_financial_amount_units_offline",
        "tests.test_checkpoint_dependency_replay_offline", "tests.test_document_report_index_offline")
    assert namespace["BASE"] == BASE and namespace["PRIOR"] == review.PRIOR
    return namespace


def build():
    return driver()["build"]()


def package_checks():
    original_driver = package_reader.driver

    def reader_four_driver():
        namespace = original_driver()
        namespace["VERSION_REPLACEMENTS"][review.REVISION_TEST] = (
            "tests.test_pdf_reader_revision_4_offline.PDFReaderRevision4Tests."
            "test_reader_revision_changes_no_frozen_content_template")
        return namespace

    with patch.object(package_reader, "driver", reader_four_driver):
        return driver()["package_checks"]()


def publish():
    return driver()["publish"]()
