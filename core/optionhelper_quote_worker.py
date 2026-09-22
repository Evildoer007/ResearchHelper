"""接收一次性已确认候选，运行 OptionHelper Quote；不重新执行研究流程。"""

from __future__ import annotations

import json
import contextlib
import os
import sys
import traceback
from pathlib import Path
from typing import Mapping


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.optionhelper_pricing import apply_formal_quote_pricing_defaults
from core.optionhelper_compat import apply_quote_compatibility_patches


def _utf8_safe(value):
    if isinstance(value, str):
        return value.encode("utf-8", errors="replace").decode("utf-8")
    if isinstance(value, list):
        return [_utf8_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_utf8_safe(item) for item in value]
    if isinstance(value, Mapping):
        return {_utf8_safe(key): _utf8_safe(item) for key, item in value.items()}
    return value


def _configure_utf8_stdio() -> None:
    """GUI 的 QProcess 协议固定为 UTF-8，不服从 Windows 控制台代码页。"""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def build_project_request(body: Mapping) -> dict:
    """Preserve this quote's explicit overrides; never derive dates from research data."""
    request = {
        "prompt": str(body.get("prompt") or ""),
        "constraints": dict(body.get("constraints") or {}),
        "selection": dict(body.get("selection") or {}),
        "output_type": "quote", "format": "html",
    }
    for key in ("term_overrides", "pricing_config", "backtest_config"):
        value = body.get(key)
        if value is not None:
            if not isinstance(value, Mapping):
                raise ValueError(f"{key} 必须为 JSON 对象")
            request[key] = dict(value)
    variants = body.get("quote_variants")
    if variants is not None:
        if not isinstance(variants, list):
            raise ValueError("quote_variants 必须为 JSON 数组")
        request["quote_variants"] = variants
    return _utf8_safe(apply_formal_quote_pricing_defaults(request))


def main() -> None:
    _configure_utf8_stdio()
    try:
        body = json.loads(sys.stdin.read())
        if not isinstance(body, Mapping):
            raise ValueError("请求必须为 JSON 对象")
        skill_root = Path(str(body["skill_root"])).resolve()
        project_root = Path(str(body["project_root"])).resolve()
        scripts = skill_root / "scripts"
        if not (scripts / "tool_entry.py").is_file():
            raise ValueError("OptionHelper Skill 根目录无效")
        sys.path.insert(0, str(scripts))
        os.environ["OPTIONHELPER_RUNTIME_ROOT"] = str(project_root / ".optionhelper" / "runtime")
        os.environ["OPTIONHELPER_DATA_ROOT"] = str(project_root / "data")
        os.environ["OPTIONHELPER_RESULT_ROOT"] = str(project_root / "result")
        import tool_entry

        # Keep the activated Skill immutable: release-specific compatibility is
        # applied at the Host boundary after import and before the project run.
        # This currently prevents OptionHelper's internal derived ``G`` points
        # field from being projected into a client-facing Quote table.
        patches = apply_quote_compatibility_patches()
        if patches:
            print(
                "[ResearchHelper/optionhelper_compat/completed] " + ",".join(patches),
                file=sys.stderr,
                flush=True,
            )

        # stdout 是供 GUI 解析的唯一 JSON 协议；阶段信息写入 stderr。这样不会
        # 污染机器可读结果，同时可明确区分卡在鉴权、取数、定价还是生成报价表。
        def emit_progress(event: Mapping) -> None:
            stage = str(event.get("stage") or "optionhelper")
            status = str(event.get("status") or "running")
            message = _utf8_safe(str(event.get("message") or ""))
            print(f"[OptionHelper/{stage}/{status}] {message}", file=sys.stderr, flush=True)

        # 内部输出只能进入 stderr，不得污染 GUI 的单 JSON 结果协议。
        with contextlib.redirect_stdout(sys.stderr):
            result = tool_entry.run_project_request(
                build_project_request(body), project_root=project_root, progress=emit_progress,
            )
        print(json.dumps(_utf8_safe({"ok": True, "result": result}), ensure_ascii=False))
    except Exception as error:
        # 失败信息也必须保持合法 UTF-8，否则 GUI 只能看到“未返回有效报价”。
        response = {"ok": False, "message": f"{type(error).__name__}: {str(error)[:500]}"}
        if os.environ.get("RESEARCH_HELPER_DEBUG_QUOTE") == "1":
            response["debug_trace"] = traceback.format_exc()
        print(json.dumps(_utf8_safe(response), ensure_ascii=False))


if __name__ == "__main__":
    main()
