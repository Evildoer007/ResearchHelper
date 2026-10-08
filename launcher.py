"""Research Helper 发布版统一入口。

无参数启动桌面端；冻结 exe 的后台任务通过 ``--worker`` 回到同一程序，研究主流程
通过 ``--cli`` 调度，避免依赖安装目录中不存在的源码脚本。
"""

from __future__ import annotations

import runpy
import sys
import traceback
from datetime import datetime
from pathlib import Path

from core.app_paths import OUTPUT_DIR, RESOURCE_ROOT, ensure_user_directories
from core.sdk_bootstrap import configure_ifind_sdk


WORKERS = {
    "event-evidence": "event_evidence_worker.py",
    "model-catalog": "model_catalog_worker.py",
    "search-test": "search_test_worker.py",
    "product-profile": "product_profile_worker.py",
}


def _run(path: Path, arguments: list[str]) -> None:
    sys.argv = [str(path), *arguments]
    try:
        runpy.run_path(str(path), run_name="__main__")
    except BaseException:
        # windowed PyInstaller 没有控制台；后台入口若在用户机器失败，必须留下
        # 可直接排查的完整堆栈，不能只剩一个短暂弹窗。
        try:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            crash_log = OUTPUT_DIR / "launcher-crash.log"
            with crash_log.open("a", encoding="utf-8") as handle:
                handle.write(f"\n[{datetime.now().isoformat(timespec='seconds')}] {path}\n")
                handle.write(traceback.format_exc())
        except OSError:
            pass
        raise


def main() -> None:
    ensure_user_directories()
    configure_ifind_sdk()
    arguments = list(sys.argv[1:])
    if arguments[:1] == ["--smoke-imports"]:
        # 发布构建使用：只验证冻结包能加载桌面端最关键的 Qt/业务模块，
        # 不创建窗口、不读取客户任务，也不发起任何外部请求。
        from PySide6.QtCore import qVersion
        from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
        from PySide6.QtWidgets import QApplication  # noqa: F401

        import gui.app  # noqa: F401
        import render.charts  # noqa: F401

        print(f"Research Helper smoke imports OK (Qt {qVersion()})")
        return
    if arguments[:1] == ["--smoke-dispatch"]:
        # 发布构建必须验证真实的两类子进程路径。仅检查主窗口能启动无法发现
        # ``runtime_source/main.py`` 这类只有开始研究后才出现的路径串线。
        from core.process_launcher import child_environment, external_worker_path

        internal_root = Path(child_environment()["RESEARCH_HELPER_RESOURCE_ROOT"])
        internal_required = [
            internal_root / "main.py",
            internal_root / "core" / WORKERS["event-evidence"],
        ]
        external_required = [
            external_worker_path("optionhelper_recommender_worker.py"),
            external_worker_path("optionhelper_quote_watchdog.py"),
        ]
        missing = [str(path) for path in (*internal_required, *external_required)
                   if not path.is_file()]
        if missing:
            raise SystemExit("发布版子进程路径缺失：" + "；".join(missing))
        print("Research Helper subprocess dispatch smoke OK")
        return
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
