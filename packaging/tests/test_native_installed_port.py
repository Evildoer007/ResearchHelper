"""Real installed-App UI acceptance against the explicitly owned QA session."""
import hashlib
import json
from pathlib import Path
import time

from pypdf import PdfReader
import native_ui_tools as native


def identified(identifier, timeout=8):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        match = next((node for node in native.nodes() if node["identifier"] == identifier), None)
        if match:
            return match
        time.sleep(.1)
    raise AssertionError("Native field unavailable: " + identifier)


def save_panel(destination):
    """Resolve the exact destination before accepting any system Save panel."""
    assert destination.parent == native.OUT and not destination.exists()
    identified("saveAsNameTextField")
    # Filename fields sanitize slashes instead of selecting a destination.
    # Navigate through the system Go-to-folder sheet before setting a basename.
    native.keys(code=5, modifiers=("command", "shift"))
    folder = identified("PathTextField")
    native.control("set", index=folder["index"], value=str(destination.parent))
    native.control("focus", index=folder["index"])
    native.keys(code=36)
    field = identified("saveAsNameTextField")
    native.control("set", index=field["index"], value=destination.name)
    native.press("Save", "AXButton")
    end = time.monotonic() + 8
    while not destination.exists() and time.monotonic() < end:
        time.sleep(.1)
    assert destination.is_file() and destination.stat().st_size > 500


def run():
    record = {"version": "0.10.0", "checks": [], "screens": [], "errors": []}
    installation = json.loads((native.ROOT / "result/acceptance/installation.json").read_text())
    record["image_sha256"] = installation["image_sha256"]
    native.wait_for("ResChat")
    native.press("ResChat")
    native.press("运行设置", "AXButton")
    native.press("合成案例", "AXButton")
    native.press("剩余现金流的投资判断", "AXButton", prefix=True)
    native.press("打开研究产物", "AXButton")
    native.press("开始研究", "AXButton")
    native.wait_for("研究报告 已保存版本", "AXButton", prefix=True)
    native.press("收起任务与交付", "AXButton")
    record["checks"].append("真实原生按钮创建与执行离线案例，报告保存成功")

    native.press("ResDesk")
    for index, title in enumerate(("研究概览", "研究过程", "研究结论", "报告交付", "判断追踪")):
        native.press(title)
        native.wait_for("返回对话", "AXButton")
        record["screens"].append(native.screenshot(f"native-port-view-{index}.png"))
    record["checks"].append("ResDesk五视图实际点击与实屏检查")
    native.press("ResChat")
    window = native.find("ResearchHelper", "AXWindow")
    native.control("resize", index=window["index"], width=1024, height=768)
    record["screens"].append(native.screenshot("native-port-1024.png"))
    native.press("展开或收起任务栏", "AXButton")
    record["screens"].append(native.screenshot("native-port-collapsed.png"))
    native.press("展开或收起任务栏", "AXButton")
    native.control("resize", index=native.find("ResearchHelper", "AXWindow")["index"], width=1320, height=860)
    record["checks"].append("原生1024窗口、真实侧栏按钮与标题栏避让")

    # Physical key codes avoid changing the user's active input method.
    native.keys(code=35, modifiers=("command",))
    native.wait_for("PDF", "AXButton", timeout=12)
    record["screens"].append(native.screenshot("native-port-print-panel.png", title="研究报告"))
    native.press("PDF", "AXButton")
    suffix = time.strftime("%Y%m%d-%H%M%S")
    pdf = native.OUT / ("native-port-" + suffix + ".pdf")
    save_panel(pdf)
    reader = PdfReader(pdf)
    contents = [page.get_contents().get_data() for page in reader.pages]
    assert len(reader.pages) >= 2 and all(len(value) > 100 for value in contents), "Blank print output"
    text = "".join(page.extract_text() for page in reader.pages)
    assert "8.6364" in text, "Frozen research figure missing from native PDF"
    record["pdf"] = {"path": str(pdf), "pages": len(reader.pages), "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest()}
    native.control("closeWindow", index=native.find("研究报告", "AXWindow")["index"])
    assert not any(node["role"] == "AXWindow" and node["title"] == "研究报告" for node in native.nodes())
    record["checks"].append("从ResChat原生Cmd-P打印冻结报告，系统PDF保存非空且数字保留，报告窗口关闭")

    native.press("ResDesk")
    native.press("报告交付")
    native.press("保存HTML", "AXButton")
    html = native.OUT / ("native-port-" + suffix + ".html")
    save_panel(html)
    assert "8.6364" in html.read_text()
    record["html"] = {"path": str(html), "sha256": hashlib.sha256(html.read_bytes()).hexdigest()}
    record["checks"].append("NSSavePanel实际保存完整HTML至已解析的本项目路径")
    native.press("ResChat")
    native.keys(code=45, modifiers=("command",))
    native.wait_for("研究标的", "AXButton", prefix=True)
    record["screens"].append(native.screenshot("native-port-empty.png"))
    (native.OUT / "port-acceptance.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run()
