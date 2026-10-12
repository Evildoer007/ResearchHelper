from __future__ import annotations

import sysconfig

from core import sdk_bootstrap


def test_ifind_import_context_redirects_purelib_and_restores(monkeypatch, tmp_path):
    bridge = tmp_path / "sdk_bridge"
    bridge.mkdir()
    (bridge / "iFinDPy.pth").write_text("D:/iFinD/bin/x64", encoding="utf-8")
    monkeypatch.setenv("RESEARCH_HELPER_IFIND_PURELIB", str(bridge))

    original = sysconfig.get_paths
    with sdk_bootstrap.ifind_sdk_import_context():
        assert sysconfig.get_paths()["purelib"] == str(bridge.resolve())

    assert sysconfig.get_paths is original


def test_ifind_import_context_is_noop_without_pth(monkeypatch, tmp_path):
    monkeypatch.setenv("RESEARCH_HELPER_IFIND_PURELIB", str(tmp_path / "missing"))
    monkeypatch.setattr(sdk_bootstrap, "_pth_candidates", lambda: [])
    original = sysconfig.get_paths

    with sdk_bootstrap.ifind_sdk_import_context():
        assert sysconfig.get_paths is original

    assert sysconfig.get_paths is original
