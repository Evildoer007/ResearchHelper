"""发现并加载终端用户机器上已有的 iFinD Quant SDK。

官方 SDK 不随 Research Helper 再分发。发布版会优先读取首次设置中指定的路径，
其次检查常见 Python/Anaconda 安装的 ``iFinDPy.pth``。这让安装包可以复用用户
依法安装的官方组件，同时不会把账号或专有 SDK 打入安装包。
"""

from __future__ import annotations

import json
import os
import site
import sys
from pathlib import Path

from .app_paths import CONFIG_PATH


def _config_sdk_path() -> str:
    try:
        value = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""
    return str(value.get("IFIND_SDK_PATH") or "").strip() if isinstance(value, dict) else ""


def _pth_candidates() -> list[Path]:
    home = Path.home()
    roots = [
        home / "anaconda3" / "Lib" / "site-packages",
        home / "miniconda3" / "Lib" / "site-packages",
    ]
    local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    programs = local / "Programs" / "Python"
    try:
        if programs.is_dir():
            roots.extend(path / "Lib" / "site-packages" for path in programs.glob("Python*"))
    except OSError:
        # 企业终端可能禁止枚举 LocalAppData 下的其它 Python；显式路径和 Anaconda
        # 候选仍可继续检查，不能因一次权限失败阻断整个桌面应用。
        pass
    return [root / "iFinDPy.pth" for root in roots]


def configure_ifind_sdk() -> list[str]:
    """把可用 SDK 路径加入当前进程；返回已加入的目录，未找到时返回空。"""
    candidates: list[str] = []
    explicit = str(os.environ.get("IFIND_SDK_PATH") or _config_sdk_path()).strip()
    if explicit:
        candidates.append(explicit)
    for pth in _pth_candidates():
        try:
            candidates.extend(
                line.strip() for line in pth.read_text(encoding="utf-8", errors="ignore").splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            )
            candidates.append(str(pth.parent))
        except OSError:
            continue
    added: list[str] = []
    for value in dict.fromkeys(candidates):
        path = Path(value).expanduser()
        if not path.exists():
            continue
        target = path if path.is_dir() else path.parent
        resolved = str(target.resolve())
        if resolved not in sys.path:
            site.addsitedir(resolved)
        try:
            os.add_dll_directory(resolved)
        except (AttributeError, OSError):
            pass
        added.append(resolved)
    return added
