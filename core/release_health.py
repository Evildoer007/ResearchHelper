"""发布版自检：输出可交给维护人员的机器可读报告，不记录任何秘密值。"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from . import config
from .app_paths import CONFIG_PATH, OUTPUT_DIR, RESOURCE_ROOT, USER_DATA_ROOT, ensure_user_directories
from .sdk_bootstrap import configure_ifind_sdk


def _writable(directory: Path) -> tuple[bool, str]:
    probe = directory / ".write-test"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="ascii")
        probe.unlink()
        return True, "可写"
    except OSError as error:
        return False, f"不可写：{error}"


def collect() -> dict:
    ensure_user_directories()
    sdk_paths = configure_ifind_sdk()
    writable, writable_detail = _writable(USER_DATA_ROOT)
    checks = {
        "user_data_writable": {"ok": writable, "detail": writable_detail},
        "deepseek_configured": {"ok": bool(config.DEEPSEEK_API_KEY), "detail": "已配置" if config.DEEPSEEK_API_KEY else "未配置"},
        "tavily_configured": {"ok": bool(config.TAVILY_API_KEY), "detail": "已配置" if config.TAVILY_API_KEY else "未配置（可使用 Bing 兜底）"},
        "ifind_sdk": {"ok": importlib.util.find_spec("iFinDPy") is not None, "detail": "；".join(sdk_paths) or "未发现官方 SDK"},
        "ifind_credentials": {"ok": config.has_ifind(), "detail": "已配置" if config.has_ifind() else "未配置"},
        "optionhelper": {"ok": config.has_optionhelper(), "detail": "已就绪" if config.has_optionhelper() else "未配置或完整性检查未通过"},
        "echarts_asset": {"ok": (RESOURCE_ROOT / "assets" / "vendor" / "echarts.min.js").is_file(), "detail": "离线交互图运行库"},
    }
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "python": sys.version,
        "executable": sys.executable,
        "resource_root": str(RESOURCE_ROOT),
        "user_data_root": str(USER_DATA_ROOT),
        "config_path": str(CONFIG_PATH),
        "checks": checks,
        "ready_for_research": writable and bool(config.DEEPSEEK_API_KEY),
        "ready_for_formal_quote": writable and bool(config.DEEPSEEK_API_KEY) and config.has_optionhelper(),
    }


def write_report() -> Path:
    target = OUTPUT_DIR / "environment-check.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(collect(), ensure_ascii=False, indent=2), encoding="utf-8")
    return target
