"""Offline browser lifecycle tests. No browser install or credentials required."""
import pytest

from openagent.browser import (
    BrowserConfig, BrowserDisconnected, BrowserManager, BrowserNavigationError,
    BrowserSessionError, BrowserStartupError,
)


class FakePage:
    def __init__(self, *, fail_nav=False):
        self.closed = False
        self.fail_nav = fail_nav
        self.url = None

    async def goto(self, url, *, wait_until, timeout):
        if self.fail_nav:
            raise RuntimeError("private URL or token should never surface")
        self.url = url
        assert wait_until == "domcontentloaded"
        assert timeout >= 100

    async def close(self):
        self.closed = True

    def is_closed(self):
        return self.closed


class FakeBackend:
    def __init__(self, *, fail_start=False, fail_nav=False):
        self.connected = False
        self.fail_start = fail_start
        self.fail_nav = fail_nav
        self.started = 0
        self.stopped = 0
        self.pages = []

    async def start(self, config):
        self.started += 1
        if self.fail_start:
            raise RuntimeError("sensitive driver diagnostics")
        self.connected = True

    async def new_page(self):
        page = FakePage(fail_nav=self.fail_nav)
        self.pages.append(page)
        return page

    def is_connected(self):
        return self.connected

    async def stop(self):
        self.stopped += 1
        self.connected = False


@pytest.mark.asyncio
async def test_start_tab_close_stop(tmp_path):
    b = FakeBackend()
    mgr = BrowserManager(BrowserConfig(profile_dir=tmp_path / "openagent", max_tabs=1), backend=b)
    assert mgr.status().state == "stopped"
    await mgr.start()
    await mgr.start()
    assert b.started == 1
    tab = await mgr.open_tab("request-1", "https://chatgpt.com/")
    assert mgr.tab("request-1") is tab
    assert mgr.status().open_tabs == 1
    with pytest.raises(BrowserSessionError, match="already allocated"):
        await mgr.open_tab("request-1", "https://chatgpt.com/")
    with pytest.raises(BrowserSessionError, match="limit"):
        await mgr.open_tab("request-2", "https://chatgpt.com/")
    await mgr.close_tab("request-1")
    assert tab.closed
    assert mgr.status().open_tabs == 0
    await mgr.stop()
    assert mgr.status().state == "stopped"
    assert b.stopped == 1


@pytest.mark.asyncio
async def test_disconnection_does_not_automatically_retry(tmp_path):
    b = FakeBackend()
    mgr = BrowserManager(BrowserConfig(profile_dir=tmp_path / "agent"), backend=b)
    await mgr.start()
    await mgr.open_tab("request", "https://example.org/")
    b.connected = False
    assert mgr.status().state == "disconnected"
    with pytest.raises(BrowserDisconnected):
        await mgr.open_tab("new-request", "https://example.org/")
    assert b.started == 1  # No implicit reconnect/replay.
    await mgr.start()  # Explicit recovery requested by caller.
    assert b.started == 2
    assert mgr.status().open_tabs == 0
    await mgr.stop()


@pytest.mark.asyncio
async def test_startup_and_navigation_errors_redacted(tmp_path):
    b = FakeBackend(fail_start=True)
    mgr = BrowserManager(BrowserConfig(profile_dir=tmp_path / "agent"), backend=b)
    with pytest.raises(BrowserStartupError) as exc:
        await mgr.start()
    assert "sensitive" not in str(exc.value)
    assert mgr.status().state == "failed"
    assert b.stopped == 1

    b2 = FakeBackend(fail_nav=True)
    mgr2 = BrowserManager(BrowserConfig(profile_dir=tmp_path / "agent2"), backend=b2)
    await mgr2.start()
    with pytest.raises(BrowserNavigationError) as exc:
        await mgr2.open_tab("request", "https://example.org/")
    assert "private URL" not in str(exc.value)
    assert mgr2.status().open_tabs == 0
    assert b2.pages[0].closed
    await mgr2.stop()


@pytest.mark.asyncio
async def test_reject_invalid_navigation_and_provider_origin(tmp_path):
    mgr = BrowserManager(
        BrowserConfig(profile_dir=tmp_path / "agent", allowed_origins=("https://chatgpt.com",)),
        backend=FakeBackend(),
    )
    await mgr.start()
    for url in ("file:///tmp/secret", "javascript:alert(1)", "http://example.org/", "https://wrong.site/", "https://user:pass@chatgpt.com/"):
        with pytest.raises(BrowserNavigationError):
            await mgr.open_tab("x", url)
    assert mgr.status().open_tabs == 0
    await mgr.stop()


def test_bad_settings_rejected(tmp_path):
    with pytest.raises(ValueError, match="CDP endpoint"):
        BrowserConfig(mode="cdp", cdp_url="http://1.2.3.4:9222")
    with pytest.raises(ValueError, match="dedicated"):
        BrowserConfig(profile_dir=tmp_path / "Default")
    with pytest.raises(ValueError, match="dedicated"):
        BrowserConfig(profile_dir=tmp_path / "Google" / "Chrome" / "User Data")
    with pytest.raises(ValueError, match="max_tabs"):
        BrowserConfig(profile_dir=tmp_path / "agent", max_tabs=0)
    with pytest.raises(ValueError, match="allowed_origins"):
        BrowserConfig(allowed_origins=("https://chatgpt.com/path",))


@pytest.mark.asyncio
async def test_context_manager_finally_cleans_up(tmp_path):
    b = FakeBackend()
    with pytest.raises(ValueError):
        async with BrowserManager(BrowserConfig(profile_dir=tmp_path / "agent"), backend=b) as mgr:
            await mgr.open_tab("x", "https://example.org/")
            raise ValueError("client failed")
    assert b.stopped == 1
    assert b.pages[0].closed
