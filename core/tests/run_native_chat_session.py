"""Keep the installed App alive for scoped Accessibility and print-panel QA."""
import hashlib
import json
from pathlib import Path
import subprocess

from rebuild_package_acceptance import native_start, stop

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "result/acceptance/native-intensive"
APP = Path("/Applications/ResearchHelper.app")


def boundaries():
    directory = Path.home() / "Library/Application Support/ResearchHelper"
    legacy = {}
    for filename in ("researchhelper.sqlite3", "config.local.json"):
        path = directory / filename
        if path.is_file():
            legacy[filename] = hashlib.sha256(path.read_bytes()).hexdigest()
    option = subprocess.check_output(["git", "status", "--porcelain=v1"],
                                     cwd="/Users/haoranxu/Desktop/OptionHelper")
    option_diff = subprocess.check_output(["git", "diff", "--binary"],
                                          cwd="/Users/haoranxu/Desktop/OptionHelper")
    return {"legacy_sha256": legacy, "option_status_sha256": hashlib.sha256(option).hexdigest(),
            "option_diff_sha256": hashlib.sha256(option_diff).hexdigest()}


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    before = boundaries()
    (OUT / "readonly-before.json").write_text(json.dumps(before, indent=2))
    with (OUT / "native-chat.log").open("wb") as log:
        process, address, children = native_start(APP, OUT / "chat-store", log)
        session = {"pid": process.pid, "children": children, "url": address,
                   "store": str(OUT / "chat-store"), "installed_app": str(APP)}
        (OUT / "session.json").write_text(json.dumps(session, indent=2))
        print(json.dumps(session), flush=True)
        try:
            exit_code = process.wait()
            (OUT / "exit.json").write_text(json.dumps({"pid": process.pid, "exit_code": exit_code}))
        finally:
            stop(process, children)
            after = boundaries()
            assert before == after, "Read-only boundary changed during installed App QA"
            (OUT / "readonly-after.json").write_text(json.dumps(after, indent=2))
            print("Installed native chat closed; child reclaimed; legacy and OptionHelper unchanged", flush=True)


if __name__ == "__main__":
    run()
