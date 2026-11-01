"""Deliver the compact API protocol using the retained offline release gates."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def driver():
    path = ROOT / "packaging/tests/release_extraction_contract.py"
    source = path.read_text()
    routing = {
        "from tests import release_extraction_diagnostic as previous":
            "from tests import release_extraction_contract as previous",
        "from tests import review_extraction_contract_release as review":
            "from tests import review_api_compact_release as review",
        "COUNT, ADDED = 1331, 87": "COUNT, ADDED = 1400, 156",
        "*review.ADDITIONS": "*review.PACKAGE_ADDITIONS",
        "20261008-extraction-contract": "20261008-api-compact",
        "extraction-contract-package.json": "api-compact-package.json",
        '"batch": "extraction-contract-release"': '"batch": "api-compact-release"',
        "extraction-contract-delivery.json": "api-compact-delivery.json",
        "== 314": "== 383",
    }
    for original, replacement in routing.items():
        assert original in source, "Release routing changed: " + original
        source = source.replace(original, replacement)
    namespace = {"__file__": __file__, "__name__": "tests.api_compact_release"}
    exec(compile(source, str(path), "exec"), namespace)
    assert namespace["BASE"] == ROOT / "result/acceptance/api-compact-release"
    assert namespace["PRIOR"] == ROOT / "result/acceptance/api-basis-release/final/api-basis-delivery.json"
    return namespace


def production_boundary(*, finalize=False):
    return driver()["production_boundary"](finalize=finalize)


def build():
    return driver()["build"]()


def package_checks():
    return driver()["package_checks"]()


def publish():
    return driver()["publish"]()
