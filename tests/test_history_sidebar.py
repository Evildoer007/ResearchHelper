"""Historical tasks are searchable and inspected without changing active work."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from core.run_history import HistoryTask
from gui.history_sidebar import HistorySidebar
from gui.history_view import HistoryView


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _task(path: Path, title: str) -> HistoryTask:
    return HistoryTask(title, 1.0, "completed", 2, path, path, True, "a" * 64, title)


def test_sidebar_search_and_selection(app, tmp_path):
    sidebar = HistorySidebar()
    robot = tmp_path / "run-robot.json"
    sidebar.set_tasks([_task(robot, "机器人行业投资机会"), _task(tmp_path / "run-ai.json", "AI 芯片影响")])
    sidebar.search.setText("机器人")
    selected = []
    sidebar.taskSelected.connect(selected.append)
    item = next(item for index in range(sidebar.list.count())
                if (item := sidebar.list.item(index)).data(Qt.ItemDataRole.UserRole))
    sidebar._open_task(item)
    assert selected == [str(robot)]
    assert sidebar.count.text() == "1 项研究"


def test_sidebar_rename_is_explicit_and_searches_original_title(app, tmp_path, monkeypatch):
    from gui import history_sidebar as sidebar_module

    sidebar = HistorySidebar()
    task = _task(tmp_path / "run-robot.json", "机器人研究")
    task = HistoryTask(task.title, task.updated_at, task.latest_status, task.attempts,
                       task.latest_run_path, task.display_run_path, task.has_report,
                       task.task_key, "未来一个月机器人行业的投资机会")
    sidebar.set_tasks([task])
    sidebar.search.setText("未来一个月")
    item = sidebar.list.item(sidebar.list.count() - 1)
    sidebar.list.setCurrentItem(item)
    renamed = []
    sidebar.renameRequested.connect(lambda key, title: renamed.append((key, title)))
    monkeypatch.setattr(sidebar_module.QInputDialog, "getText", lambda *_args, **_kwargs: ("机器人专题", True))
    sidebar._request_rename()
    assert renamed == [("a" * 64, "机器人专题")]


def test_history_view_reads_saved_report_and_draft(app, tmp_path):
    report = tmp_path / "report.html"
    report.write_text("<html><body>历史研究报告</body></html>", encoding="utf-8")
    draft = tmp_path / "draft.md"
    draft.write_text("内部依据与审核记录", encoding="utf-8")
    run = tmp_path / "run-20260918-100000-a1.json"
    view = HistoryView(None)
    view.load_task(_task(run, "机器人行业投资机会"), {
        "request": "机器人行业投资机会", "status": "completed",
        "artifacts": {"研究报告": str(report), "内部底稿": str(draft)},
    })
    assert "历史研究报告" in view.report.toPlainText()
    assert "内部依据与审核记录" in view.notes.toPlainText()
    assert view.files.count() == 2
    assert view.edit.isEnabled()
    requested = []
    view.editRequested.connect(requested.append)
    view.edit.click()
    assert requested == [str(run)]


def test_history_view_prefers_manual_revision(app, tmp_path):
    generated = tmp_path / "report.html"
    generated.write_text("<html><body>自动生成稿</body></html>", encoding="utf-8")
    edited = tmp_path / "report_人工修订.html"
    edited.write_text("<html><body>人工修订稿</body></html>", encoding="utf-8")
    run = tmp_path / "run-20260918-100000-a1.json"
    view = HistoryView(None)
    view.load_task(_task(run, "机器人行业投资机会"), {
        "request": "机器人行业投资机会", "status": "completed",
        "artifacts": {"研究报告": str(generated), "人工修订版 HTML": str(edited)},
    })
    assert "人工修订稿" in view.report.toPlainText()


def test_opening_history_does_not_replace_active_research(app, tmp_path, monkeypatch):
    import json
    import gui.app as gui

    report = tmp_path / "report.html"
    report.write_text("<html><body>保存的旧报告</body></html>", encoding="utf-8")
    run = tmp_path / "run-20260918-100000-a1.json"
    run.write_text(json.dumps({
        "run_id": run.stem, "request": "机器人行业投资机会", "status": "completed",
        "artifacts": {"研究报告": str(report)},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(gui, "RUNS", tmp_path)
    monkeypatch.setattr(gui, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(gui, "QWebEngineView", None)
    window = gui.ResearchHelperWindow()
    window.prompt.setPlainText("尚未提交的新客户需求")
    before = (window.prompt.toPlainText(), dict(window.last_summary),
              window.report_preview.toHtml(), list(window.quote_jobs))
    window._open_history_task(str(run))
    assert window.page_stack.currentWidget() is window.history_view
    assert (window.prompt.toPlainText(), window.last_summary,
            window.report_preview.toHtml(), window.quote_jobs) == before
    window.close()


def test_editing_history_report_keeps_active_task_unchanged(app, tmp_path, monkeypatch):
    import json
    import gui.app as gui

    generated = tmp_path / "report.html"
    generated.write_text("<html><body>自动稿</body></html>", encoding="utf-8")
    edited = tmp_path / "report_人工修订.html"
    edited.write_text("<html><body>人工修订稿</body></html>", encoding="utf-8")
    run = tmp_path / "run-20260918-100000-a1.json"
    run.write_text(json.dumps({
        "run_id": run.stem, "request": "机器人行业投资机会", "status": "completed",
        "artifacts": {"研究报告": str(generated)},
    }, ensure_ascii=False), encoding="utf-8")

    class FakeEditor:
        def __init__(self, _parent, *, base_html):
            assert base_html == generated
            self.saved_result = {
                "html": str(edited), "sidecar": str(tmp_path / "edit.json"),
                "revision": 1, "edited_at": "2026-09-22 10:00", "pdf": "", "pdf_pages": None,
            }

        def exec(self):
            return 1

    monkeypatch.setattr(gui, "RUNS", tmp_path)
    monkeypatch.setattr(gui, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(gui, "QWebEngineView", None)
    monkeypatch.setattr(gui, "ReportEditorDialog", FakeEditor)
    window = gui.ResearchHelperWindow()
    window.prompt.setPlainText("未提交的新需求")
    window._open_history_task(str(run))
    window._edit_history_report(str(run))
    saved = json.loads(run.read_text(encoding="utf-8"))
    assert saved["artifacts"]["人工修订版 HTML"] == str(edited)
    assert saved["metadata"]["一页通交付校验"].startswith("未校验")
    assert window.last_summary == {}
    assert window.prompt.toPlainText() == "未提交的新需求"
    window.close()


def test_work_pages_use_full_width_without_splitter(app, tmp_path, monkeypatch):
    import gui.app as gui

    monkeypatch.setattr(gui, "RUNS", tmp_path)
    monkeypatch.setattr(gui, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(gui, "QWebEngineView", None)
    window = gui.ResearchHelperWindow()
    assert window.page_stack.currentWidget() is window.pages["research"]
    window._navigate("report")
    assert window.page_stack.currentWidget() is window.pages["report"]
    window._navigate("review")
    assert window.page_stack.currentWidget() is window.pages["review"]
    assert not hasattr(window, "workspace")
    window.close()


def test_app_rename_persists_alias_without_changing_run(app, tmp_path, monkeypatch):
    import json
    import gui.app as gui

    run = tmp_path / "run-20260918-100000-a1.json"
    run.write_text(json.dumps({
        "run_id": run.stem, "request": "机器人行业投资机会", "status": "completed", "artifacts": {},
    }, ensure_ascii=False), encoding="utf-8")
    original = run.read_bytes()
    monkeypatch.setattr(gui, "RUNS", tmp_path)
    monkeypatch.setattr(gui, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(gui, "QWebEngineView", None)
    window = gui.ResearchHelperWindow()
    task, = window.history_tasks
    window._rename_history_task(task.task_key, "机器人专题")
    assert window.history_tasks[0].title == "机器人专题"
    assert run.read_bytes() == original
    assert (tmp_path / "history_titles.json").is_file()
    window.close()
