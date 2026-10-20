"""Adapter contract with deterministic fake DOM and no browser credentials."""
import asyncio
import json
import pytest
from openagent.browser import BrowserConfig, BrowserManager
from openagent.browser.diagnostics import UISelectors
from openagent.models import Message
from openagent.providers.browser_ui import (
    BrowserSiteProfile, BrowserUIProvider, BrowserUIError, serialize_messages,
)


SEL = UISelectors(
    composer="#input", send_button="#submit", assistant_message=".answer",
    login_required="#login", security_challenge="#challenge",
    generation_in_progress="#busy", response_complete="#done",
)


class Locator:
    def __init__(self, page, key, n=None):
        self.page, self.key, self.n = page, key, n

    @property
    def first(self):
        return self

    def nth(self, n):
        return Locator(self.page, self.key, n)

    async def is_visible(self):
        if self.key == "#busy":
            return self.page.busy
        if self.key == "#done":
            return self.page.finished
        return self.key in self.page.visible

    async def count(self):
        return int(self.page.submitted) if self.key == ".answer" else 1

    async def inner_text(self):
        return self.page.text

    async def fill(self, text):
        self.page.filled = text

    async def click(self):
        self.page.submitted = True
        self.page.busy = True
        self.page.click_count += 1
        if self.page.behavior == "success":
            asyncio.create_task(self.page.respond())


class FakePage:
    def __init__(self, behavior="success", visible=None):
        self.behavior = behavior
        self.visible = visible if visible is not None else {"#input", "#submit"}
        self.filled = ""
        self.submitted = False
        self.busy = False
        self.finished = False
        self.text = ""
        self.closed = False
        self.click_count = 0

    def is_closed(self):
        return self.closed

    def locator(self, selector):
        return Locator(self, selector)

    async def goto(self, *_args, **_kwargs):
        pass

    async def close(self):
        self.closed = True

    async def respond(self):
        await asyncio.sleep(0.02)
        self.text = "Partial, could look finished"
        await asyncio.sleep(0.04)
        self.text = "Final verified response"
        self.busy = False
        self.finished = True


class Backend:
    def __init__(self, behavior="success", visible=None):
        self.pages = []
        self.connected = False
        self.behavior = behavior
        self.visible = visible

    async def start(self, config):
        self.connected = True

    async def new_page(self):
        p = FakePage(self.behavior, self.visible)
        self.pages.append(p)
        return p

    async def stop(self):
        self.connected = False

    def is_connected(self):
        return self.connected


def make_site(timeout=1, stable=3):
    return BrowserSiteProfile(
        provider_id="fixture", model_id="fixture/ui",
        url="http://127.0.0.1:8999/chat_fixture.html", selectors=SEL,
        timeout_s=timeout, poll_s=0.02, stable_observations=stable,
    )


@pytest.mark.asyncio
async def test_adapter_complete_and_preserve_all_roles(tmp_path):
    backend = Backend()
    manager = BrowserManager(BrowserConfig(profile_dir=tmp_path / "profile"), backend=backend)
    async with manager:
        provider = BrowserUIProvider(make_site(), manager)
        messages = [
            Message(role="system", content="keep this system message"),
            Message(role="assistant", content="a prior turn"),
            Message(role="tool", content="test failed", tool_call_id="t-1"),
            Message(role="user", content="fix the issue"),
        ]
        result = await provider.complete("fixture/ui", messages)
        assert result.content == "Final verified response"
        page = backend.pages[-1]
        assert page.click_count == 1
        assert all(x in page.filled for x in ("system", "tool", "t-1", "fix the issue"))
        assert page.closed
        assert manager.status().open_tabs == 0
        caps = await provider.capabilities("fixture/ui")
        assert caps.streaming.state == "unsupported"
        assert caps.persistent_chat.state == "unsupported"
        assert caps.native_tools.state == "unsupported"


@pytest.mark.asyncio
async def test_adapter_timeout_does_not_replay(tmp_path):
    backend = Backend(behavior="stuck")
    manager = BrowserManager(BrowserConfig(profile_dir=tmp_path / "profile"), backend=backend)
    async with manager:
        provider = BrowserUIProvider(make_site(timeout=1), manager)
        with pytest.raises(BrowserUIError, match="timeout") as error:
            await provider.complete("fixture/ui", [Message(role="user", content="hi")])
        assert error.value.code == "generation_timeout"
        assert backend.pages[-1].click_count == 1
        assert backend.pages[-1].closed


@pytest.mark.asyncio
async def test_adapter_login_failure_does_not_submit(tmp_path):
    backend = Backend(visible={"#login"})
    manager = BrowserManager(BrowserConfig(profile_dir=tmp_path / "profile"), backend=backend)
    async with manager:
        provider = BrowserUIProvider(make_site(), manager)
        with pytest.raises(BrowserUIError) as error:
            await provider.complete("fixture/ui", [Message(role="user", content="hi")])
        assert error.value.code == "login_required"
        assert backend.pages[-1].click_count == 0
        assert backend.pages[-1].closed


@pytest.mark.asyncio
async def test_adapter_incompatible_permissions_and_missing_browser(tmp_path):
    mgr = BrowserManager(BrowserConfig(profile_dir=tmp_path / "profile", allowed_origins=("https://example.com",)), backend=Backend())
    with pytest.raises(ValueError, match="allowlist"):
        BrowserUIProvider(make_site(), mgr)
    other = BrowserManager(BrowserConfig(profile_dir=tmp_path / "profile2"), backend=Backend())
    provider = BrowserUIProvider(make_site(), other)
    assert (await provider.inspect()).state == "browser_disconnected"
    with pytest.raises(BrowserUIError, match="not running"):
        await provider.complete("fixture/ui", [Message(role="user", content="hi")])


def test_serialization_and_validation():
    text = serialize_messages([Message(role="developer", content="setup"), Message(role="user", content="你好")])
    assert "你好" in text and "\\u4f60" not in text
    assert '"role":"developer"' in text
    with pytest.raises(ValueError, match="model_id"):
        BrowserSiteProfile("fixture", "wrong/name", "https://example.com", SEL)


@pytest.mark.asyncio
async def test_stale_complete_marker_cannot_complete_partial_answer(tmp_path):
    backend = Backend(behavior="stuck")
    manager = BrowserManager(BrowserConfig(profile_dir=tmp_path / "profile"), backend=backend)
    async with manager:
        provider = BrowserUIProvider(make_site(timeout=1), manager)
        # Simulate a stale complete marker left over from a previous turn.
        # Never return the stable but incomplete assistant text.
        async def new_page():
            p = FakePage("stuck")
            p.finished = True
            p.text = "Old answer"
            backend.pages.append(p)
            return p
        backend.new_page = new_page
        with pytest.raises(BrowserUIError) as error:
            await provider.complete("fixture/ui", [Message(role="user", content="new")])
        assert error.value.code == "generation_timeout"
        assert backend.pages[-1].click_count == 1


@pytest.mark.asyncio
async def test_cancelled_request_closes_tab_without_resubmission(tmp_path):
    backend = Backend(behavior="stuck")
    manager = BrowserManager(BrowserConfig(profile_dir=tmp_path / "profile"), backend=backend)
    async with manager:
        provider = BrowserUIProvider(make_site(), manager)
        task = asyncio.create_task(provider.complete("fixture/ui", [Message(role="user", content="cancel")]))
        await asyncio.sleep(0.06)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert backend.pages[-1].closed
        assert backend.pages[-1].click_count == 1
        assert manager.status().open_tabs == 0
