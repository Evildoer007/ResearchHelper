import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from core.report_edits import edit_region, section_region
from gui import report_editor as editor_module


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _html():
    chart = section_region(
        "logic_1_chart_1",
        '<div class="rh-editable-chart"><div class="c-title">'
        + edit_region("logic_1_chart_1_title", "原图表")
        + '</div><div class="chart">图表内容</div></div>',
    )
    logic = (
        '<div class="logic"><span class="ltitle">'
        + edit_region("logic_1_title", "原论点")
        + '</span><div class="body">'
        + edit_region("logic_1_body", "原正文")
        + "</div>" + chart + "</div>"
    )
    return (
        '<html><body><div class="page"><h1>场外衍生品投资策略 '
        '<span class="accent">—— '
        + edit_region("report_title", "原题")
        + '</span></h1><div class="concl"><span class="lbl">核心结论</span>'
        + edit_region("core_conclusion", "原结论")
        + "</div>"
        + section_region("logic_1", logic)
        + '<section class="quote">正式报价 8.8%</section><div class="foot">来源</div>'
        + "</div></body></html>"
    )


def test_editor_saves_revision_without_editing_locked_quote(app, tmp_path, monkeypatch):
    monkeypatch.setattr(editor_module, "QWebEngineView", None)
    base = tmp_path / "onepager.html"
    base.write_text(_html(), encoding="utf-8")
    dialog = editor_module.ReportEditorDialog(None, base_html=base)
    dialog.show()
    QApplication.processEvents()
    assert dialog.save_button.isVisible()
    assert dialog.save_button.mapTo(dialog, dialog.save_button.rect().topLeft()).y() < 140
    assert not dialog.folder_button.isEnabled()
    for row in range(dialog.field_list.count()):
        item = dialog.field_list.item(row)
        if item.data(Qt.ItemDataRole.UserRole) == "core_conclusion":
            dialog.field_list.setCurrentRow(row)
            break
    dialog.editor.setPlainText("人工修改后的结论")
    assert dialog.save()
    assert dialog.folder_button.isEnabled()
    final = dialog.saved_result
    rendered = open(final["html"], encoding="utf-8").read()
    assert "人工修改后的结论" in rendered
    assert "正式报价 8.8%" in rendered
    assert base.read_text(encoding="utf-8") == _html()
    dialog.close()


def test_editor_can_remove_one_chart_and_keep_its_logic(app, tmp_path, monkeypatch):
    monkeypatch.setattr(editor_module, "QWebEngineView", None)
    base = tmp_path / "onepager.html"
    base.write_text(_html(), encoding="utf-8")
    dialog = editor_module.ReportEditorDialog(None, base_html=base)
    assert dialog.chart_list.count() == 1
    dialog.chart_list.setCurrentRow(0)
    dialog._set_selected_chart_visible(False)
    assert dialog.save()
    rendered = open(dialog.saved_result["html"], encoding="utf-8").read()
    assert "图表内容" not in rendered
    assert "原正文" in rendered
    dialog.close()
