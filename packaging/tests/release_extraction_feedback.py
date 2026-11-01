"""Reuse reviewed packaging gates; isolate this additive release's receipts."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def driver():
    path = ROOT / "packaging/tests/release_extraction_contract.py"
    source = path.read_text()
    routing = {
        "from tests import release_extraction_diagnostic as previous":
            "from tests import release_extraction_contract as previous",
        "from tests import review_extraction_contract_release as review":
            "from tests import review_extraction_feedback_release as review",
        "COUNT, ADDED = 1331, 87": "COUNT, ADDED = 1371, 127",
        '*review.ADDITIONS': '*review.PACKAGE_ADDITIONS',
        "20261008-extraction-contract": "20261008-extraction-feedback",
        "extraction-contract-package.json": "extraction-feedback-package.json",
        '"batch": "extraction-contract-release"': '"batch": "extraction-feedback-release"',
        "extraction-contract-delivery.json": "extraction-feedback-delivery.json",
        "== 314": "== 354",
    }
    for original, replacement in routing.items():
        assert original in source, "Release routing changed: " + original
        source = source.replace(original, replacement)
    namespace = {"__file__": __file__, "__name__": "tests.extraction_feedback_release"}
    exec(compile(source, str(path), "exec"), namespace)
    assert namespace["BASE"] == ROOT / "result/acceptance/extraction-feedback-release"
    assert namespace["PRIOR"] == ROOT / "result/acceptance/api-guidance-release/final/api-guidance-delivery.json"
    return namespace


def production_boundary(*, finalize=False):
    return driver()["production_boundary"](finalize=finalize)


def build():
    return driver()["build"]()


def package_checks():
    return driver()["package_checks"]()


def publish():
    return driver()["publish"]()
