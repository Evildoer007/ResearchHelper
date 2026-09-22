"""Research Helper 发布版统一入口。

无参数启动桌面端；冻结 exe 的后台任务通过 ``--worker`` 回到同一程序，研究主流程
通过 ``--cli`` 调度，避免依赖安装目录中不存在的源码脚本。
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

from core.app_paths import RESOURCE_ROOT, ensure_user_directories
from core.sdk_bootstrap import configure_ifind_sdk


WORKERS = {
    "event-evidence": "event_evidence_worker.py",
    "model-catalog": "model_catalog_worker.py",
    "search-test": "search_test_worker.py",
    "product-profile": "product_profile_worker.py",
}


def _run(path: Path, arguments: list[str]) -> None:
    sys.argv = [str(path), *arguments]
    runpy.run_path(str(path), run_name="__main__")


def main() -> None:
    ensure_user_directories()
    configure_ifind_sdk()
    arguments = list(sys.argv[1:])
    if arguments[:1] == ["--doctor"]:
        from core.release_health import write_report
        print(write_report())
        return
    if len(arguments) >= 2 and arguments[0] == "--worker":
        name = arguments[1]
        if name not in WORKERS:
            raise SystemExit(f"未知后台任务：{name}")
        _run(RESOURCE_ROOT / "core" / WORKERS[name], arguments[2:])
        return
    if arguments[:1] == ["--cli"]:
        _run(RESOURCE_ROOT / "main.py", arguments[1:])
        return
    from gui.app import main as gui_main
    gui_main()


if __name__ == "__main__":
    main()
