"""Diagnostic controls use only safe DOM metadata, and never submit a turn."""
import asyncio
from pathlib import Path

import pytest

from openagent.browser.diagnostics import UISelectors
from openagent.browser.surface import inspect_surface, observe_status, save_private_screenshot


SELECTORS = UISelectors("#composer", "#send", ".answer", login_required="#login")


class Locator:
    def __init__(self, page, selector):
        self.page, self.selector = page, selector

    @property
    def first(self):
        return self

    async def is_visible(self):
        self.page.reads += 1
        return self.selector in self.page.visible


class Page:
    def __init__(self):
        self.visible = set()
        self.closed = False
        self.reads = 0
        self.evaluations = 0

    def is_closed(self):
        return self.closed

    def locator(self, selector):
        return Locator(self, selector)

    async def evaluate(self, script):
        self.evaluations += 1
        assert "innerText" not in script
        assert "cookie" not in script
        assert "localStorage" not in script
        return {
            "ready_state": "complete", "body_present": True,
            "main_present": True, "visible_editable_candidates": 2,
            "visible_buttons": 4, "iframe_count": 0,
            "PRIVATE_CONTENT": "must not be returned",
        }

    async def screenshot(self, **kwargs):
        assert kwargs == {"full_page": False, "type": "png"}
        return b"testpng"


@pytest.mark.asyncio
async def test_wait_for_hydration_before_reporting_ui_changed():
    page = Page()

    async def finish_render():
        await asyncio.sleep(0.04)
        page.visible.update(("#composer", "#send"))

    task = asyncio.create_task(finish_render())
    observation, controls, waited = await observe_status(
        page, SELECTORS, wait_seconds=0.3, poll_seconds=0.05,
    )
    await task
    assert observation.state == "ready"
    assert controls["composer"] == "visible"
    assert waited >= 0.03


@pytest.mark.asyncio
async def test_unavailable_ui_stays_uncertain():
    page = Page()
    obs, controls, _ = await observe_status(page, SELECTORS, wait_seconds=0.055, poll_seconds=0.05)
    assert obs.state == "ui_changed"
    assert controls["composer"] == "not_visible"


@pytest.mark.asyncio
async def test_login_marker_is_reported_without_clicking():
    page = Page()
    page.visible.add("#login")
    obs, _, waited = await observe_status(page, SELECTORS, wait_seconds=0.4)
    assert obs.state == "login_required"
    assert waited < 0.4
    assert page.evaluations == 0


@pytest.mark.asyncio
async def test_safe_surface_summary_only_allowlisted_fields():
    result = await inspect_surface(Page())
    assert result == {
        "available": True, "ready_state": "complete", "body_present": True,
        "main_present": True, "visible_editable_candidates": 2,
        "visible_buttons": 4, "iframe_count": 0,
    }
    assert "PRIVATE_CONTENT" not in str(result)


@pytest.mark.asyncio
async def test_closed_or_malformed_surface_is_safe():
    page = Page()
    page.closed = True
    assert (await inspect_surface(page)) == {"available": False, "reason": "page_closed"}
    page.closed = False

    async def bad_evaluate(_script):
        raise RuntimeError("PRIVATE_ACCOUNT_DETAILS")

    page.evaluate = bad_evaluate
    result = await inspect_surface(page)
    assert result == {"available": False, "reason": "surface_unavailable"}


@pytest.mark.asyncio
async def test_screenshot_explicit_path_and_no_overwrite(tmp_path):
    page = Page()
    target = tmp_path / "surface.png"
    await save_private_screenshot(page, target)
    assert target.read_bytes() == b"testpng"
    with pytest.raises(FileExistsError):
        await save_private_screenshot(page, target)
    with pytest.raises(ValueError):
        await save_private_screenshot(page, tmp_path / "missing" / "surface.png")
    with pytest.raises(ValueError):
        await save_private_screenshot(page, tmp_path / "surface.html")


@pytest.mark.asyncio
async def test_invalid_diagnostic_wait():
    with pytest.raises(ValueError, match="wait"):
        await observe_status(Page(), SELECTORS, wait_seconds=31)
    with pytest.raises(ValueError, match="wait"):
        await observe_status(Page(), SELECTORS, wait_seconds=-1)

@pytest.mark.asyncio
async def test_surface_contract_real_chromium(tmp_path):
    from openagent.browser import BrowserConfig, BrowserManager
    from test_ui_real_dom import LocalContentBackend, find_chromium_executable

    executable = find_chromium_executable()
    if not executable:
        pytest.skip("Optional local Chromium fixture unavailable")
    try:
        import playwright  # noqa: F401
    except ImportError:
        pytest.skip("Optional Playwright dependency unavailable")

    fixture = """<!doctype html><html><body><main><textarea>PRIVATE MESSAGE</textarea>
        <button>Send</button><iframe></iframe></main></body></html>"""
    backend = LocalContentBackend(executable, fixture)
    manager = BrowserManager(BrowserConfig(profile_dir=tmp_path / "test-profile"), backend=backend)
    async with manager:
        page = await manager.open_tab("fixture", "http://127.0.0.1:8999/page.html")
        info = await inspect_surface(page)
        assert info["available"] is True
        assert info["body_present"] and info["main_present"]
        assert info["visible_editable_candidates"] == 1
        assert info["visible_buttons"] == 1
        assert "PRIVATE MESSAGE" not in str(info)
