"""为 OptionHelper 正式报价提供进程级超时保护。

GUI 的 Qt 定时器只能在事件循环可调度时生效；若 Qt/子进程状态异常，不能让
“报价中”永久留在界面。这个小包装器由同一解释器启动真实 worker，并在独立进程
内以 wall-clock 时间强制收束，不接触任何报价内容或凭证。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / "core" / "optionhelper_quote_worker.py"
TIMEOUT_SECONDS = 180


def main() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
    raw_request = sys.stdin.buffer.read()
    try:
        process = subprocess.Popen(
            [sys.executable, str(WORKER)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except OSError as error:
        sys.stdout.write(json.dumps({
            "ok": False, "reason": "worker_start",
            "message": f"OptionHelper 报价子进程无法启动：{type(error).__name__}: {error}",
        }, ensure_ascii=False))
        return
    try:
        stdout, stderr = process.communicate(raw_request, timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        _stdout, stderr = process.communicate()
        response = {
            "ok": False,
            "reason": "timeout",
            "timeout_seconds": TIMEOUT_SECONDS,
            "message": (
                f"OptionHelper 正式报价超过 {TIMEOUT_SECONDS} 秒已停止；"
                "请检查 iFinD 凭证、网络或 OptionHelper 运行日志后重试。"
            ),
        }
        sys.stdout.write(json.dumps(response, ensure_ascii=False))
        if stderr:
            sys.stderr.buffer.write(stderr[-2000:])
        return
    # 子 worker 的 stdout 是唯一 JSON 协议。若它在输出协议之前崩溃，
    # 包装器必须返回可区分的错误，而不是让 GUI 误认为“没有正式报价”。
    try:
        valid_output = isinstance(json.loads(stdout), dict)
    except (ValueError, UnicodeDecodeError):
        valid_output = False
    if not valid_output:
        sys.stdout.write(json.dumps({
            "ok": False,
            "reason": "worker_exit" if process.returncode else "invalid_worker_output",
            "worker_exit_code": process.returncode,
            "message": (
                f"OptionHelper 报价子进程未返回有效 JSON（exit={process.returncode}）；"
                "请查看本笔报价诊断中的错误输出。"
            ),
        }, ensure_ascii=False))
    else:
        sys.stdout.buffer.write(stdout)
    if stderr:
        sys.stderr.buffer.write(stderr)


if __name__ == "__main__":
    main()
