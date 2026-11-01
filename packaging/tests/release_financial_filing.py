"""Publish the financial-original batch through retained offline release gates.

Route inventories and receipt locations only. Supplier authorization, source
verification, signing, installation and privacy assertions are not adapted.
"""
from pathlib import Path
from tests import release_scope_submission as previous
from tests import review_financial_filing_bridge as review

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "result/acceptance/financial-filing-release"


def driver():
    original = Path(previous.__file__)
    source = original.read_text()
    replacements = (
        ("scope-submission", "financial-filing"),
        ("prompt-reader-release/final/prompt-reader-delivery.json",
            "scope-snapshot-release/final/scope-snapshot-delivery.json"),
        ("1434", "1468"), ("412", "446"),
        ("COUNT, ADDED = 1468, 190", "COUNT, ADDED = 1468, 224"),
        ("PACKAGE_ADDITIONS, 15", "PACKAGE_ADDITIONS, 49"),
        ('"helper_created_after_source_inventory": True', '"helper_created_after_source_inventory": False'),
    )
    for before, after in replacements:
        assert before in source, before
        source = source.replace(before, after)
    namespace = {"__file__": __file__, "__name__": "tests.financial_filing_delivery"}
    exec(compile(source, str(original), "exec"), namespace)
    namespace["PACKAGE_ADDITIONS"] = (*namespace["PACKAGE_ADDITIONS"],
        "tests.test_extraction_registry_scope_offline", "tests.test_financial_filing_bridge_offline",
        "tests.test_financial_metric_binding_offline", "tests.test_financial_amount_units_offline")
    assert namespace["BASE"] == BASE
    assert namespace["PRIOR"] == ROOT / "result/acceptance/scope-snapshot-release/final/scope-snapshot-delivery.json"
    return namespace


def source_checks():
    review.BASE = BASE
    return review.review()


def build():
    return driver()["build"]()


def package_checks():
    return driver()["package_checks"]()


def publish():
    return driver()["publish"]()
