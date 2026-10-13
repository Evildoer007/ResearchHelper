"""iFinD Refresh Token 连接测试子进程。

凭证只经本机 stdin 传入，响应中的 Access Token 只用于判断成功，不输出、不落盘。
"""

from __future__ import annotations

import json
import sys

import requests


RESULT_PREFIX = "IFIND_CONNECTION_RESULT="


def check_ifind_connection(settings: dict) -> dict:
    token = str(settings.get("refresh_token") or "").strip()
    base_url = str(
        settings.get("base_url") or "https://quantapi.51ifind.com/api/v1"
    ).strip().rstrip("/")
    if not token:
        return {"ok": False, "error": "未配置 iFinD Refresh Token"}
    if not base_url.startswith(("https://", "http://")):
        return {"ok": False, "error": "iFinD HTTP API 地址无效"}
    session = requests.Session()
    # iFinD 国内接口不应被本机全局代理转发；同时避免把认证头交给代理服务。
    session.trust_env = False
    try:
        response = session.post(
            f"{base_url}/get_access_token",
            headers={"Content-Type": "application/json", "refresh_token": token},
            timeout=25,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as error:
        return {"ok": False, "error": f"无法连接 iFinD：{type(error).__name__}"}
    except (TypeError, ValueError):
        return {"ok": False, "error": "iFinD 返回格式无效"}
    access_token = str(((payload.get("data") or {}).get("access_token")) or "").strip()
    if not access_token:
        message = str(payload.get("errmsg") or payload.get("message") or "Refresh Token 未通过验证")
        if token:
            message = message.replace(token, "[redacted]")
        return {"ok": False, "error": message[:200]}
    return {"ok": True, "message": "Refresh Token 有效，iFinD 已返回临时 Access Token。"}


def main() -> int:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        settings = payload if isinstance(payload, dict) else {}
        result = check_ifind_connection(settings)
    except Exception as error:  # noqa: BLE001 - 始终返回脱敏结构化状态
        result = {"ok": False, "error": f"连接测试异常：{type(error).__name__}"}
    print(RESULT_PREFIX + json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
