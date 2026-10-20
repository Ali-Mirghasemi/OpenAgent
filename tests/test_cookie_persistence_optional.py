"""Optional real-browser test: only synthetic cookies, disposable profile, no network."""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from openagent.browser import BrowserConfig, BrowserManager
from test_ui_real_dom import find_chromium_executable


@pytest.mark.asyncio
async def test_site_cookie_survives_managed_chromium_restart(tmp_path):
    path = find_chromium_executable()
    if not path:
        pytest.skip('No suitable local Chromium executable')
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        pytest.skip('Playwright optional dependency unavailable')

    profile = tmp_path / 'disposable-no-account'
    conf = BrowserConfig(profile_dir=profile, executable_path=Path(path), headless=True)
    async with BrowserManager(conf) as manager:
        count = await manager.install_site_cookies('chatgpt.com', [{
            'name': 'openagent_fixture_cookie', 'value': 'synthetic',
            'domain': '.chatgpt.com', 'path': '/', 'expires': time.time() + 300,
            'secure': True, 'httpOnly': True, 'sameSite': 'Lax',
        }])
        assert count == 1

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            str(profile), executable_path=path, headless=True,
        )
        try:
            cookies = await context.cookies('https://chatgpt.com')
            assert any(c['name'] == 'openagent_fixture_cookie' and c['value'] == 'synthetic'
                       for c in cookies)
        finally:
            await context.close()


@pytest.mark.asyncio
async def test_portable_far_future_expiry_real_chromium(tmp_path):
    """Optional test of the exact Firefox expiry error observed on Windows."""
    path = find_chromium_executable()
    if not path:
        pytest.skip('No local Chromium executable')
    from openagent.browser.firefox_session import _portable_expiry, _MAX_PLAYWRIGHT_EXPIRY
    conf = BrowserConfig(profile_dir=tmp_path / 'disposable-large-expiry',
                         executable_path=Path(path), headless=True)
    async with BrowserManager(conf) as manager:
        await manager.install_site_cookies('chatgpt.com', [{
            'name': 'openagent_large_expiry_fixture', 'value': 'synthetic',
            'domain': '.chatgpt.com', 'path': '/',
            'expires': _portable_expiry(2**63 - 1, time.time()),
            'secure': True, 'httpOnly': True, 'sameSite': 'Lax'
        }])
