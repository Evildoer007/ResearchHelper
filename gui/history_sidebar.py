"""Searchable, non-destructive history navigation beside the desktop workbench."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMenu, QMessageBox, QPushButton, QVBoxLayout,
)

from core.run_history import HistoryTask


HISTORY_STYLE = """
QFrame#historySidebar { background: #24282b; border: none; border-right: 1px solid #393e42; }
QLabel#historyHeading { color: #f4f1ef; font-size: 15px; font-weight: 700; }
QLabel#historyCount { color: #b3acae; font-size: 11px; }
QLineEdit#historySearch {
    color: #f4f1ef; background: #303438; border: 1px solid #484d51;
    border-radius: 8px; padding: 7px 9px; min-height: 23px;
}
QLineEdit#historySearch:focus { border-color: #b3acae; }
QPushButton#historyNew {
    color: #f4f1ef; background: #393e42; border: 1px solid #51565a;
    border-radius: 8px; padding: 5px 10px; min-height: 26px; max-height: 32px;
}
QPushButton#historyNew:hover { background: #484d51; }
QPushButton#historyRename {
    color: #d8d3d1; background: transparent; border: none;
    padding: 2px 4px; min-height: 22px; max-height: 28px;
}
QPushButton#historyRename:hover { color: #ffffff; background: #393e42; }
QListWidget#historyTasks { background: transparent; color: #e8e4e2; border: none; padding: 0; }
QListWidget#historyTasks::item { border-radius: 7px; padding: 7px 6px; }
QListWidget#historyTasks::item:hover { background: #353a3e; }
QListWidget#historyTasks::item:selected { background: #484d51; color: #ffffff; }
"""


class HistorySidebar(QFrame):
    taskSelected = Signal(str)
    newResearchRequested = Signal()
    renameRequested = Signal(str, str)

    TASK_KEY_ROLE = int(Qt.ItemDataRole.UserRole) + 1
    TITLE_ROLE = TASK_KEY_ROLE + 1

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("historySidebar")
        self.setFixedWidth(252)
        self.setStyleSheet(HISTORY_STYLE)
        self._tasks: list[HistoryTask] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 20, 13, 13)
        layout.setSpacing(11)
        heading = QLabel("历史任务")
        heading.setObjectName("historyHeading")
        layout.addWidget(heading)
        new_button = QPushButton("＋  新研究")
        new_button.setObjectName("historyNew")
        new_button.clicked.connect(self.newResearchRequested.emit)
        layout.addWidget(new_button)
        self.search = QLineEdit()
        self.search.setObjectName("historySearch")
        self.search.setPlaceholderText("搜索研究题目")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._render)
        layout.addWidget(self.search)
        count_row = QHBoxLayout()
        self.count = QLabel()
        self.count.setObjectName("historyCount")
        count_row.addWidget(self.count)
        count_row.addStretch(1)
        self.rename_button = QPushButton("重命名")
        self.rename_button.setObjectName("historyRename")
        self.rename_button.setToolTip("选中历史任务后重命名侧栏标题；客户原文和报告不变")
        self.rename_button.setEnabled(False)
        self.rename_button.clicked.connect(self._request_rename)
        count_row.addWidget(self.rename_button)
        layout.addLayout(count_row)
        self.list = QListWidget()
        self.list.setObjectName("historyTasks")
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.itemClicked.connect(self._open_task)
        self.list.itemActivated.connect(self._open_task)
        self.list.currentItemChanged.connect(self._update_rename_button)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.list, 1)
        self.set_tasks([])

    def set_tasks(self, tasks: list[HistoryTask]) -> None:
        self._tasks = tasks
        self._render()

    def _render(self) -> None:
        query = " ".join(self.search.text().casefold().split())
        matches = [task for task in self._tasks
                   if query in task.title.casefold() or query in task.original_title.casefold()]
        self.count.setText(f"{len(matches)} 项研究" if self._tasks else "尚无历史研究")
        self.list.clear()
        if not matches:
            item = QListWidgetItem("没有匹配的研究" if query else "完成研究后，历史任务会显示在这里")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(item)
            return
        last_group = ""
        for task in matches:
            group = self._date_group(task.updated_at)
            if group != last_group:
                heading = QListWidgetItem(group)
                heading.setFlags(Qt.ItemFlag.NoItemFlags)
                heading.setForeground(Qt.GlobalColor.gray)
                self.list.addItem(heading)
                last_group = group
            suffix = f" · {task.attempts} 次运行" if task.attempts > 1 else ""
            title = task.title + suffix
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, str(task.display_run_path))
            item.setData(self.TASK_KEY_ROLE, task.task_key)
            item.setData(self.TITLE_ROLE, task.title)
            item.setToolTip(
                f"{task.title}\n原始需求：{task.original_title or task.title}"
                f"\n最近状态：{task.latest_status}；共 {task.attempts} 次运行"
                + ("\n打开最近一次可用报告" if task.has_report else "\n暂无可用报告")
            )
            self.list.addItem(item)

    @staticmethod
    def _date_group(timestamp: float) -> str:
        day = datetime.fromtimestamp(timestamp).date()
        today = date.today()
        if day == today:
            return "今天"
        if day == today - timedelta(days=1):
            return "昨天"
        if day >= today - timedelta(days=7):
            return "最近 7 天"
        return "更早"

    def _open_task(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self.taskSelected.emit(str(path))

    def _update_rename_button(self, item: QListWidgetItem | None = None, _previous=None) -> None:
        self.rename_button.setEnabled(bool(item and item.data(self.TASK_KEY_ROLE)))

    def _show_context_menu(self, position) -> None:
        item = self.list.itemAt(position)
        if not item or not item.data(self.TASK_KEY_ROLE):
            return
        self.list.setCurrentItem(item)
        menu = QMenu(self)
        rename = menu.addAction("重命名任务")
        if menu.exec(self.list.mapToGlobal(position)) == rename:
            self._request_rename()

    def _request_rename(self) -> None:
        item = self.list.currentItem()
        if not item:
            return
        task_key = item.data(self.TASK_KEY_ROLE)
        if not task_key:
            return
        name, accepted = QInputDialog.getText(
            self, "重命名历史任务", "新名称（只修改侧栏标题）：",
            text=str(item.data(self.TITLE_ROLE) or ""),
        )
        if not accepted:
            return
        cleaned = " ".join(name.split())
        if not cleaned or len(cleaned) > 80:
            QMessageBox.information(self, "名称无效", "请输入 1–80 个字符的任务名称。")
            return
        self.renameRequested.emit(str(task_key), cleaned)
