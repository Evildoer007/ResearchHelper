# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir 配置；不打包任何本机凭证、材料、缓存或正式报价状态。"""

from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH)


def py_tree(folder: str, destination: str):
    return [
        (str(path), str(Path(destination) / path.parent.relative_to(root / folder)))
        for path in (root / folder).rglob("*.py")
        if "__pycache__" not in path.parts
    ]


datas = [
    (str(root / "main.py"), "."),
    (str(root / "THESIS_LIBRARY.md"), "."),
    (str(root / "sector_groups.json"), "."),
    (str(root / "underlying_map.json"), "."),
    (str(root / "VERSION"), "."),
    (str(root / "assets"), "assets"),
]
# 冻结主进程用 RESOURCE_ROOT/core/*.py 调度 worker；独立 OptionHelper Python 使用
# runtime_source。两份都是不含凭证/数据的兼容源码，只用于跨解释器进程边界。
datas += py_tree("core", "core")
datas += py_tree("core", "runtime_source/core")
datas += py_tree("llm", "runtime_source/llm")

hiddenimports = []
for package in ("core", "gui", "llm", "render"):
    hiddenimports += collect_submodules(package)
hiddenimports += ["matplotlib.backends.backend_agg", "PySide6.QtWebEngineWidgets"]

a = Analysis(
    [str(root / "launcher.py")],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Anaconda 中 akshare/pandas 的可选发现链会把整套 Notebook、文档构建和代码
    # 格式化工具误判为运行依赖；这些模块不参与 Research Helper 的任何交付路径。
    excludes=[
        "tkinter", "pytest", "IPython", "dask", "sphinx", "docutils",
        "nbformat", "notebook", "jupyter", "jedi", "astroid", "black",
        "pylint", "yapf", "PyQt5", "PyQt6", "PySide2", "pyarrow",
        "numba", "llvmlite", "boto3", "botocore", "tables", "sqlalchemy",
        "zmq", "fsspec", "statsmodels", "patsy",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ResearchHelper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="ResearchHelper",
)
