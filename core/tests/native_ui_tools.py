"""Accessibility controls and screenshots limited to the installed QA process."""

import json
from pathlib import Path
import subprocess
import time

from core.tests.acceptance_safety import require_foreground_consent

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "result/acceptance/native-intensive"


def session():
    require_foreground_consent()
    value = json.loads((OUT / "session.json").read_text())
    command = subprocess.check_output(["ps", "-p", str(value["pid"]), "-o", "comm="], text=True).strip()
    if command != "/Applications/ResearchHelper.app/Contents/MacOS/ResearchHelper":
        raise RuntimeError("Owned native QA session is not alive")
    return value


def control(operation="tree", **parameters):
    request = dict(pid=session()["pid"], operation=operation, **parameters)
    response = subprocess.run([str(ROOT / "result/acceptance/native-accessibility")],
                              input=json.dumps(request), text=True, capture_output=True, check=True, timeout=15)
    value = json.loads(response.stdout)
    if "error" in value or value.get("result", 0) != 0:
        raise RuntimeError(str(value))
    return value


def nodes():
    return control()["nodes"]


def find(label, role=None, prefix=False):
    for item in nodes():
        if role and item["role"] != role:
            continue
        words = [item["title"], item["description"], item["value"]]
        if any(word.startswith(label) if prefix else word == label for word in words):
            return item
    raise AssertionError("Native element not found: " + label)


def press(label, role=None, prefix=False):
    return control("press", index=find(label, role, prefix)["index"])


def set_text(label, value, role="AXTextField"):
    return control("set", index=find(label, role)["index"], value=value)


def wait_for(label, role=None, prefix=False, timeout=8):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            return find(label, role, prefix)
        except AssertionError:
            time.sleep(.15)
    raise AssertionError("Native element did not appear: " + label)


def keys(phrase=None, code=None, modifiers=()):
    pid = session()["pid"]
    action = "keystroke " + json.dumps(phrase) if phrase is not None else "key code " + str(code)
    if modifiers:
        action += " using {" + ",".join(m + " down" for m in modifiers) + "}"
    script = f'tell application "System Events" to tell first process whose unix id is {pid}\nset frontmost to true\n{action}\nend tell'
    subprocess.run(["osascript", "-e", script], check=True, capture_output=True)


def screenshot(name, title="ResearchHelper"):
    window = find(title, "AXWindow")
    control("raise", index=window["index"])
    windows = control("windows")["windows"]
    options = [w for w in windows if w.get("kCGWindowName") == title]
    if not options:
        options = [w for w in windows if w["kCGWindowBounds"]["Width"] > 700]
    window = max(options, key=lambda w: w["kCGWindowBounds"]["Width"] * w["kCGWindowBounds"]["Height"])
    target = OUT / name
    subprocess.run(["/usr/sbin/screencapture", "-x", "-l", str(window["kCGWindowNumber"]), str(target)], check=True)
    return str(target)
