"""PowerShell-friendly API diagnostics must not expose bearer token values."""
from argparse import Namespace
from unittest.mock import Mock

import httpx
from openagent.cli import _api_check


def test_api_check_rejects_non_loopback_even_with_token(monkeypatch, capsys):
    monkeypatch.setenv("OPENAGENT_API_TOKEN", "super-secret")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not connect")))
    assert _api_check(Namespace(url="https://example.org")) == 1
    assert "super-secret" not in capsys.readouterr().out


def test_api_check_reports_token_mismatch_without_printing_token(monkeypatch, capsys):
    monkeypatch.setenv("OPENAGENT_API_TOKEN", "super-secret")
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/health"):
            return httpx.Response(200, json={"service": "openagent-bridge", "version": "0.4.1"}, request=httpx.Request("GET", url))
        return httpx.Response(401, json={"error": {"message": "invalid bearer token"}}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    assert _api_check(Namespace(url="http://127.0.0.1:12321")) == 2
    assert calls[1][1]["headers"] == {"Authorization": "Bearer super-secret"}
    output = capsys.readouterr().out
    assert "THIS PowerShell terminal" in output
    assert "super-secret" not in output


def test_api_check_success_without_token_when_server_unprotected(monkeypatch, capsys):
    monkeypatch.delenv("OPENAGENT_API_TOKEN", raising=False)

    def fake_get(url, **kwargs):
        if url.endswith("/health"):
            data = {"service": "openagent-bridge", "version": "0.4.1"}
        else:
            assert kwargs["headers"] == {}
            data = {"data": [{"id": "mock/echo"}]}
        return httpx.Response(200, json=data, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    assert _api_check(Namespace(url="http://localhost:12321")) == 0
    assert "mock/echo" in capsys.readouterr().out


def test_api_check_connection_failure(monkeypatch, capsys):
    def fail(url, **kwargs):
        raise httpx.ConnectError("never echo remote address or secret")
    monkeypatch.setattr(httpx, "get", fail)
    assert _api_check(Namespace(url="http://127.0.0.1:12321")) == 1
    assert "Start 'serve' in another terminal" in capsys.readouterr().out
