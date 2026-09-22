"""为源码态与冻结发布态生成一致的后台进程启动命令。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .app_paths import IS_FROZEN, RESOURCE_ROOT, USER_DATA_ROOT, runtime_source_root


_WORKERS = {
    "event-evidence": "event_evidence_worker.py",
    "model-catalog": "model_catalog_worker.py",
    "search-test": "search_test_worker.py",
    "product-profile": "product_profile_worker.py",
}


def internal_worker_command(name: str) -> tuple[str, list[str]]:
    filename = _WORKERS[name]
    if IS_FROZEN:
        return sys.executable, ["--worker", name]
    return sys.executable, [str(RESOURCE_ROOT / "core" / filename)]


def research_command(arguments: list[str]) -> tuple[str, list[str]]:
    if IS_FROZEN:
        return sys.executable, ["--cli", *arguments]
    return sys.executable, [str(RESOURCE_ROOT / "main.py"), *arguments]


def external_worker_path(filename: str) -> Path:
    """供独立 OptionHelper Python 使用；发布包会携带最小兼容源码树。"""
    return runtime_source_root() / "core" / filename


def child_environment() -> dict[str, str]:
    """传给内部/外部子进程的路径契约。"""
    return {
        "RESEARCH_HELPER_DATA_ROOT": str(USER_DATA_ROOT),
        "RESEARCH_HELPER_RESOURCE_ROOT": str(runtime_source_root()),
    }


def apply_to_qprocess(process) -> None:
    """不覆盖已有模型等环境，仅补充发布态路径。"""
    environment = process.processEnvironment()
    if environment.isEmpty():
        from PySide6.QtCore import QProcessEnvironment
        environment = QProcessEnvironment.systemEnvironment()
    for key, value in child_environment().items():
        environment.insert(key, value)
    process.setProcessEnvironment(environment)
