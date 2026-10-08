"""iFinD SDK/HTTP 双通道兼容层。

Windows 保持使用官方 ``iFinDPy`` SDK；未安装 SDK（macOS 等平台）时，使用
官方 HTTP API。上层仍调用熟悉的 THS_* 方法，避免研究口径因操作系统分叉。
Access Token 只缓存在进程内，不写配置、日志或报告。
"""

from __future__ import annotations

import importlib.util
import sys
import threading
from types import SimpleNamespace
from typing import Any

import requests

from . import config


def _parts(value: str, separator: str = ";") -> list[str]:
    return [part.strip() for part in str(value or "").split(separator)]


def _function_params(value: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for part in str(value or "").split(","):
        key, marker, item = part.partition(":")
        if marker and key.strip():
            result[key.strip()] = item.strip()
    return result


def _indicator_payload(indicators: str, params: str) -> list[dict[str, Any]]:
    names = _parts(indicators)
    groups = _parts(params)
    groups += [""] * max(0, len(names) - len(groups))
    return [
        {
            "indicator": name,
            "indiparams": [item.strip() for item in groups[index].split(",")]
            if groups[index] else [],
        }
        for index, name in enumerate(names)
        if name
    ]


class IFindClient:
    """按运行环境选择SDK或HTTP，公开与iFinDPy相同的必要方法。"""

    def __init__(self) -> None:
        self._access_token = ""
        self._token_lock = threading.Lock()
        self._session = requests.Session()

    @staticmethod
    def sdk_available() -> bool:
        # 保留Windows既有SDK/测试注入路径。部分受控运行会先把iFinDPy放入
        # sys.modules，但模块没有可供find_spec读取的__spec__。
        if "iFinDPy" in sys.modules:
            return True
        try:
            return importlib.util.find_spec("iFinDPy") is not None
        except (ImportError, AttributeError, ValueError):
            return False

    def available(self) -> bool:
        return bool(
            (self.sdk_available() and config.IFIND_ACCOUNT and config.IFIND_PASSWORD)
            or config.IFIND_REFRESH_TOKEN
        )

    @staticmethod
    def _sdk():
        import iFinDPy  # type: ignore

        return iFinDPy

    def _token(self, *, refresh: bool = False) -> str:
        if not config.IFIND_REFRESH_TOKEN:
            raise RuntimeError("未配置 iFinD HTTP Refresh Token")
        with self._token_lock:
            if self._access_token and not refresh:
                return self._access_token
            response = self._session.post(
                f"{config.IFIND_API_BASE_URL}/get_access_token",
                headers={
                    "Content-Type": "application/json",
                    "refresh_token": config.IFIND_REFRESH_TOKEN,
                },
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
            token = str(((payload.get("data") or {}).get("access_token")) or "").strip()
            if not token:
                raise RuntimeError(
                    f"iFinD HTTP未返回Access Token：{payload.get('errmsg') or payload.get('message') or '未知错误'}"
                )
            self._access_token = token
            return token

    def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.sdk_available() and config.IFIND_ACCOUNT and config.IFIND_PASSWORD:
            raise RuntimeError("SDK调用不应进入HTTP请求函数")
        for attempt in range(2):
            token = self._token(refresh=attempt > 0)
            response = self._session.post(
                f"{config.IFIND_API_BASE_URL}/{endpoint}",
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "access_token": token,
                    "ifindlang": "cn",
                },
                timeout=45,
            )
            if response.status_code in {401, 403} and attempt == 0:
                self._access_token = ""
                continue
            response.raise_for_status()
            value = response.json()
            if not isinstance(value, dict):
                raise RuntimeError("iFinD HTTP返回格式无效")
            return value
        raise RuntimeError("iFinD HTTP鉴权失败")

    def THS_iFinDLogin(self, account: str = "", password: str = "") -> int:
        if self.sdk_available() and account and password:
            return int(self._sdk().THS_iFinDLogin(account, password))
        self._token()
        return 0

    def THS_iFinDLogout(self) -> int:
        if self.sdk_available() and config.IFIND_ACCOUNT and config.IFIND_PASSWORD:
            return int(self._sdk().THS_iFinDLogout())
        self._access_token = ""
        return 0

    def THS_BasicData(self, codes: str, indicators: str, params: str = "") -> dict[str, Any]:
        if self.sdk_available() and config.IFIND_ACCOUNT and config.IFIND_PASSWORD:
            return self._sdk().THS_BasicData(codes, indicators, params)
        return self._post("basic_data_service", {
            "codes": codes,
            "indipara": _indicator_payload(indicators, params),
        })

    def THS_HistoryQuotes(self, codes: str, indicators: str, params: str,
                          start: str, end: str) -> dict[str, Any]:
        if self.sdk_available() and config.IFIND_ACCOUNT and config.IFIND_PASSWORD:
            return self._sdk().THS_HistoryQuotes(codes, indicators, params, start, end)
        return self._post("cmd_history_quotation", {
            "codes": codes,
            "indicators": indicators,
            "startdate": start,
            "enddate": end,
            "functionpara": _function_params(params) or {"Fill": "Blank"},
        })

    def THS_DateSerial(self, codes: str, indicators: str, params: str,
                       global_params: str, start: str, end: str) -> dict[str, Any]:
        if self.sdk_available() and config.IFIND_ACCOUNT and config.IFIND_PASSWORD:
            return self._sdk().THS_DateSerial(
                codes, indicators, params, global_params, start, end,
            )
        return self._post("date_sequence", {
            "codes": codes,
            "startdate": start,
            "enddate": end,
            "functionpara": _function_params(global_params),
            "indipara": _indicator_payload(indicators, params),
        })

    def THS_iwencai(self, query: str, query_type: str) -> dict[str, Any]:
        if self.sdk_available() and config.IFIND_ACCOUNT and config.IFIND_PASSWORD:
            return self._sdk().THS_iwencai(query, query_type)
        return self._post("smart_stock_picking", {
            "searchstring": query,
            "searchtype": query_type,
        })

    def THS_DataPool(self, model_name: str, input_params: str,
                     output_params: str) -> dict[str, Any]:
        if self.sdk_available() and config.IFIND_ACCOUNT and config.IFIND_PASSWORD:
            return self._sdk().THS_DataPool(model_name, input_params, output_params)
        values = _parts(input_params)
        function_params: dict[str, str]
        if model_name == "index" and len(values) >= 2:
            function_params = {"date": values[0], "blockname": values[1]}
        else:
            function_params = {
                f"param{index + 1}": value for index, value in enumerate(values) if value
            }
        return self._post("data_pool", {
            "reportname": model_name,
            "functionpara": function_params,
            "outputpara": output_params,
        })

    def THS_EDB(self, indicators: str, params: str, start: str, end: str):
        if self.sdk_available() and config.IFIND_ACCOUNT and config.IFIND_PASSWORD:
            return self._sdk().THS_EDB(indicators, params, start, end)
        payload = self._post("edb_service", {
            "indicators": indicators.replace(";", ","),
            "startdate": start,
            "enddate": end,
        })
        try:
            import pandas as pd

            if isinstance(payload.get("data"), list):
                frame = pd.DataFrame(payload["data"])
            else:
                tables = payload.get("tables") or []
                table = (tables[0].get("table") or {}) if tables else {}
                frame = pd.DataFrame(table)
            return SimpleNamespace(data=frame, raw=payload)
        except Exception:
            return SimpleNamespace(data=[], raw=payload)


client = IFindClient()

