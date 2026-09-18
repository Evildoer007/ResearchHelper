from copy import deepcopy
import json
from hashlib import sha256
from core.optionhelper_quote_worker import build_project_request
from tools.optionhelper_install import ensure_skill_read_access, verify_contents, activate, rollback
from core.optionhelper_bridge import _quote_facts


def test_explicit_quote_dates_are_preserved_without_mutating_inputs():
    body = {
        "selection": {"product_id": "1.1", "underlyings": ["515880.SH"]},
        "pricing_config": {"valuation_date": "2026-09-15"},
        "term_overrides": {"T": 0.25},
        "backtest_config": {"start_date": "2021-01-01"},
        "quote_variants": [{"pricing_config": {"valuation_date": "2026-09-14"}}],
    }
    original = deepcopy(body)
    request = build_project_request(body)
    assert request["pricing_config"]["valuation_date"] == "2026-09-15"
    assert request["quote_variants"][0]["pricing_config"]["valuation_date"] == "2026-09-14"
    assert request["term_overrides"] == {"T": 0.25}
    assert body == original


def test_each_underlying_has_independent_request_and_no_invented_date():
    first = build_project_request({"selection": {"underlyings": ["561160.SH"]},
                                  "pricing_config": {"valuation_date": "2026-09-15"}})
    second = build_project_request({"selection": {"underlyings": ["561910.SH"]}})
    assert second["selection"]["underlyings"] == ["561910.SH"]
    assert "pricing_config" not in second
    assert first["pricing_config"]["valuation_date"] == "2026-09-15"


def test_invalid_quote_overrides_are_rejected():
    import pytest
    with pytest.raises(ValueError, match="pricing_config"):
        build_project_request({"pricing_config": "2026-09-15"})


def _skill(root):
    root.mkdir(parents=True)
    hashes = {}
    for relative in ("SKILL.md", "scripts/tool_entry.py", "scripts/environment_check.py"):
        target = root / relative
        target.parent.mkdir(exist_ok=True)
        target.write_text("# test", encoding="utf-8")
        hashes[relative] = sha256(target.read_bytes()).hexdigest()
    (root / "capability-manifest.json").write_text(json.dumps({"content_hashes": hashes}), encoding="utf-8")
    return root


def test_install_rejects_modified_package(tmp_path):
    import pytest
    skill = _skill(tmp_path / "option-helper")
    (skill / "scripts/tool_entry.py").write_text("modified", encoding="utf-8")
    with pytest.raises(ValueError, match="被修改"):
        verify_contents(skill)


def test_failed_readiness_keeps_config_unchanged(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import pytest
    monkeypatch.delenv("OPTIONHELPER_SKILL_ROOT", raising=False)
    monkeypatch.delenv("OPTIONHELPER_PYTHON", raising=False)
    skill = _skill(tmp_path / "option-helper")
    config = tmp_path / "config.local.json"
    config.write_text('{"other_setting": "keep"}', encoding="utf-8")
    original = config.read_bytes()
    monkeypatch.setattr("tools.optionhelper_install.ensure_skill_read_access",
                        lambda *a, **k: "")
    monkeypatch.setattr("tools.optionhelper_install.subprocess.run",
                        lambda *a, **k: SimpleNamespace(returncode=1))
    with pytest.raises(ValueError, match="未切换"):
        activate(skill, "test-commit", project=tmp_path)
    assert config.read_bytes() == original


def test_activate_and_rollback_preserve_other_settings(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import sys
    monkeypatch.delenv("OPTIONHELPER_SKILL_ROOT", raising=False)
    monkeypatch.delenv("OPTIONHELPER_PYTHON", raising=False)
    old = _skill(tmp_path / "old" / "option-helper")
    new = _skill(tmp_path / "new" / "option-helper")
    config_path = tmp_path / "config.local.json"
    config_path.write_text(json.dumps({"OPTIONHELPER_SKILL_ROOT": str(old),
                                      "OPTIONHELPER_PYTHON": sys.executable,
                                      "other_setting": "old"}), encoding="utf-8")
    monkeypatch.setattr("tools.optionhelper_install.ensure_skill_read_access",
                        lambda *a, **k: "Fei\\87055")
    monkeypatch.setattr("tools.optionhelper_install.subprocess.run",
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout='{"ok": true}'))
    backup = activate(new, "test-commit", project=tmp_path)
    assert (backup / "previous-skill" / "SKILL.md").is_file()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["OPTIONHELPER_SKILL_ROOT"] == str(new.resolve())
    config["other_setting"] = "updated-later"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    rollback(backup, project=tmp_path)
    restored = json.loads(config_path.read_text(encoding="utf-8"))
    assert restored["other_setting"] == "updated-later"
    assert restored["OPTIONHELPER_PYTHON"] == sys.executable
    assert restored["OPTIONHELPER_SKILL_ROOT"] == str(backup / "previous-skill")


def test_windows_activation_repairs_inheritance_and_grants_project_owner(tmp_path, monkeypatch):
    from types import SimpleNamespace

    skill = _skill(tmp_path / "candidate" / "option-helper")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[0] == "powershell":
            return SimpleNamespace(returncode=0, stdout="Fei\\87055\n")
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr("tools.optionhelper_install.subprocess.run", fake_run)
    principal = ensure_skill_read_access(skill, tmp_path, platform_name="win32")
    assert principal == "Fei\\87055"
    assert calls[1][:3] == ["icacls", str(skill.resolve()), "/inheritance:e"]
    assert calls[2][:3] == ["icacls", str(skill.resolve()), "/grant"]
    assert "Fei\\87055:(OI)(CI)RX" in calls[2]


def test_quote_facts_are_read_only_from_each_returned_report_directory(tmp_path):
    for code, day, price in (("561160.SH", "2026-09-15", "1.25"),
                             ("561910.SH", "2026-09-14", "2.50")):
        report_dir = tmp_path / "result" / code
        report_dir.mkdir(parents=True)
        (report_dir / "quote.html").write_text("preview", encoding="utf-8")
        (report_dir / "designer-input.json").write_text(json.dumps({"reference_quote": {
            "quote_date": day, "note": code, "groups": [{"title": code,
                "columns": [{"key": "price", "label": "价格"}], "rows": [{"price": price}]}],
        }}), encoding="utf-8")
    for code, day in (("561160.SH", "2026-09-15"), ("561910.SH", "2026-09-14")):
        groups, date, note, path = _quote_facts(str(tmp_path / "result" / code / "quote.html"), tmp_path)
        assert groups[0].title == code
        assert date == day and note == code
        assert path == str(tmp_path / "result" / code / "designer-input.json")


def test_missing_quote_facts_do_not_fall_back_to_another_run(tmp_path):
    existing = tmp_path / "result" / "old"
    existing.mkdir(parents=True)
    (existing / "designer-input.json").write_text('{"reference_quote": {}}', encoding="utf-8")
    groups, day, note, error = _quote_facts(str(tmp_path / "result" / "new" / "quote.html"), tmp_path)
    assert not groups and not day
    assert "new" in error
