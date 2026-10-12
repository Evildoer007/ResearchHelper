from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QPushButton

from gui import app as gui_app


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_application_settings_is_the_single_configuration_entry(qapp, tmp_path, monkeypatch):
    config_path = tmp_path / "config.local.json"
    config_path.write_text(json.dumps({
        "DEEPSEEK_API_KEY": "configured",
        "SEARCH_PROVIDER": "bing",
        "IFIND_REFRESH_TOKEN": "configured",
    }), encoding="utf-8")
    monkeypatch.setattr(gui_app, "LOCAL_CONFIG", config_path)
    dialog = gui_app.ApplicationSettingsDialog()
    labels = {button.text() for button in dialog.findChildren(QPushButton)}
    assert {"模型与 API", "搜索与 API", "iFinD 设置", "OptionHelper 设置", "运行环境检查"} <= labels
    dialog.close()


def test_user_guide_can_open_without_webengine(qapp, tmp_path, monkeypatch):
    guide = tmp_path / "user-guide.html"
    guide.write_text("<html><body><h1>使用说明</h1></body></html>", encoding="utf-8")
    monkeypatch.setattr(gui_app, "QWebEngineView", None)
    dialog = gui_app.UserGuideDialog(guide)
    assert dialog.windowTitle() == "Research Helper 使用说明书"
    dialog.close()


def test_release_specs_bundle_the_user_guide():
    root = Path(__file__).resolve().parent.parent
    for name in ("ResearchHelper.spec", "ResearchHelper-macOS.spec"):
        content = (root / name).read_text(encoding="utf-8")
        assert 'root / "docs" / "user-guide.html"' in content
        assert 'root / "docs" / "images"' in content


def test_user_guide_screenshots_are_local_and_redacted():
    root = Path(__file__).resolve().parent.parent
    guide = (root / "docs" / "user-guide.html").read_text(encoding="utf-8")
    image_root = root / "docs" / "images" / "user-guide"
    expected = {
        "01-research-input.png",
        "02-settings-redacted.png",
        "03-scope-confirmation.png",
        "04-event-evidence.png",
        "05-logic-selection.png",
        "06-quote-underlying.png",
        "07-optionhelper-review.png",
        "08-report-editor.png",
    }
    assert expected <= {path.name for path in image_root.glob("*.png")}
    assert "C:\\Users\\87055" not in guide
    for name in expected:
        assert f"images/user-guide/{name}" in guide
    assert 'href="images/user-guide/' not in guide
    assert guide.index('id="overview"') < guide.index('id="quickstart"')
    assert 'id="screenshots"' not in guide


def test_duplicate_internal_history_panel_is_removed():
    root = Path(__file__).resolve().parent.parent
    content = (root / "gui" / "app.py").read_text(encoding="utf-8")
    assert "历史运行与内部底稿" not in content
    assert "刷新运行记录" not in content
    assert "历史任务、旧报告和再次使用入口已统一放在左侧历史任务栏" in content
