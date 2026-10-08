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


def child_environment(*, external_python: bool = False) -> dict[str, str]:
    """传给子进程的路径契约。

    冻结程序的内部 ``--cli``/``--worker`` 仍由同一个可执行文件分派，必须以
    PyInstaller 资源根目录寻找 ``main.py``、assets 等文件。只有由独立
    OptionHelper Python 直接执行兼容源码 worker 时，才把资源根切到
    ``runtime_source``。两者混用会让内部研究进程错误寻找
    ``runtime_source/main.py``。
    """
    return {
        "RESEARCH_HELPER_DATA_ROOT": str(USER_DATA_ROOT),
        "RESEARCH_HELPER_RESOURCE_ROOT": str(
            runtime_source_root() if external_python else RESOURCE_ROOT
        ),
    }


def apply_to_qprocess(process, *, external_python: bool = False) -> None:
    """不覆盖已有模型等环境，仅补充发布态路径。"""
    environment = process.processEnvironment()
    if environment.isEmpty():
        from PySide6.QtCore import QProcessEnvironment
        environment = QProcessEnvironment.systemEnvironment()
    for key, value in child_environment(external_python=external_python).items():
        environment.insert(key, value)
    process.setProcessEnvironment(environment)
