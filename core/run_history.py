"""Read-only projection of run summaries into sidebar research tasks.

Run JSON files are immutable execution records, not a persistent task database.
Exact repeats of the same client question are grouped for navigation; their
individual attempts remain available in the existing history selector.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path


PRIMARY_RUN_FILE = re.compile(r"^run-\d{8}-\d{6}-[0-9a-f]+\.json$", re.I)


@dataclass(frozen=True)
class HistoryTask:
    title: str
    updated_at: float
    latest_status: str
    attempts: int
    latest_run_path: Path
    display_run_path: Path
    has_report: bool
    task_key: str = ""
    original_title: str = ""


def primary_run_files(directory: Path) -> list[Path]:
    """Exclude OptionHelper handoffs, profile files, and other sidecars."""
    if not directory.is_dir():
        return []
    paths = (path for path in directory.glob("run-*.json") if PRIMARY_RUN_FILE.fullmatch(path.name))
    return sorted(paths, key=lambda path: path.stat().st_mtime, reverse=True)


def _report_path(summary: dict) -> Path | None:
    artifacts = summary.get("artifacts") or {}
    if not isinstance(artifacts, dict):
        return None
    raw = str(artifacts.get("人工修订版 HTML") or artifacts.get("研究报告") or next(
        (value for name, value in artifacts.items() if "研究报告" in str(name)), "",
    )).strip()
    return Path(raw) if raw else None


def load_history_titles(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    raw = payload.get("titles") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        return {}
    return {key: value for key, value in raw.items()
            if isinstance(key, str) and re.fullmatch(r"[0-9a-f]{64}", key)
            and isinstance(value, str) and value.strip()}


def save_history_title(path: Path, task_key: str, title: str, original_title: str) -> None:
    """Persist a sidebar label atomically; never edit a run summary or report."""
    if not re.fullmatch(r"[0-9a-f]{64}", task_key):
        raise ValueError("历史任务标识无效")
    cleaned = " ".join(title.split())
    if not cleaned or len(cleaned) > 80:
        raise ValueError("名称应为 1–80 个字符")
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("titles"), dict):
                raise ValueError("历史任务名称文件格式无效")
        except (OSError, ValueError) as error:
            raise ValueError("历史任务名称文件无法读取；原文件未被覆盖") from error
        titles = dict(payload["titles"])
    else:
        titles = {}
    if cleaned == original_title:
        titles.pop(task_key, None)
    else:
        titles[task_key] = cleaned
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix="history-titles-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump({"version": 1, "titles": titles}, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_history_tasks(directory: Path, titles_path: Path | None = None) -> list[HistoryTask]:
    aliases = load_history_titles(titles_path) if titles_path else {}
    groups: dict[str, list[tuple[Path, dict, float, bool]]] = {}
    for path in primary_run_files(directory):
        try:
            summary = json.loads(path.read_text(encoding="utf-8"))
            modified = path.stat().st_mtime
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(summary, dict):
            continue
        request = " ".join(str(summary.get("request") or "").split())
        key = request.casefold() if request else path.stem
        report = _report_path(summary)
        groups.setdefault(key, []).append((path, summary, modified, bool(report and report.is_file())))

    tasks: list[HistoryTask] = []
    for records in groups.values():
        latest = records[0]
        display = next((record for record in records if record[3]), latest)
        original_title = " ".join(str(latest[1].get("request") or "").split()) or "未命名研究"
        key = hashlib.sha256((" ".join(str(latest[1].get("request") or "").split()).casefold()
                              or latest[0].stem).encode("utf-8")).hexdigest()
        tasks.append(HistoryTask(
            title=aliases.get(key) or original_title,
            updated_at=latest[2],
            latest_status=str(latest[1].get("status") or "未知"),
            attempts=len(records),
            latest_run_path=latest[0],
            display_run_path=display[0],
            has_report=display[3],
            task_key=key,
            original_title=original_title,
        ))
    return sorted(tasks, key=lambda item: item.updated_at, reverse=True)
