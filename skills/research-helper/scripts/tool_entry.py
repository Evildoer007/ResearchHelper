#!/usr/bin/env python3
"""Stable JSON entry point for the Research Helper skill.

This file deliberately contains only orchestration. Research rules remain in the
Research Helper runtime so the desktop app and skill do not diverge.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", line_buffering=True)
    except (AttributeError, OSError):
        pass


SKILL_ROOT = Path(__file__).resolve().parent.parent
SKILL_VERSION = (SKILL_ROOT / "VERSION").read_text(encoding="utf-8").strip()
RUN_ID_RE = re.compile(r"运行编号：(run-[A-Za-z0-9_.-]+)")


def _runtime_root() -> Path:
    configured = str(os.environ.get("RESEARCH_HELPER_ROOT") or "").strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.append(Path(__file__).resolve().parent / "runtime_source")
    candidates.extend(SKILL_ROOT.parents)
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if (resolved / "main.py").is_file() and (resolved / "core" / "brief.py").is_file():
            return resolved
    raise FileNotFoundError(
        "未找到 Research Helper 运行源码。请使用打包后的 Skill，或设置 RESEARCH_HELPER_ROOT。"
    )


def _platform_data_root() -> Path:
    configured = str(os.environ.get("RESEARCH_HELPER_DATA_ROOT") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    runtime = _runtime_root()
    # 源码 Skill 与桌面开发环境共享本机配置；打包 Skill 不携带配置，读取用户目录。
    if (runtime / "config.local.json").is_file() and runtime.name != "runtime_source":
        return runtime
    system = platform.system().lower()
    if system == "windows":
        # Codex 的本地执行沙箱会把 LOCALAPPDATA 映射到 Packages/sandbox.../AC。
        # 凭证不应复制进临时沙箱；它们固定保存在当前 Windows 用户真实的
        # AppData/Local/ResearchHelper。USERPROFILE/Path.home 在该沙箱中仍指向
        # 实际用户目录，因此优先由它推导路径。
        home = Path(os.environ.get("USERPROFILE") or Path.home()).expanduser()
        base = home / "AppData" / "Local"
    elif system == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return (base / "ResearchHelper").resolve()


def _prepare_runtime() -> tuple[Path, Path]:
    runtime = _runtime_root()
    data_root = _platform_data_root()
    os.environ["RESEARCH_HELPER_RESOURCE_ROOT"] = str(runtime)
    os.environ["RESEARCH_HELPER_DATA_ROOT"] = str(data_root)
    if str(runtime) not in sys.path:
        sys.path.insert(0, str(runtime))
    # Keep the Skill aligned with the desktop launcher. On Windows, the official
    # iFinD module and native DLLs remain outside the package and are discovered
    # from the user's configured SDK or standard Python installations.
    from core.sdk_bootstrap import configure_ifind_sdk
    configure_ifind_sdk()
    return runtime, data_root


def _read_payload(path: str = "") -> dict[str, Any]:
    if path:
        raw = Path(path).read_text(encoding="utf-8")
    elif sys.stdin.isatty():
        raw = "{}"
    else:
        raw = sys.stdin.read()
    if not raw.strip():
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("输入必须是 JSON 对象")
    return value


def _emit(payload: dict[str, Any], *, exit_code: int = 0) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return exit_code


def _list_tools() -> dict[str, Any]:
    return {
        "ok": True,
        "skill": "research-helper",
        "version": SKILL_VERSION,
        "actions": {
            "environment-check": "检查运行源码、依赖与本机配置；不访问外部接口",
            "analyze-request": "解析客户需求并形成待确认的研究取数目标候选",
            "verify-target": "校验分析师确认的市场、研究路径与取数目标",
            "prepare-viewpoints": "完成取数并返回待分析师确认的候选观点；不生成报告",
            "run-research": "在确认信息齐全后运行研究、生成报告并返回交付状态",
            "latest-run": "读取最近一次或指定 run_id 的可追踪运行摘要",
        },
    }


def _environment_check() -> dict[str, Any]:
    runtime, data_root = _prepare_runtime()
    modules = [
        "requests", "pandas", "numpy", "matplotlib", "seaborn", "pyecharts",
        "pypdf", "docx", "PySide6", "core.brief", "core.market_confirmation",
        "core.pipeline", "render.layout",
    ]
    imports: dict[str, dict[str, Any]] = {}
    for name in modules:
        try:
            module = __import__(name, fromlist=["*"])
            imports[name] = {"ok": True, "version": str(getattr(module, "__version__", ""))}
        except Exception as error:  # noqa: BLE001 - readiness report must retain every failure
            imports[name] = {"ok": False, "error": f"{type(error).__name__}: {error}"}

    from core import config

    credentials = {
        "deepseek": bool(config.DEEPSEEK_API_KEY),
        "ifind": bool(config.has_ifind()),
        "tavily": bool(config.TAVILY_API_KEY),
    }
    missing_imports = [name for name, value in imports.items() if not value["ok"]]
    missing_required_credentials = [
        name for name in ("deepseek", "ifind") if not credentials[name]
    ]
    missing_optional_credentials = [
        name for name in ("tavily",) if not credentials[name]
    ]
    ready = not missing_imports and not missing_required_credentials
    result = {
        "ok": ready,
        "ready_for_research": ready,
        "skill": "research-helper",
        "version": SKILL_VERSION,
        "python": {"executable": sys.executable, "version": platform.python_version()},
        "runtime_root": str(runtime),
        "data_root": str(data_root),
        "imports": imports,
        "credentials_configured": credentials,
        "optionhelper_configured": bool(config.has_optionhelper()),
        "missing_imports": missing_imports,
        "missing_required_credentials": missing_required_credentials,
        "missing_optional_credentials": missing_optional_credentials,
        "missing_credentials": missing_required_credentials + missing_optional_credentials,
        "note": ("凭证状态仅表示本机已配置；本检查不会输出密钥，也不会调用外部接口。"
                 "Tavily 为可选项，缺失时公开证据检索使用 Bing RSS 兜底。"),
    }
    if ready:
        result.update({
            "next_action": "analyze-request",
            "next_prompt": "环境检查通过。请提供要研究的投资问题；我会先解析需求，再请你确认取数目标。",
        })
    else:
        result.update({
            "next_action": "configure-environment",
            "next_prompt": "环境尚未就绪。请按缺失项完成配置，回复“已配置”后我会重新检查。",
        })
    return result


def _brief_to_dict(value: Any) -> dict[str, Any]:
    return dataclasses.asdict(value)


def _brief_from_dict(raw: dict[str, Any]) -> Any:
    from core.brief import Brief, ImpactBranch, MarketClaim, TargetRef

    fields = {item.name for item in dataclasses.fields(Brief)}
    data = {key: value for key, value in raw.items() if key in fields}
    data["市场判断"] = [
        MarketClaim(**item) for item in data.get("市场判断", []) if isinstance(item, dict)
    ]
    for key in ("候选标的", "主题篮子候选", "主题篮子候选池"):
        data[key] = [TargetRef(**item) for item in data.get(key, []) if isinstance(item, dict)]
    trigger = data.get("触发实体")
    data["触发实体"] = TargetRef(**trigger) if isinstance(trigger, dict) else None
    data["候选影响分支"] = [
        ImpactBranch(**item) for item in data.get("候选影响分支", []) if isinstance(item, dict)
    ]
    return Brief(**data)


def _analyze_request(payload: dict[str, Any]) -> dict[str, Any]:
    _prepare_runtime()
    from core import brief, market_confirmation
    from core.provider import get_provider

    request = str(payload.get("request") or "").strip()
    if not request:
        raise ValueError("缺少 request")
    parsed = brief.parse(request)
    result: dict[str, Any] = {
        "ok": bool(parsed.ok),
        "status": "needs_target_confirmation" if parsed.ok else "failed",
        "brief": _brief_to_dict(parsed),
        "rendered_brief": brief.render(parsed, 含外部事实=False),
        "error": parsed.error,
    }
    if parsed.ok and bool(payload.get("include_target_proposal", True)):
        result["target_proposal"] = market_confirmation.proposal(parsed, provider=get_provider())
    if parsed.ok:
        result.update({
            "next_action": "ask-target-confirmation",
            "next_prompt": (
                "请确认上面的研究市场、取数路径与取数目标。"
                "推荐直接回复“确认”；如需调整，请回复要修改的市场、路径或证券代码。"
            ),
        })
    else:
        result.update({
            "next_action": "revise-request",
            "next_prompt": "需求尚未形成可验证的研究路径。请补充研究对象、市场或证券代码后重试。",
        })
    return result


def _verify_target(payload: dict[str, Any]) -> dict[str, Any]:
    _prepare_runtime()
    from core import market_confirmation
    from core.provider import get_provider

    raw_brief = payload.get("brief")
    raw_confirmation = payload.get("confirmation")
    if not isinstance(raw_brief, dict):
        raise ValueError("缺少 analyze-request 返回的 brief")
    if not isinstance(raw_confirmation, dict):
        raise ValueError("缺少 confirmation")
    parsed = _brief_from_dict(raw_brief)
    value = market_confirmation.from_dict(raw_confirmation)
    checked = market_confirmation.verify(value, parsed, provider=get_provider())
    if checked.ok:
        market_confirmation.apply_to_brief(checked, parsed, provider=get_provider())
    result = {
        "ok": bool(checked.ok),
        "status": "confirmed" if checked.ok else "rejected",
        "errors": list(checked.errors),
        "warnings": list(checked.warnings),
        "confirmation": dataclasses.asdict(value),
        "exposure": dataclasses.asdict(checked.exposure) if checked.exposure is not None else None,
        "brief": _brief_to_dict(parsed),
    }
    if checked.ok:
        result.update({
            "next_action": "prepare-viewpoints",
            "next_prompt": (
                "取数目标已确认。是否开始取数并生成候选观点？"
                "推荐回复“开始”；如需返回修改目标，请回复“修改目标”。"
            ),
        })
    else:
        result.update({
            "next_action": "revise-target",
            "next_prompt": "目标校验未通过。请根据错误修改取数目标后重新确认。",
        })
    return result


def _load_overrides(payload: dict[str, Any]) -> Any:
    """Load analyst overrides and merge an explicitly confirmed event-evidence package."""
    from core import event_evidence
    from core import overrides as overrides_module

    path = str(payload.get("overrides_path") or "").strip()
    value = overrides_module.load(path or None)
    if not value.ok:
        raise ValueError("；".join(value.errors))
    raw_evidence = payload.get("event_evidence")
    if isinstance(raw_evidence, dict) and raw_evidence:
        if isinstance(raw_evidence.get("事件证据"), dict):
            raw_evidence = raw_evidence["事件证据"]
        parsed = event_evidence.parse(raw_evidence)
        if parsed.errors:
            raise ValueError("；".join(parsed.errors))
        value.事件证据 = parsed
    return value


def _candidate_to_dict(candidate: Any, index: int) -> dict[str, Any]:
    return {
        "index": index,
        "id": candidate.id,
        "kind": candidate.kind,
        "name": candidate.名称,
        "category": candidate.类别,
        "direction": candidate.方向,
        "basis": candidate.依据,
        "source": candidate.出处,
        "evidence_scope": candidate.证据范围,
        "evidence_subject": candidate.证据主体,
    }


def _prepare_viewpoints(payload: dict[str, Any]) -> dict[str, Any]:
    """Retrieve research data and expose candidates without rendering a report."""
    _prepare_runtime()
    from core import pipeline
    from core.provider import get_provider

    raw_brief = payload.get("brief")
    if not isinstance(raw_brief, dict):
        raise ValueError("缺少 verify-target 返回的 brief")
    parsed = _brief_from_dict(raw_brief)
    if not getattr(parsed, "市场确认", None):
        return {
            "ok": False,
            "status": "needs_input",
            "required_type": "market_confirmation",
            "next_action": "verify-target",
            "next_prompt": "尚未确认取数目标。请先确认研究市场、取数路径与目标。",
        }
    overrides = _load_overrides(payload)
    prepared = pipeline.prepare_from_brief(
        parsed, provider=get_provider(), with_docs=True, overrides=overrides,
    )
    if prepared is None:
        return {
            "ok": False,
            "status": "failed",
            "error": "已确认的取数目标无法形成研究数据，请返回修改目标。",
            "next_action": "revise-target",
            "next_prompt": "当前目标无法完成取数。是否返回修改研究路径或证券代码？",
        }
    candidates = pipeline.candidates(prepared)
    if not candidates:
        return {
            "ok": False,
            "status": "no_viewpoint_candidates",
            "candidates": [],
            "next_action": "revise-inputs",
            "next_prompt": "本次取数没有形成可用观点。是否补充材料、调整目标或允许重新取数？",
        }
    suggested_indices = pipeline.recommend(candidates)
    suggested_ids = [candidates[index - 1].id for index in suggested_indices]
    return {
        "ok": True,
        "status": "needs_viewpoint_confirmation",
        "candidates": [
            _candidate_to_dict(candidate, index)
            for index, candidate in enumerate(candidates, 1)
        ],
        "rendered_candidates": pipeline.render_candidates(candidates),
        "suggested_indices": suggested_indices,
        "suggested_ids": suggested_ids,
        "require_event_chain": bool(getattr(prepared, "event_required", False)),
        "next_action": "ask-viewpoint-confirmation",
        "next_prompt": (
            "请选择 2—3 条作为报告主轴。可回复序号或观点 ID；"
            "若接受系统建议，回复“采用建议组合”。"
        ),
    }


def _latest_run(payload: dict[str, Any]) -> dict[str, Any]:
    _, data_root = _prepare_runtime()
    directory = data_root / "output" / "runs"
    run_id = str(payload.get("run_id") or "").strip()
    path = directory / f"{run_id}.json" if run_id else None
    if path is None:
        candidates = sorted(directory.glob("run-*.json"), key=lambda item: item.stat().st_mtime,
                            reverse=True) if directory.is_dir() else []
        path = candidates[0] if candidates else None
    if path is None or not path.is_file():
        return {"ok": False, "status": "not_found", "error": "未找到运行摘要"}
    return {
        "ok": True,
        "status": "found",
        "summary_path": str(path),
        "summary": json.loads(path.read_text(encoding="utf-8")),
    }


def _child_command(runtime: Path, payload: dict[str, Any]) -> list[str]:
    request = str(payload.get("request") or "").strip()
    if not request:
        raise ValueError("缺少 request")
    command = [sys.executable, str(runtime / "main.py"), "-b", request, "--confirm-market"]
    if bool(payload.get("pdf", True)):
        command.append("--pdf")
    if "logic_picks" in payload or "logic_pick_ids" in payload:
        command.append("--pick")
    optionhelper = str(payload.get("optionhelper") or "").strip().lower()
    if optionhelper:
        if optionhelper not in {"recommend", "quote"}:
            raise ValueError("optionhelper 只能为空、recommend 或 quote")
        command.extend(["--optionhelper", optionhelper])
    overrides_path = str(payload.get("overrides_path") or "").strip()
    if overrides_path:
        command.extend(["--overrides", overrides_path])
    constraints = payload.get("client_constraints") or {}
    if isinstance(constraints, dict):
        mapping = {
            "horizon": "--horizon",
            "max_loss": "--max-loss",
            "return_preference": "--return-preference",
        }
        for key, flag in mapping.items():
            value = constraints.get(key)
            if value not in (None, ""):
                command.extend([flag, str(value)])
        if "principal_fluctuation" in constraints:
            command.extend([
                "--principal-fluctuation",
                "yes" if bool(constraints["principal_fluctuation"]) else "no",
            ])
    return command


def _logic_pick_reply(marker: dict[str, Any], payload: dict[str, Any]) -> str | None:
    """Resolve analyst selections to the current engine indices.

    IDs are preferred because a candidate order can change after refreshed data.
    ``None`` means the requested IDs are no longer available and the run must stop.
    """
    ids = payload.get("logic_pick_ids")
    if isinstance(ids, list):
        wanted = {str(item).strip() for item in ids if str(item).strip()}
        current = marker.get("candidates") or []
        indices = [
            int(item["index"])
            for item in current
            if isinstance(item, dict) and str(item.get("id") or "").strip() in wanted
        ]
        if not wanted or len(indices) != len(wanted):
            return None
        return " ".join(str(item) for item in indices)
    picks = payload.get("logic_picks")
    if isinstance(picks, list) and picks:
        return " ".join(str(item) for item in picks)
    return "" if bool(payload.get("auto_select_viewpoints", False)) else None


def _run_research(payload: dict[str, Any]) -> dict[str, Any]:
    runtime, data_root = _prepare_runtime()
    has_selection = bool(payload.get("logic_pick_ids") or payload.get("logic_picks"))
    if not has_selection and not bool(payload.get("auto_select_viewpoints", False)):
        return {
            "ok": False,
            "status": "needs_input",
            "required_type": "viewpoint_selection",
            "next_action": "prepare-viewpoints",
            "next_prompt": (
                "正式生成前需要确认报告观点。请先生成候选观点并选择 2—3 条；"
                "如确实希望系统自动选择，请明确回复“自动选择观点”。"
            ),
        }
    command = _child_command(runtime, payload)
    timeout_seconds = max(60, min(int(payload.get("timeout_seconds") or 3600), 14400))
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["RESEARCH_HELPER_RESOURCE_ROOT"] = str(runtime)
    env["RESEARCH_HELPER_DATA_ROOT"] = str(data_root)

    process = subprocess.Popen(
        command,
        cwd=runtime,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    lines: list[str] = []
    required_type = ""
    required_payload: dict[str, Any] | None = None
    market_attempts = 0
    started = time.monotonic()

    assert process.stdout is not None
    assert process.stdin is not None

    def reply(value: Any) -> None:
        if isinstance(value, str):
            text = value
        else:
            text = json.dumps(value, ensure_ascii=False)
        process.stdin.write(text + "\n")
        process.stdin.flush()

    while True:
        if time.monotonic() - started > timeout_seconds:
            process.kill()
            required_type = "timeout"
            required_payload = {"timeout_seconds": timeout_seconds}
            break
        line = process.stdout.readline()
        if not line:
            if process.poll() is not None:
                break
            time.sleep(0.05)
            continue
        clean = line.rstrip("\r\n")
        lines.append(clean)

        if clean.startswith("LLM_MODEL_CONFIRMATION_REQUIRED="):
            marker = json.loads(clean.split("=", 1)[1])
            if bool(payload.get("allow_model_fallback", False)):
                reply({"allow_fallback": True})
            else:
                required_type, required_payload = "model_fallback", marker
                reply({"allow_fallback": False})
        elif clean.startswith("MARKET_CONFIRMATION_REQUIRED="):
            marker = json.loads(clean.split("=", 1)[1])
            confirmation = payload.get("market_confirmation")
            market_attempts += 1
            if isinstance(confirmation, dict) and market_attempts == 1:
                reply(confirmation)
            else:
                required_type, required_payload = "market_confirmation", marker
                reply({"cancelled": True})
        elif clean.startswith("EVENT_EVIDENCE_CONTEXT_REQUIRED="):
            marker = json.loads(clean.split("=", 1)[1])
            evidence = payload.get("event_evidence")
            if isinstance(evidence, dict) and evidence:
                reply({"event_evidence": evidence})
            else:
                required_type, required_payload = "event_evidence", marker
                reply({"cancelled": True})
        elif clean.startswith("EVENT_EVIDENCE_REQUIRED="):
            required_type = "event_evidence"
            required_payload = json.loads(clean.split("=", 1)[1])
        elif clean.startswith("LOGIC_PICK_REQUIRED="):
            marker = json.loads(clean.split("=", 1)[1])
            selected = _logic_pick_reply(marker, payload)
            if selected is None:
                required_type, required_payload = "viewpoint_selection", marker
                process.terminate()
                break
            reply(selected)
            if not selected and bool(marker.get("require_event_chain")):
                lines.append("Skill: 分析师已明确允许自动选择，采用包含事件链的系统建议。")

    try:
        return_code = process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        return_code = process.wait()

    log_dir = data_root / "output" / "skill_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    run_id = ""
    for line in lines:
        match = RUN_ID_RE.search(line)
        if match:
            run_id = match.group(1)
            break
    log_name = f"{run_id or ('skill-' + time.strftime('%Y%m%d-%H%M%S'))}.log"
    log_path = log_dir / log_name
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    summary_path = data_root / "output" / "runs" / f"{run_id}.json" if run_id else None
    summary = None
    if summary_path is not None and summary_path.is_file():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            summary = None
    status = str((summary or {}).get("status") or ("completed" if return_code == 0 else "failed"))
    if required_type:
        status = "needs_input" if required_type != "timeout" else "timeout"
    result = {
        "ok": status.startswith("completed"),
        "status": status,
        "run_id": run_id,
        "return_code": return_code,
        "required_type": required_type,
        "required_payload": required_payload,
        "summary_path": str(summary_path) if summary_path is not None and summary_path.is_file() else "",
        "summary": summary,
        "log_path": str(log_path),
        "log_tail": lines[-80:],
    }
    if status.startswith("completed"):
        result.update({
            "next_action": "ask-delivery-action",
            "next_prompt": (
                "研究报告已生成。请选择下一步：打开并编辑报告、导出报告、"
                "进入 OptionHelper，或结束本次研究。"
            ),
        })
    elif status == "needs_input":
        prompts = {
            "model_fallback": "首选模型不可用。是否允许使用已显示的备用模型继续？",
            "market_confirmation": "取数目标需要重新确认。是否返回目标确认步骤？",
            "event_evidence": "事件型研究缺少已确认的证据链。是否开始查找并审核事件证据？",
            "viewpoint_selection": "候选观点已变化或尚未选择。是否重新生成并选择报告观点？",
        }
        result.update({
            "next_action": f"resolve-{required_type or 'required-input'}",
            "next_prompt": prompts.get(required_type, "运行需要补充信息。是否现在补充并继续？"),
        })
    elif status == "timeout":
        result.update({
            "next_action": "inspect-timeout",
            "next_prompt": "运行已超时。是否查看运行日志并定位停在哪一步？",
        })
    else:
        result.update({
            "next_action": "inspect-failure",
            "next_prompt": "本次运行未完成。是否查看日志并定位失败原因？",
        })
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Research Helper Skill JSON entry point")
    parser.add_argument("action", nargs="?", default="")
    parser.add_argument("--input", default="", help="UTF-8 JSON input file; otherwise stdin")
    parser.add_argument("--list", action="store_true", dest="list_tools")
    args = parser.parse_args()
    if args.list_tools or args.action == "list-tools":
        return _emit(_list_tools())
    try:
        payload = _read_payload(args.input)
        handlers = {
            "environment-check": lambda value: _environment_check(),
            "analyze-request": _analyze_request,
            "verify-target": _verify_target,
            "prepare-viewpoints": _prepare_viewpoints,
            "run-research": _run_research,
            "latest-run": _latest_run,
        }
        if args.action not in handlers:
            return _emit({
                "ok": False,
                "status": "invalid_action",
                "error": f"未知 action：{args.action or '（空）'}",
                "available": list(handlers),
            }, exit_code=2)
        result = handlers[args.action](payload)
        return _emit(result, exit_code=0 if result.get("ok", False) or result.get("status") in {
            "needs_target_confirmation", "confirmed", "rejected", "needs_input", "found",
            "needs_viewpoint_confirmation", "no_viewpoint_candidates"
        } else 1)
    except Exception as error:  # noqa: BLE001 - stable JSON boundary
        return _emit({
            "ok": False,
            "status": "error",
            "error": f"{type(error).__name__}: {error}",
        }, exit_code=1)


if __name__ == "__main__":
    raise SystemExit(main())
