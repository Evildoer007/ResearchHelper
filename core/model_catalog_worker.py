"""LLM 设置页的模型目录刷新/连接测试子进程。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import config
from core.config import no_proxy
from core.model_registry import fetch_model_catalog, test_chat_model


RESULT_PREFIX = "MODEL_CATALOG_RESULT="


def main() -> int:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        key = str(payload.get("api_key") or config.DEEPSEEK_API_KEY)
        with no_proxy():
            catalog = fetch_model_catalog(
                api_key=key,
                base_url=config.DEEPSEEK_BASE_URL,
                cache_path=config.DEEPSEEK_MODEL_CACHE,
            )
            result = catalog.payload()
            if payload.get("action") == "test" and catalog.ok:
                result["test"] = test_chat_model(
                    api_key=key,
                    base_url=config.DEEPSEEK_BASE_URL,
                    model=str(payload.get("model") or ""),
                )
    except Exception as error:  # noqa: BLE001
        result = {"ok": False, "error": f"{type(error).__name__}: {error}"}
    print(RESULT_PREFIX + json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
