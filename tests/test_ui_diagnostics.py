"""Read-only UI diagnostics; no real web service accounts involved."""
import pytest
from openagent.browser.diagnostics import UISelectors, inspect_ui


SELECTORS = UISelectors(
    composer="#composer", send_button="#send", assistant_message=".assistant",
    login_required="#login", security_challenge="#challenge",
    rate_limited="#rate", generation_in_progress="#busy",
)


class Locator:
    def __init__(self, page, key):
        self.page, self.key = page, key

    @property
    def first(self):
        return self

    async def is_visible(self):
        if self.page.fail:
            raise RuntimeError("private browser error")
        return self.key in self.page.visible


class Page:
    def __init__(self, *visible, closed=False, fail=False):
        self.visible = set(visible)
        self.closed = closed
        self.fail = fail

    def is_closed(self):
        return self.closed

    def locator(self, name):
        return Locator(self, name)


@pytest.mark.asyncio
@pytest.mark.parametrize("visible,expected", [
    (("#composer", "#send"), "ready"),
    (("#login", "#composer", "#send"), "login_required"),
    (("#challenge", "#login", "#composer", "#send"), "security_challenge"),
    (("#rate", "#composer", "#send"), "rate_limited"),
    (("#busy", "#composer", "#send"), "generation_in_progress"),
    (("#composer",), "ui_changed"),
])
async def test_status_priority(visible, expected):
    result = await inspect_ui(Page(*visible), SELECTORS)
    assert result.state == expected
    assert "private" not in str(result.as_dict())


@pytest.mark.asyncio
async def test_closed_page_and_selector_errors():
    assert (await inspect_ui(Page(closed=True), SELECTORS)).state == "browser_disconnected"
    result = await inspect_ui(Page(fail=True), SELECTORS)
    assert result.as_dict() == {"state": "ui_changed", "evidence": "selector_unavailable"}


def test_required_selectors():
    with pytest.raises(ValueError, match="composer"):
        UISelectors("", "#send", ".assistant")

@pytest.mark.asyncio
async def test_safe_control_visibility_snapshot():
    from openagent.browser.diagnostics import inspect_ui_controls
    result = await inspect_ui_controls(Page("#composer", "#send", "#login"), SELECTORS)
    assert result["composer"] == "visible"
    assert result["send_button"] == "visible"
    assert result["login_required"] == "visible"
    assert result["security_challenge"] == "not_visible"
    assert result["response_complete"] == "not_configured"
    assert all("#" not in value for value in result.values())
    assert set(result) == {
        "composer", "send_button", "assistant_message", "login_required",
        "security_challenge", "rate_limited", "generation_in_progress",
        "response_complete",
    }


@pytest.mark.asyncio
async def test_safe_control_snapshot_when_browser_unavailable():
    from openagent.browser.diagnostics import inspect_ui_controls
    result = await inspect_ui_controls(Page(closed=True), SELECTORS)
    assert result["composer"] == "unavailable"
    result = await inspect_ui_controls(Page(fail=True), SELECTORS)
    assert result["send_button"] == "unavailable"
