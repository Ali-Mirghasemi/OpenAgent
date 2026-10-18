"""Tests ownership of persistent-vs-attached browser handles without Chromium."""
import sys
import types
import pytest

from openagent.browser import BrowserConfig
from openagent.browser.playwright_backend import PlaywrightBackend


class FakePage:
    pass


class FakeContext:
    def __init__(self):
        self.closed = False
        self.browser = FakeBrowser([self])

    async def close(self):
        self.closed = True

    async def new_page(self):
        return FakePage()


class FakeBrowser:
    def __init__(self, contexts):
        self.contexts = contexts
        self.connected = True

    def is_connected(self):
        return self.connected


class FakeChromium:
    def __init__(self, context):
        self.ctx = context

    async def launch_persistent_context(self, *_args, **_kwargs):
        return self.ctx

    async def connect_over_cdp(self, *_args, **_kwargs):
        return self.ctx.browser


class FakePlaywright:
    def __init__(self, context):
        self.chromium = FakeChromium(context)
        self.stopped = False

    async def stop(self):
        self.stopped = True


class FakeStarter:
    def __init__(self, pw):
        self.pw = pw

    async def start(self):
        return self.pw


def install_fake(monkeypatch, context):
    pw = FakePlaywright(context)
    mod = types.ModuleType("playwright")
    api = types.ModuleType("playwright.async_api")
    api.async_playwright = lambda: FakeStarter(pw)
    monkeypatch.setitem(sys.modules, "playwright", mod)
    monkeypatch.setitem(sys.modules, "playwright.async_api", api)
    return pw


@pytest.mark.asyncio
async def test_persistent_mode_owns_context(tmp_path, monkeypatch):
    ctx = FakeContext()
    pw = install_fake(monkeypatch, ctx)
    b = PlaywrightBackend()
    await b.start(BrowserConfig(profile_dir=tmp_path / "openagent"))
    assert b.is_connected()
    await b.new_page()
    await b.stop()
    assert ctx.closed
    assert pw.stopped


@pytest.mark.asyncio
async def test_cdp_mode_does_not_close_existing_context(monkeypatch):
    ctx = FakeContext()
    pw = install_fake(monkeypatch, ctx)
    b = PlaywrightBackend()
    await b.start(BrowserConfig(mode="cdp"))
    await b.stop()
    assert not ctx.closed  # Browser belongs to the existing user session.
    assert pw.stopped
