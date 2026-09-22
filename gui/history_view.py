"""Read-only historical report viewer; never mutates the active run."""

from __future__ import annotations

from html import escape
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QListWidget, QPlainTextEdit, QPushButton,
    QTabWidget, QTextBrowser, QVBoxLayout,
)

from core.run_history import HistoryTask

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
except ImportError:  # pragma: no cover - depends on installed Qt components
    QWebEngineView = None


HISTORY_VIEW_STYLE = """
QFrame#historyView { background: #f5f2ef; border: none; }
QFrame#historyPaper { background: #fffdfb; border: 1px solid #ded8d6; border-radius: 13px; }
QLabel#historyTitle { color: #292427; font-size: 19px; font-weight: 700; }
QLabel#historyMeta { color: #6e6569; font-size: 12px; }
QPushButton#historyAction {
    color: #292427; background: #fffdfb; border: 1px solid #ded8d6;
    border-radius: 8px; padding: 5px 10px; min-height: 25px; max-height: 32px;
}
QPushButton#historyAction:hover { background: #f5f2ef; }
"""


class HistoryView(QFrame):
    backRequested = Signal()
    reuseRequested = Signal(str)
    editRequested = Signal(str)

    def __init__(self, webengine_cls=QWebEngineView) -> None:
        super().__init__()
        self._webengine_cls = webengine_cls
        self.setObjectName("historyView")
        self.setStyleSheet(HISTORY_VIEW_STYLE)
        self._request = ""
        self._run_path = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(13)
        toolbar = QHBoxLayout()
        back = QPushButton("← 返回工作台")
        back.setObjectName("historyAction")
        back.clicked.connect(self.backRequested.emit)
        toolbar.addWidget(back)
        toolbar.addStretch(1)
        reuse = QPushButton("将需求带入新研究")
        reuse.setObjectName("historyAction")
        reuse.clicked.connect(lambda: self.reuseRequested.emit(self._request))
        toolbar.addWidget(reuse)
        self.edit = QPushButton("编辑此报告")
        self.edit.setObjectName("historyAction")
        self.edit.setEnabled(False)
        self.edit.clicked.connect(lambda: self.editRequested.emit(self._run_path))
        toolbar.addWidget(self.edit)
        layout.addLayout(toolbar)

        header = QFrame()
        header.setObjectName("historyPaper")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(20, 17, 20, 16)
        self.title = QLabel("选择左侧历史任务")
        self.title.setObjectName("historyTitle")
        self.title.setWordWrap(True)
        header_layout.addWidget(self.title)
        self.meta = QLabel("历史报告只读，不会覆盖当前研究或报价队列。")
        self.meta.setObjectName("historyMeta")
        self.meta.setWordWrap(True)
        header_layout.addWidget(self.meta)
        layout.addWidget(header)

        self.tabs = QTabWidget()
        self.report = webengine_cls() if webengine_cls is not None else QTextBrowser()
        self.notes = QPlainTextEdit()
        self.notes.setReadOnly(True)
        self.files = QListWidget()
        self.files.itemDoubleClicked.connect(self._open_artifact)
        self.tabs.addTab(self.report, "历史报告")
        self.tabs.addTab(self.notes, "内部底稿")
        self.tabs.addTab(self.files, "交付文件")
        layout.addWidget(self.tabs, 1)
        self._show_placeholder("请选择左侧的历史任务。")

    def load_task(self, task: HistoryTask, summary: dict) -> None:
        self._request = str(summary.get("request") or "")
        self._run_path = str(task.display_run_path)
        self.title.setText(task.title)
        selected_is_latest = task.display_run_path == task.latest_run_path
        selected_status = str(summary.get("status") or "未知")
        self.meta.setText(
            f"已保存 {task.attempts} 次运行；当前展示：{selected_status}"
            + ("。最近一次运行未形成报告，因此展示之前可用的报告。" if not selected_is_latest else "。")
            + " 历史内容只读，不影响当前研究和报价。"
        )
        artifacts = summary.get("artifacts") or {}
        if not isinstance(artifacts, dict):
            artifacts = {}
        report_raw = str(artifacts.get("人工修订版 HTML") or artifacts.get("研究报告") or next(
            (value for name, value in artifacts.items() if "研究报告" in str(name)), "",
        )).strip()
        report_file = Path(report_raw) if report_raw else None
        self.edit.setEnabled(bool(report_file and report_file.is_file()))
        if report_file and report_file.is_file():
            if self._webengine_cls is not None and isinstance(self.report, self._webengine_cls):
                self.report.load(QUrl.fromLocalFile(str(report_file.resolve())))
            else:
                try:
                    self.report.setHtml(report_file.read_text(encoding="utf-8"))
                except OSError:
                    self._show_placeholder("历史报告文件无法读取。")
        else:
            reason = str(summary.get("error") or "该次运行没有生成可用报告。")
            self._show_placeholder(reason)
        draft_raw = str(artifacts.get("内部底稿") or "").strip()
        draft_file = Path(draft_raw) if draft_raw else None
        try:
            notes = draft_file.read_text(encoding="utf-8") if draft_file and draft_file.is_file() else "该次运行没有可用的内部底稿。"
        except OSError:
            notes = "内部底稿文件无法读取。"
        self.notes.setPlainText(notes)
        self.files.clear()
        for name, raw in artifacts.items():
            path = Path(str(raw))
            if path.is_file():
                self.files.addItem(f"{name}  ·  {path.name}")
                self.files.item(self.files.count() - 1).setData(Qt.ItemDataRole.UserRole, str(path))
        self.tabs.setCurrentIndex(0)

    def _show_placeholder(self, message: str) -> None:
        self.report.setHtml(
            "<html><body style='font-family:Segoe UI,Microsoft YaHei,sans-serif;"
            "background:#fffdfb;color:#6e6569;padding:36px'>"
            "<h2 style='color:#292427'>暂无历史报告</h2><p>" + escape(message) + "</p></body></html>"
        )

    def _open_artifact(self, item) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if path and Path(path).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve())))
