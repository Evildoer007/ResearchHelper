r"""Research Helper 的安装资源与用户数据路径。

源码运行时保持原有目录布局，避免影响开发与既有测试；PyInstaller 发布版则把
只读程序资源与可写数据严格分开。最终用户的配置、材料、缓存、运行记录和报告
统一写入 ``%LOCALAPPDATA%\ResearchHelper``，不向安装目录写文件。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "ResearchHelper"
SOURCE_ROOT = Path(__file__).resolve().parent.parent
IS_FROZEN = bool(getattr(sys, "frozen", False))


def _resolved_env(name: str) -> Path | None:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        return None
    return Path(value).expanduser().resolve()


def resource_root() -> Path:
    """返回只读资源根目录；支持发布版外部 worker 显式传入。"""
    configured = _resolved_env("RESEARCH_HELPER_RESOURCE_ROOT")
    if configured is not None:
        return configured
    if IS_FROZEN:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve()
    return SOURCE_ROOT


def user_data_root() -> Path:
    """返回本机可写数据根目录。

    开发态默认仍使用仓库根目录；设置环境变量可在测试中验证发布态布局。
    """
    configured = _resolved_env("RESEARCH_HELPER_DATA_ROOT")
    if configured is not None:
        return configured
    if not IS_FROZEN:
        return SOURCE_ROOT
    local = str(os.environ.get("LOCALAPPDATA") or "").strip()
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return (base / APP_NAME).resolve()


RESOURCE_ROOT = resource_root()
USER_DATA_ROOT = user_data_root()
CONFIG_PATH = USER_DATA_ROOT / "config.local.json"
OUTPUT_DIR = USER_DATA_ROOT / "output"
RUNS_DIR = OUTPUT_DIR / "runs"
SOURCES_DIR = USER_DATA_ROOT / "sources"
DATA_CACHE_DIR = USER_DATA_ROOT / "data_cache"
MATERIALS_DIR = OUTPUT_DIR / "materials"
OPTIONHELPER_DIR = USER_DATA_ROOT / ".optionhelper"
DATA_DIR = USER_DATA_ROOT / "data"
RESULT_DIR = USER_DATA_ROOT / "result"


def ensure_user_directories() -> None:
    """创建发布版需要的可写目录；重复调用安全。"""
    for path in (
        USER_DATA_ROOT, OUTPUT_DIR, RUNS_DIR, SOURCES_DIR, DATA_CACHE_DIR,
        MATERIALS_DIR, OPTIONHELPER_DIR, DATA_DIR, RESULT_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)


def runtime_source_root() -> Path:
    """外部 OptionHelper Python 可执行的兼容源码目录。"""
    bundled = RESOURCE_ROOT / "runtime_source"
    return bundled if bundled.is_dir() else RESOURCE_ROOT
