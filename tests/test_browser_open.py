"""Interruptible CLI manual browser handling, without a real browser or sign-in."""
import asyncio
from argparse import Namespace
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from openagent import cli
from openagent.browser import BrowserConfig


class FakeKeys:
    def __init__(self, sequence):
        self.sequence = list(sequence)

    def kbhit(self):
        return bool(self.sequence)

    def getwch(self):
        return self.sequence.pop(0)


@pytest.mark.asyncio
async def test_windows_enter_polls_without_input_thread(monkeypatch):
    monkeypatch.setattr(cli, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(cli, "sys", SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: True)))
    monkeypatch.setitem(sys.modules, "msvcrt", FakeKeys(["x", "\r"]))
    assert await cli._wait_for_manual_confirmation() is True


@pytest.mark.asyncio
async def test_windows_ctrl_c_key_cancels_without_input_thread(monkeypatch):
    monkeypatch.setattr(cli, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(cli, "sys", SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: True)))
    monkeypatch.setitem(sys.modules, "msvcrt", FakeKeys(["\x03"]))
    with pytest.raises(asyncio.CancelledError):
        await cli._wait_for_manual_confirmation()


@pytest.mark.asyncio
async def test_browser_open_cancellation_closes_owned_browser(monkeypatch, tmp_path, capsys):
    import openagent.browser as browser
    status = {"closed": False, "url": None}

    class FakeManager:
        def __init__(self, config):
            self.config = config

        async def __aenter__(self):
            return self

        async def open_tab(self, tab_id, url):
            status["url"] = url

        async def __aexit__(self, *_):
            status["closed"] = True

    async def cancelled():
        raise asyncio.CancelledError()

    monkeypatch.setattr(browser, "BrowserManager", FakeManager)
    monkeypatch.setattr(cli, "_browser_settings",
                        lambda args: BrowserConfig(profile_dir=tmp_path / "profile"))
    monkeypatch.setattr(cli, "_wait_for_manual_confirmation", cancelled)
    assert await cli._open_browser(Namespace(url="https://example.com")) == 130
    assert status == {"closed": True, "url": "https://example.com"}
    assert "cancelled" in capsys.readouterr().out.lower()
