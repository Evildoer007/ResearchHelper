import json
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

from PySide6.QtCore import QProcess

from core.quote_diagnostics import last_worker_stage, safe_error_tail
from gui import app


def test_error_tail_redacts_credentials_and_keeps_last_stage(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "top-secret-123")
    stderr = (
        "[OptionHelper/data/running] reading\n"
        "Authorization: Bearer abc123\n"
        "api_key=visible-secret\n"
        "top-secret-123\n"
        "[OptionHelper/pricing/failed] pricing failed\n"
    )
    safe = safe_error_tail(stderr)
    assert "top-secret-123" not in safe
    assert "visible-secret" not in safe
    assert "abc123" not in safe
    assert last_worker_stage(stderr) == "[OptionHelper/pricing/failed]"


def test_process_crash_callback_preserves_existing_gui_timeout():
    job = app.QuoteJob("quote-abc", "request", "562500.SH", "prompt", {}, {}, False)
    job.forced_stop_reason = "正式报价超过 195 秒"
    job.failure_reason = "gui_timeout"
    process = SimpleNamespace(
        state=lambda: QProcess.ProcessState.Running,
        kill=lambda: None,
    )
    window = SimpleNamespace(active_quote_job=job, option_process=process)
    app.ResearchHelperWindow._quote_process_error(
        window, job, QProcess.ProcessError.Crashed,
    )
    assert job.forced_stop_reason == "正式报价超过 195 秒"
    assert job.failure_reason == "gui_timeout"
    assert "Crashed" in job.process_error


def test_per_quote_diagnostic_keeps_structure_elapsed_error_without_selection(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "RUNS", tmp_path)
    job = app.QuoteJob(
        "quote-abc", "request", "562500.SH", "private market prompt",
        {"selection": {"product_id": "8.19", "reason": "private selection rationale"}},
        {}, False, source_run_id="run-123", selected_product_name="凤凰式结构",
        status="failed", message="OptionHelper 超时", started_at="2026-09-22T10:00:00+01:00",
        worker_duration_seconds=180.02, failure_reason="timeout",
    )
    window = SimpleNamespace(
        run_id="run-123", last_summary={"artifacts": {}},
        _option_error_output="[OptionHelper/pricing/running] 定价中\ntraceback",
        _persist_quote_delivery=lambda: None,
    )
    app.ResearchHelperWindow._persist_quote_diagnostic(window, job)
    path = tmp_path / "run-123.quote-abc.optionhelper-quote.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["structure"] == {"product_id": "8.19", "product_name": "凤凰式结构"}
    assert saved["worker_duration_seconds"] == 180.02
    assert saved["failure_reason"] == "timeout"
    assert saved["last_worker_stage"] == "[OptionHelper/pricing/running]"
    assert saved["pricing"]["path_count"] == 100_000
    assert "private market prompt" not in path.read_text(encoding="utf-8")
    assert "private selection rationale" not in path.read_text(encoding="utf-8")


def test_watchdog_returns_structured_worker_error_without_formal_quote(tmp_path):
    watchdog = Path(app.ROOT) / "core" / "optionhelper_quote_watchdog.py"
    completed = subprocess.run(
        [sys.executable, str(watchdog)],
        input=json.dumps({"skill_root": str(tmp_path / "absent"),
                          "project_root": str(tmp_path)}),
        text=True, encoding="utf-8", errors="replace",
        capture_output=True, timeout=15, check=False,
    )
    payload = json.loads(completed.stdout)
    assert payload["ok"] is False
    assert "Skill 根目录无效" in payload["message"]
