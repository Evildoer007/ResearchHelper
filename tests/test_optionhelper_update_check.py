from __future__ import annotations

import json
from pathlib import Path

from tools.optionhelper_check_update import (
    Audit,
    Check,
    _extract_json,
    _finalize,
    _markdown,
    _active_state,
    run_audit,
)


def test_extract_json_tolerates_progress_before_and_after_payload():
    text = ('progress\n{\n  "ok": true, '
            '"nested": {"ok": false}, "items": [1]\n}\nthird party noise\n')
    assert _extract_json(text) == {"ok": True, "nested": {"ok": False}, "items": [1]}


def test_active_state_matches_receipt_to_configured_skill(tmp_path: Path):
    selected = tmp_path / "skills" / "new" / "option-helper"
    (tmp_path / "config.local.json").write_text(json.dumps({
        "OPTIONHELPER_SKILL_ROOT": str(selected),
        "OPTIONHELPER_PYTHON": "C:/candidate/python.exe",
        "DEEPSEEK_API_KEY": "must-not-leak",
    }), encoding="utf-8")
    old = tmp_path / ".optionhelper" / "migrations" / "old"
    new = tmp_path / ".optionhelper" / "migrations" / "new"
    old.mkdir(parents=True); new.mkdir(parents=True)
    (old / "receipt.json").write_text(json.dumps({
        "skill_root": "C:/old", "source_commit": "old-commit",
    }), encoding="utf-8")
    (new / "receipt.json").write_text(json.dumps({
        "skill_root": str(selected), "source_commit": "new-commit",
    }), encoding="utf-8")
    config, receipt = _active_state(tmp_path)
    assert config["OPTIONHELPER_SKILL_ROOT"] == str(selected)
    assert receipt["source_commit"] == "new-commit"


def test_report_never_claims_technical_pass_when_a_check_fails(tmp_path: Path):
    audit = Audit(generated_at="2026-09-17T10:00:00+01:00",
                  project_root=str(tmp_path), source_root="D:/OptionHelper")
    audit.checks = [
        Check("integrity", "Skill 完整性", "pass", "ok"),
        Check("smoke", "兼容 smoke", "fail", "protocol mismatch"),
    ]
    _finalize(audit)
    assert audit.technical_status == "failed"
    assert "不得激活" in audit.activation_recommendation
    report = _markdown(audit)
    assert "protocol mismatch" in report
    assert "未执行 git pull" in report


def test_skipped_regression_marks_report_incomplete(tmp_path: Path):
    audit = Audit(generated_at="2026-09-17T10:00:00+01:00",
                  project_root=str(tmp_path), source_root="D:/OptionHelper")
    audit.checks = [Check("tests", "全量回归", "skip", "requested")]
    _finalize(audit)
    assert audit.technical_status == "incomplete"


def test_relative_report_directory_is_resolved_under_project(tmp_path: Path):
    source = tmp_path / "source"
    (source / ".git").mkdir(parents=True)
    # A deliberately invalid Git directory is enough to exercise report-path
    # resolution without building or executing an external candidate.
    audit, markdown, payload = run_audit(
        project=tmp_path,
        source=source,
        candidate_skill=None,
        candidate_python=None,
        project_python=Path("missing-python.exe"),
        output_dir=Path("output") / "relative-audit",
        skip_project_tests=True,
    )
    assert audit.technical_status in {"failed", "incomplete"}
    assert markdown == tmp_path / "output" / "relative-audit" / "optionhelper-update-check.md"
    assert payload.is_file()
