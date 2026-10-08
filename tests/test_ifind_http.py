from __future__ import annotations

import sys
from types import SimpleNamespace

from core import config
from core.ifind_client import IFindClient, _indicator_payload


class _Response:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self.payload


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def post(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


def _http_client(monkeypatch, responses: list[_Response]) -> tuple[IFindClient, _Session]:
    client = IFindClient()
    session = _Session(responses)
    client._session = session
    monkeypatch.setattr(client, "sdk_available", lambda: False)
    monkeypatch.setattr(config, "IFIND_REFRESH_TOKEN", "secret-refresh-token")
    monkeypatch.setattr(config, "IFIND_API_BASE_URL", "https://quant.example/api/v1")
    return client, session


def test_indicator_payload_keeps_indicator_parameter_alignment() -> None:
    assert _indicator_payload("one;two;three", "a,b;;c") == [
        {"indicator": "one", "indiparams": ["a", "b"]},
        {"indicator": "two", "indiparams": []},
        {"indicator": "three", "indiparams": ["c"]},
    ]


def test_http_basic_data_uses_access_token_without_echoing_refresh_token(monkeypatch) -> None:
    client, session = _http_client(monkeypatch, [
        _Response({"data": {"access_token": "short-lived"}}),
        _Response({"errorcode": 0, "tables": []}),
    ])

    result = client.THS_BasicData("300033.SZ", "name;close", ";20260929")

    assert result["errorcode"] == 0
    assert session.calls[0]["headers"]["refresh_token"] == "secret-refresh-token"
    assert session.calls[1]["headers"]["access_token"] == "short-lived"
    assert "refresh_token" not in session.calls[1]["headers"]
    assert session.calls[1]["json"]["indipara"] == [
        {"indicator": "name", "indiparams": []},
        {"indicator": "close", "indiparams": ["20260929"]},
    ]


def test_http_history_uses_official_endpoint_and_default_fill(monkeypatch) -> None:
    client, session = _http_client(monkeypatch, [
        _Response({"data": {"access_token": "short-lived"}}),
        _Response({"errorcode": 0, "tables": []}),
    ])

    client.THS_HistoryQuotes("000001.SH", "close", "", "2026-01-01", "2026-02-01")

    call = session.calls[1]
    assert call["url"].endswith("/cmd_history_quotation")
    assert call["json"]["functionpara"] == {"Fill": "Blank"}


def test_http_index_pool_maps_sdk_positional_parameters(monkeypatch) -> None:
    client, session = _http_client(monkeypatch, [
        _Response({"data": {"access_token": "short-lived"}}),
        _Response({"errorcode": 0, "tables": []}),
    ])

    client.THS_DataPool("index", "2026-09-29;950125", "thscode:Y,weight:Y")

    assert session.calls[1]["json"]["functionpara"] == {
        "date": "2026-09-29",
        "blockname": "950125",
    }


def test_windows_sdk_path_remains_preferred_when_ifindpy_is_present(monkeypatch) -> None:
    calls: list[tuple] = []
    sdk = SimpleNamespace(
        THS_BasicData=lambda *args: calls.append(args) or {"errorcode": 0, "tables": []},
    )
    monkeypatch.setitem(sys.modules, "iFinDPy", sdk)
    monkeypatch.setattr(config, "IFIND_ACCOUNT", "windows-account")
    monkeypatch.setattr(config, "IFIND_PASSWORD", "windows-password")
    monkeypatch.setattr(config, "IFIND_REFRESH_TOKEN", "mac-http-token")
    client = IFindClient()

    result = client.THS_BasicData("300033.SZ", "name", "")

    assert result["errorcode"] == 0
    assert calls == [("300033.SZ", "name", "")]
