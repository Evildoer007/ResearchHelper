"""一页通结构化人工编辑窗口。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QSplitter, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from core.report_edits import (
    apply_edits, chart_sections, editable_fields, field_label, final_pdf_path, load_revision, logic_sections,
    save_revision,
)

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
except ImportError:  # pragma: no cover - 精简安装仍可编辑和保存 HTML
    QWebEngineView = None


class ReportEditorDialog(QDialog):
    """编辑白名单字段；报价、数据、来源、日期和免责声明从不进入表单。"""

    def __init__(self, parent, *, base_html: Path) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑一页通报告")
        available = (self.screen() or QApplication.primaryScreen()).availableGeometry()
        self.resize(min(1440, available.width()), min(900, available.height()))
        self.base_html = Path(base_html).resolve()
        self.source = self.base_html.read_text(encoding="utf-8")
        generated_fields = editable_fields(self.source)
        previous = load_revision(self.base_html)
        saved_fields = previous.get("fields") if isinstance(previous.get("fields"), dict) else {}
        self.values = {key: str(saved_fields.get(key, value)) for key, value in generated_fields.items()}
        default_order = logic_sections(self.source)
        requested = [str(key) for key in (previous.get("logic_order") or []) if str(key) in default_order]
        self.logic_order = requested + [key for key in default_order if key not in requested]
        self.hidden_logic = {str(key) for key in (previous.get("hidden_logic") or [])}
        self.chart_keys = chart_sections(self.source)
        self.hidden_charts = {str(key) for key in (previous.get("hidden_charts") or [])}
        self.current_key = ""
        self.saved_result: dict | None = None
        self._preview_file: Path | None = None
        self._export_after_load = False
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._refresh_preview)

        self.field_list = QListWidget()
        for key in generated_fields:
            item = QListWidgetItem(field_label(key))
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.field_list.addItem(item)
        self.field_list.currentItemChanged.connect(self._field_changed)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("在这里修改选中区块。支持换行及 **加粗**；粘贴的网页样式不会进入报告。")
        self.editor.textChanged.connect(self._text_changed)
        self.field_hint = QLabel("只开放客户报告文字。报价、数据、来源、日期和免责声明保持锁定。")
        self.field_hint.setWordWrap(True)
        field_page = QWidget()
        field_layout = QVBoxLayout(field_page)
        field_layout.addWidget(self.field_hint)
        field_layout.addWidget(self.editor, 1)

        self.logic_list = QListWidget()
        for key in self.logic_order:
            item = QListWidgetItem(self._logic_label(key))
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked if key in self.hidden_logic else Qt.CheckState.Checked)
            self.logic_list.addItem(item)
        self.logic_list.itemChanged.connect(lambda _item: self._schedule_preview())
        up = QPushButton("上移")
        down = QPushButton("下移")
        up.clicked.connect(lambda: self._move_logic(-1))
        down.clicked.connect(lambda: self._move_logic(1))
        logic_page = QWidget()
        logic_layout = QVBoxLayout(logic_page)
        logic_help = QLabel("勾选决定是否显示该论点；上移、下移只调整策略逻辑顺序，不影响正式报价表。")
        logic_help.setWordWrap(True)
        logic_layout.addWidget(logic_help)
        logic_layout.addWidget(self.logic_list, 1)
        controls = QHBoxLayout(); controls.addWidget(up); controls.addWidget(down); controls.addStretch(1)
        logic_layout.addLayout(controls)

        self.chart_list = QListWidget()
        for key in self.chart_keys:
            item = QListWidgetItem(self._chart_label(key))
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked if key in self.hidden_charts else Qt.CheckState.Checked)
            self.chart_list.addItem(item)
        self.chart_list.itemChanged.connect(lambda _item: self._schedule_preview())
        chart_page = QWidget()
        chart_layout = QVBoxLayout(chart_page)
        chart_help = QLabel(
            "取消勾选即可从人工修订版删除该图表；若只剩一张紧凑图，会自动与正文左右排版。"
            "宽图仍保持整行，自动生成原稿不会被覆盖。"
        )
        chart_help.setWordWrap(True)
        chart_layout.addWidget(chart_help)
        chart_layout.addWidget(self.chart_list, 1)
        remove_chart = QPushButton("删除选中图表")
        restore_chart = QPushButton("恢复选中图表")
        remove_chart.clicked.connect(lambda: self._set_selected_chart_visible(False))
        restore_chart.clicked.connect(lambda: self._set_selected_chart_visible(True))
        chart_controls = QHBoxLayout()
        chart_controls.addWidget(remove_chart)
        chart_controls.addWidget(restore_chart)
        chart_controls.addStretch(1)
        chart_layout.addLayout(chart_controls)

        tabs = QTabWidget()
        tabs.addTab(field_page, "文字")
        tabs.addTab(logic_page, "论点顺序与显示")
        tabs.addTab(chart_page, "图表显示")

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("可编辑区块"))
        left_layout.addWidget(self.field_list, 2)
        left_layout.addWidget(tabs, 5)

        self.preview = QWebEngineView() if QWebEngineView is not None else QTextBrowser()
        if QWebEngineView is not None and isinstance(self.preview, QWebEngineView):
            self.preview.loadFinished.connect(self._preview_loaded)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("实时预览（最终是否为一页仍以导出 PDF 实测为准）"))
        right_layout.addWidget(self.preview, 1)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([440, 980])

        self.status = QLabel("尚未保存人工修订。")
        self.status.setWordWrap(True)
        self.save_button = QPushButton("保存修订版 HTML")
        self.save_button.setProperty("role", "primary")
        self.export_button = QPushButton("保存并导出 PDF")
        self.folder_button = QPushButton("打开文件夹")
        self.folder_button.setEnabled(False)
        reset = QPushButton("重置为自动生成内容")
        close = QPushButton("关闭")
        self.save_button.clicked.connect(self.save)
        self.export_button.clicked.connect(self.export_pdf)
        self.folder_button.clicked.connect(self._open_output_folder)
        reset.clicked.connect(self.reset_to_generated)
        close.clicked.connect(self.accept)
        self.export_button.setEnabled(QWebEngineView is not None)

        actions = QHBoxLayout()
        actions.addWidget(QLabel("人工修订"))
        actions.addStretch(1)
        actions.addWidget(reset)
        actions.addWidget(self.folder_button)
        actions.addWidget(self.save_button)
        actions.addWidget(self.export_button)
        actions.addWidget(close)
        root = QVBoxLayout(self)
        root.addLayout(actions)
        root.addWidget(self.status)
        root.addWidget(split, 1)

        if self.field_list.count():
            self.field_list.setCurrentRow(0)
        self._refresh_preview()

    def _logic_label(self, key: str) -> str:
        title = self.values.get(key + "_title", "").strip()
        return title or key

    def _chart_label(self, key: str) -> str:
        title = self.values.get(key + "_title", "").strip()
        if title:
            return title
        parts = key.split("_")
        if len(parts) >= 4:
            return f"策略逻辑{parts[1]} · 图表{parts[3]}"
        return key

    def _store_current(self) -> None:
        if self.current_key:
            self.values[self.current_key] = self.editor.toPlainText().strip()

    def _field_changed(self, current, _previous) -> None:
        self._store_current()
        self.current_key = str(current.data(Qt.ItemDataRole.UserRole)) if current else ""
        self.editor.blockSignals(True)
        self.editor.setPlainText(self.values.get(self.current_key, ""))
        self.editor.blockSignals(False)

    def _text_changed(self) -> None:
        self._store_current()
        self._schedule_preview()

    def _logic_state(self) -> tuple[list[str], list[str]]:
        order: list[str] = []
        hidden: list[str] = []
        for index in range(self.logic_list.count()):
            item = self.logic_list.item(index)
            key = str(item.data(Qt.ItemDataRole.UserRole))
            order.append(key)
            if item.checkState() != Qt.CheckState.Checked:
                hidden.append(key)
        return order, hidden

    def _chart_state(self) -> list[str]:
        hidden: list[str] = []
        for index in range(self.chart_list.count()):
            item = self.chart_list.item(index)
            if item.checkState() != Qt.CheckState.Checked:
                hidden.append(str(item.data(Qt.ItemDataRole.UserRole)))
        return hidden

    def _set_selected_chart_visible(self, visible: bool) -> None:
        item = self.chart_list.currentItem()
        if item is not None:
            item.setCheckState(Qt.CheckState.Checked if visible else Qt.CheckState.Unchecked)

    def _move_logic(self, offset: int) -> None:
        row = self.logic_list.currentRow()
        target = row + offset
        if row < 0 or target < 0 or target >= self.logic_list.count():
            return
        item = self.logic_list.takeItem(row)
        self.logic_list.insertItem(target, item)
        self.logic_list.setCurrentRow(target)
        self._schedule_preview()

    def _schedule_preview(self) -> None:
        self._preview_timer.start(250)

    def _rendered(self) -> str:
        self._store_current()
        order, hidden = self._logic_state()
        return apply_edits(
            self.source, fields=self.values, logic_order=order, hidden_logic=hidden,
            hidden_charts=self._chart_state(),
        )

    def _refresh_preview(self) -> None:
        rendered = self._rendered()
        if QWebEngineView is not None and isinstance(self.preview, QWebEngineView):
            if self._preview_file is None:
                handle = tempfile.NamedTemporaryFile(
                    "w", encoding="utf-8", suffix=".html", prefix="report-edit-preview-",
                    dir=self.base_html.parent, delete=False,
                )
                self._preview_file = Path(handle.name)
                handle.close()
            self._preview_file.write_text(rendered, encoding="utf-8")
            self.preview.load(QUrl.fromLocalFile(str(self._preview_file)))
        else:
            self.preview.setHtml(rendered)

    def save(self) -> bool:
        try:
            order, hidden = self._logic_state()
            final, sidecar, payload = save_revision(
                self.base_html, fields=self.values, logic_order=order, hidden_logic=hidden,
                hidden_charts=self._chart_state(),
            )
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "无法保存修订", str(error))
            return False
        self.saved_result = {
            "html": str(final), "sidecar": str(sidecar), "revision": payload["revision"],
            "edited_at": payload["edited_at"], "pdf": "", "pdf_pages": None,
        }
        self.folder_button.setEnabled(True)
        self.status.setText(f"已保存 v{payload['revision']}：{final}。可继续导出 PDF。")
        return True

    def _open_output_folder(self) -> None:
        if self.saved_result:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.saved_result["html"]).parent)))

    def export_pdf(self) -> None:
        if not self.save():
            return
        if QWebEngineView is None or not isinstance(self.preview, QWebEngineView):
            QMessageBox.warning(self, "无法导出 PDF", "当前环境未安装 QtWebEngine。")
            return
        final = Path(str(self.saved_result["html"]))
        self._export_after_load = True
        self.status.setText("正在加载修订版并导出 PDF…")
        self.preview.load(QUrl.fromLocalFile(str(final)))

    def _preview_loaded(self, ok: bool) -> None:
        if not self._export_after_load:
            return
        self._export_after_load = False
        if not ok or not self.saved_result:
            QMessageBox.warning(self, "无法导出 PDF", "修订版 HTML 加载失败。")
            return
        from render.pdf_out import page_count, print_page_async

        target = final_pdf_path(self.base_html)

        def done(path: str | None) -> None:
            if not path:
                QMessageBox.warning(self, "无法导出 PDF", "printToPdf 没有返回文件。")
                return
            try:
                pages = page_count(path)
            except (OSError, ValueError):
                pages = None
            self.saved_result["pdf"] = str(path)
            self.saved_result["pdf_pages"] = pages
            if pages == 1:
                self.status.setText(f"修订版 PDF 已导出并通过一页校验：{path}")
                QMessageBox.information(self, "导出完成", f"修订版 PDF 已导出，实测为 1 页。\n{path}")
            elif pages is None:
                self.status.setText(f"修订版 PDF 已导出，但页数无法校验：{path}")
                QMessageBox.warning(self, "需要复核", f"PDF 已导出，但未能确认真实页数。\n{path}")
            else:
                self.status.setText(f"修订版 PDF 实测 {pages} 页，不能作为一页通正式交付：{path}")
                QMessageBox.warning(self, "超过一页", f"修订版 PDF 实测 {pages} 页，请压缩内容后重新导出。\n{path}")

        print_page_async(self.preview.page(), target, on_done=done)

    def reset_to_generated(self) -> None:
        generated = editable_fields(self.source)
        self.values = dict(generated)
        self.current_key = ""
        self.field_list.setCurrentRow(-1)
        if self.field_list.count():
            self.field_list.setCurrentRow(0)
        for index in range(self.logic_list.count()):
            self.logic_list.item(index).setCheckState(Qt.CheckState.Checked)
        for index in range(self.chart_list.count()):
            self.chart_list.item(index).setCheckState(Qt.CheckState.Checked)
        default = logic_sections(self.source)
        items = {}
        while self.logic_list.count():
            item = self.logic_list.takeItem(0)
            items[str(item.data(Qt.ItemDataRole.UserRole))] = item
        for key in default:
            if key in items:
                self.logic_list.addItem(items[key])
        self.status.setText("已重置为自动生成内容；点击“保存修订”后生效。")
        self._refresh_preview()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._preview_file is not None:
            self._preview_file.unlink(missing_ok=True)
        super().closeEvent(event)


__all__ = ("ReportEditorDialog",)
