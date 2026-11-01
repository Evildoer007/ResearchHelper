"""Batched offline delivery; keep earlier release helpers and receipts intact.

Reuse the established signing, mount, package, installation and demo gates.
Route only batch paths, inventory counts and the seven new extraction checks.
No shipping assertion, source rule or private configuration is overridden.
"""

from pathlib import Path
from tests import release_scope_submission as previous
from tests import review_scope_snapshot_release as review

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "result/acceptance/scope-snapshot-release"


def driver():
    original = Path(previous.__file__)
    source = original.read_text()
    replacements = (
        ("scope-submission", "scope-snapshot"),
        ("prompt-reader-release/final/prompt-reader-delivery.json",
         "scope-submission-release/final/scope-submission-delivery.json"),
        ("1434", "1441"), ("412", "419"),
        ("COUNT, ADDED = 1441, 190", "COUNT, ADDED = 1441, 197"),
        ("PACKAGE_ADDITIONS, 15", "PACKAGE_ADDITIONS, 22"),
        ('"helper_created_after_source_inventory": True',
         '"helper_created_after_source_inventory": False'),
    )
    for before, after in replacements:
        assert before in source, before
        source = source.replace(before, after)
    namespace = {"__file__": __file__, "__name__": "tests.scope_snapshot_delivery"}
    exec(compile(source, str(original), "exec"), namespace)
    namespace["PACKAGE_ADDITIONS"] = (*namespace["PACKAGE_ADDITIONS"],
                                       "tests.test_extraction_registry_scope_offline")
    assert namespace["BASE"] == BASE
    assert namespace["PRIOR"] == ROOT / "result/acceptance/scope-submission-release/final/scope-submission-delivery.json"
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
