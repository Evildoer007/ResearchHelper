"""发现并加载终端用户机器上已有的 iFinD Quant SDK。

官方 SDK 不随 Research Helper 再分发。发布版会优先读取首次设置中指定的路径，
其次检查常见 Python/Anaconda 安装的 ``iFinDPy.pth``。这让安装包可以复用用户
依法安装的官方组件，同时不会把账号或专有 SDK 打入安装包。
"""

from __future__ import annotations

import json
import os
import platform
import site
import sys
import sysconfig
from contextlib import contextmanager
from pathlib import Path
from threading import RLock

from .app_paths import CONFIG_PATH, IS_FROZEN, USER_DATA_ROOT


_IMPORT_LOCK = RLock()
_PURELIB_ENV = "RESEARCH_HELPER_IFIND_PURELIB"


def _config_sdk_path() -> str:
    try:
        value = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""
    return str(value.get("IFIND_SDK_PATH") or "").strip() if isinstance(value, dict) else ""


def _pth_candidates() -> list[Path]:
    if platform.system() != "Windows":
        return []
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
    sdk_roots: list[Path] = []
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
        if (target / "iFinDPy.py").is_file() and (target / "ShellExport.dll").is_file():
            sdk_roots.append(target.resolve())

    # iFinD 的 Windows 版 Python 文件会在导入时通过
    # ``sysconfig.get_paths()['purelib']/iFinDPy.pth`` 寻找原生 DLL。冻结后的
    # purelib 位于安装目录的 ``_internal``，与用户真实 Python 环境无关。
    # 在可写的用户数据目录生成一个只含 SDK 路径的桥接 .pth，导入期间再临时
    # 将 purelib 指向这里，避免把 .pth 误当作 DLL 加载。
    if IS_FROZEN and sdk_roots:
        bridge = USER_DATA_ROOT / "sdk_bridge"
        try:
            bridge.mkdir(parents=True, exist_ok=True)
            (bridge / "iFinDPy.pth").write_text(
                str(sdk_roots[0]), encoding="utf-8"
            )
            os.environ[_PURELIB_ENV] = str(bridge.resolve())
        except OSError:
            # 企业终端若禁止写桥接目录，后续仍会尝试用户 Python 环境中已有的
            # iFinDPy.pth，并由正常的 SDK 可用性检查给出明确结果。
            pass
    return added


def _ifind_purelib() -> Path | None:
    configured = str(os.environ.get(_PURELIB_ENV) or "").strip()
    if configured:
        path = Path(configured).expanduser()
        if (path / "iFinDPy.pth").is_file():
            return path.resolve()
    for pth in _pth_candidates():
        try:
            if pth.is_file():
                return pth.parent.resolve()
        except OSError:
            continue
    return None


@contextmanager
def ifind_sdk_import_context():
    """让旧版 iFinD SDK 在冻结程序中读取真实的 ``iFinDPy.pth``。"""
    purelib = _ifind_purelib()
    if purelib is None:
        yield
        return

    with _IMPORT_LOCK:
        original = sysconfig.get_paths

        def patched_get_paths(*args, **kwargs):
            values = dict(original(*args, **kwargs))
            values["purelib"] = str(purelib)
            return values

        sysconfig.get_paths = patched_get_paths
        try:
            yield
        finally:
            sysconfig.get_paths = original
