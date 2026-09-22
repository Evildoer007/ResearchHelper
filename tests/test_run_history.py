import json
from pathlib import Path

import pytest

from core.run_history import load_history_tasks, primary_run_files, save_history_title


def _write_run(directory: Path, name: str, request: str, status: str, report: str = "") -> Path:
    path = directory / name
    path.write_text(json.dumps({
        "run_id": path.stem, "request": request, "status": status,
        "artifacts": {"研究报告": report} if report else {},
    }, ensure_ascii=False), encoding="utf-8")
    return path


def test_history_groups_exact_repeat_and_prefers_latest_available_report(tmp_path):
    report = tmp_path / "onepager.html"
    report.write_text("<h1>报告</h1>", encoding="utf-8")
    completed = _write_run(
        tmp_path, "run-20260918-100000-a1.json", "机器人行业投资机会", "completed", str(report),
    )
    failed = _write_run(
        tmp_path, "run-20260918-110000-a2.json", "机器人行业投资机会", "failed",
    )
    _write_run(tmp_path, "run-20260918-120000-a3.json", "消费电子前景", "completed")
    (tmp_path / "run-20260918-110000-a2.optionhelper-recommender.json").write_text(
        '{"request":"不应显示"}', encoding="utf-8",
    )
    failed.touch()

    assert len(primary_run_files(tmp_path)) == 3
    tasks = load_history_tasks(tmp_path)
    assert len(tasks) == 2
    robot = next(task for task in tasks if task.title == "机器人行业投资机会")
    assert robot.attempts == 2
    assert robot.latest_run_path == failed
    assert robot.display_run_path == completed
    assert robot.latest_status == "failed"
    assert robot.has_report


def test_history_keeps_failed_task_without_report(tmp_path):
    failed = _write_run(tmp_path, "run-20260918-110000-a2.json", "未来一月机器人", "failed")
    task, = load_history_tasks(tmp_path)
    assert task.display_run_path == failed
    assert not task.has_report


def test_rename_changes_only_sidebar_label_and_survives_restart(tmp_path):
    run = _write_run(tmp_path, "run-20260918-110000-a2.json", "机器人行业投资机会", "completed")
    aliases = tmp_path / "history_titles.json"
    task, = load_history_tasks(tmp_path, aliases)
    original_run = run.read_bytes()
    save_history_title(aliases, task.task_key, "机器人研究", task.original_title)
    renamed, = load_history_tasks(tmp_path, aliases)
    assert renamed.title == "机器人研究"
    assert renamed.original_title == "机器人行业投资机会"
    assert run.read_bytes() == original_run
    save_history_title(aliases, task.task_key, task.original_title, task.original_title)
    restored, = load_history_tasks(tmp_path, aliases)
    assert restored.title == task.original_title


def test_rename_refuses_to_overwrite_corrupt_title_store(tmp_path):
    aliases = tmp_path / "history_titles.json"
    aliases.write_text("not JSON", encoding="utf-8")
    with pytest.raises(ValueError, match="未被覆盖"):
        save_history_title(aliases, "a" * 64, "新标题", "旧标题")
    assert aliases.read_text(encoding="utf-8") == "not JSON"
