from __future__ import annotations

from types import SimpleNamespace

from core.ifind_connection_worker import check_ifind_connection


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_connection_accepts_valid_token_without_returning_access_token(monkeypatch):
    captured = {}

    def post(_url, *, headers, timeout):
        captured.update(headers)
        return _Response({"data": {"access_token": "temporary-secret"}})

    session = SimpleNamespace(trust_env=True, post=post)
    monkeypatch.setattr("core.ifind_connection_worker.requests.Session", lambda: session)

    result = check_ifind_connection({"refresh_token": "refresh-secret", "base_url": "https://example.test/api"})

    assert result["ok"]
    assert session.trust_env is False
    assert captured["refresh_token"] == "refresh-secret"
    assert "temporary-secret" not in str(result)
    assert "refresh-secret" not in str(result)


def test_connection_reports_provider_rejection_without_returning_token(monkeypatch):
    session = SimpleNamespace(
        trust_env=True,
        post=lambda *_args, **_kwargs: _Response({"errmsg": "Refresh Token 无效或已失效"}),
    )
    monkeypatch.setattr("core.ifind_connection_worker.requests.Session", lambda: session)

    result = check_ifind_connection({"refresh_token": "rejected-secret"})

    assert not result["ok"]
    assert "无效或已失效" in result["error"]
    assert "rejected-secret" not in str(result)
