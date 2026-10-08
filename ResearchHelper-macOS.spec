# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller macOS .app配置；不包含凭证、材料、缓存或报价状态。"""

import os
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
    (str(root / "README.md"), "."),
    (str(root / "assets"), "assets"),
]
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
    # 只收集报告静态渲染使用的 Agg 后端。大型 Conda 环境常通过
    # sitecustomize 注册 IDE 后端，自动发现会把无关 GUI/科学计算包拉入应用。
    hooksconfig={"matplotlib": {"backends": ["Agg"]}},
    runtime_hooks=[],
    # akshare、pandas 以及 Conda 的第三方 hook 会探测大量“已安装但本项目未
    # 导入”的可选生态。明确排除它们，保证打包范围由项目需求决定，而不是由
    # 构建机恰好安装了什么决定。
    excludes=[
        "tkinter", "pytest", "IPython", "dask", "sphinx", "docutils",
        "nbformat", "notebook", "jupyter", "jedi", "astroid", "black",
        "pylint", "yapf", "PyQt5", "PyQt6", "PySide2", "pyarrow",
        "numba", "llvmlite", "boto3", "botocore", "tables", "sqlalchemy",
        "zmq", "fsspec", "statsmodels", "patsy",
        "tensorflow", "tensorflow_probability", "tensorboard", "keras",
        "torch", "torchvision", "torchaudio", "pytorch_lightning",
        "jax", "jaxlib", "cupy", "transformers",
        "sklearn", "xgboost", "lightgbm", "catboost",
        "shapely", "geopandas", "sympy", "cv2",
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
    upx=False,
    console=False,
    target_arch=os.environ.get("RESEARCH_HELPER_MAC_ARCH") or None,
    codesign_identity=os.environ.get("APPLE_CODESIGN_IDENTITY") or None,
    entitlements_file=str(root / "packaging" / "macos" / "entitlements.plist"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ResearchHelper",
)
app = BUNDLE(
    coll,
    name="ResearchHelper.app",
    bundle_identifier="com.researchhelper.desktop",
    info_plist={
        "CFBundleDisplayName": "Research Helper",
        "CFBundleName": "Research Helper",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "12.0",
    },
)
