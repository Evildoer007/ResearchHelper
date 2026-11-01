"""Route the retained release gates to a fresh API-guidance batch.

Only output namespaces, prior inventory and additive test counts change. Keep
the reviewed installation, rollback and package checks byte-for-byte intact.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def driver():
    path = ROOT / "packaging/tests/release_extraction_contract.py"
    source = path.read_text()
    routing = {
        "from tests import release_extraction_diagnostic as previous":
            "from tests import release_extraction_contract as previous",
        "from tests import review_extraction_contract_release as review":
            "from tests import review_api_guidance_release as review",
        "COUNT, ADDED = 1331, 87": "COUNT, ADDED = 1362, 118",
        "20261008-extraction-contract": "20261008-api-guidance",
        "extraction-contract-package.json": "api-guidance-package.json",
        '"batch": "extraction-contract-release"': '"batch": "api-guidance-release"',
        "extraction-contract-delivery.json": "api-guidance-delivery.json",
        "== 314": "== 345",
    }
    for original, replacement in routing.items():
        assert original in source, "Retained release routing changed: " + original
        source = source.replace(original, replacement)
    namespace = {"__file__": __file__, "__name__": "tests.api_guidance_release_routing"}
    exec(compile(source, str(path), "exec"), namespace)
    assert namespace["BASE"] == ROOT / "result/acceptance/api-guidance-release"
    assert namespace["PRIOR"] == ROOT / "result/acceptance/extraction-contract-release/final/extraction-contract-delivery.json"
    return namespace


def production_boundary(*, finalize=False):
    return driver()["production_boundary"](finalize=finalize)


def build():
    return driver()["build"]()


def recover_seal():
    """Resume signing of the built runtime after a verified DMG mount fault.

    Do not rebuild Python, overwrite recovery copies, or hide the first failed
    build. The caller must have detached only this build's unmounted image.
    """
    namespace = driver()
    namespace["regression"]()
    release = namespace["driver"]()
    prior = release["read"](release["OUT"] / "build.json")
    assert prior["status"] == "build_failed" and Path(prior["backup"]).is_dir()
    path = release["OUT"] / "build-recovery.json"
    assert not path.exists(), "Do not repeat a sealing attempt"
    packaging = release["dispatch"].load_script("packaging/assemble_release.py")
    state = {"status": "resealing_built_runtime", "first_build_preserved": True,
             "first_build_sha256": release["digest"](release["OUT"] / "build.json"),
             "python_rebuilt": False, "app_installed": False, "demo_synced": False}
    release["save"](path, state)
    try:
        image = packaging.seal(packaging.OUTPUT / "ResearchHelper.app")
        artifacts = {"skill": str(packaging.OUTPUT / "ResearchHelper-Skill-0.10.0.zip"),
                     "demo": str(packaging.OUTPUT / "demo"), "dmg": str(image)}
        packaging.release_record(artifacts)
        state.update(status="built_candidate_not_installed", artifacts=artifacts,
                     source_hashes=release["read"](packaging.OUTPUT / "release-manifest.json")["source_hashes"])
    except BaseException:
        state["status"] = "sealing_recovery_failed"
        raise
    finally:
        release["save"](path, state)
    return {key: state[key] for key in ("status", "python_rebuilt", "app_installed", "demo_synced")}


def package_checks():
    return driver()["package_checks"]()


def publish():
    return driver()["publish"]()
