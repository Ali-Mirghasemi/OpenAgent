"""Read-only Project page validation with a real browser and synthetic HTML."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from openagent.browser import BrowserConfig, BrowserManager
from openagent.providers.chatgpt_projects import (
    ChatGPTProject, verify_chatgpt_project_destination, parse_project_url,
)
from test_chatgpt_project_rows import OriginPage
from test_ui_real_dom import LocalContentBackend, find_chromium_executable

PROJECT_ID = 'g-p-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-demo'
ORIGIN = 'http://127.0.0.1:8999'
URL = f'{ORIGIN}/g/{PROJECT_ID}/project'
PROJECT = ChatGPTProject(PROJECT_ID, 'OpenAgent', URL)


@pytest.mark.parametrize('url,ok', [
    (URL, True),
    (URL + '?token=abc', False),
    (ORIGIN + '/g/g-p-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-demo/project', True),
    (ORIGIN + '/c/ab73adc5-d6d0-4016-bf80-de9f69023411', False),
    ('https://evil.example/g/' + PROJECT_ID + '/project', False),
])
def test_canonical_project_path_parser(url, ok):
    assert (parse_project_url(url, expected_origin=ORIGIN) is not None) is ok


@pytest.mark.asyncio
async def test_invalid_project_identity_rejected_before_page_interaction():
    fake = SimpleNamespace(url=URL)
    for project in [ChatGPTProject('bad', 'OpenAgent', URL),
                    ChatGPTProject(PROJECT_ID, PROJECT_ID, URL),
                    ChatGPTProject(PROJECT_ID, 'OpenAgent', URL + '?unsafe=yes')]:
        with pytest.raises(ValueError):
            await verify_chatgpt_project_destination(fake, project, origin=ORIGIN)


@pytest.mark.asyncio
@pytest.mark.parametrize('scenario,expected', [
    ('matching', 'project_heading_matches_route'),
    ('sidebar_only', 'project_heading_not_verified'),
    ('wrong_title', 'project_heading_not_verified'),
    ('redirect', 'route_mismatch'),
    ('login', 'login_or_challenge_visible'),
    ('delayed', 'project_heading_matches_route'),
])
async def test_project_destination_with_real_dom(scenario, expected, tmp_path):
    binary = find_chromium_executable()
    if not binary:
        pytest.skip('No Chromium test executable')
    pytest.importorskip('playwright')
    headings = {
        'matching': '<main><h1>OpenAgent</h1></main>',
        'sidebar_only': '<main><h1>Other Project</h1></main>',
        'wrong_title': '<main><h1>OpenAgent Chat</h1></main>',
        'redirect': '<main><h1>OpenAgent</h1></main>',
        'login': '<main><h1>OpenAgent</h1></main><button>Log in</button>',
        'delayed': '<main id="content"></main><script>setTimeout(() => document.querySelector("#content").innerHTML = "<h1>OpenAgent</h1>", 140)</script>',
    }
    html = ('<!doctype html><html><body><nav><span>OpenAgent</span></nav>'
            + headings[scenario] + '</body></html>')
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/scenario),
                             backend=LocalContentBackend(binary, html))
    async with browser:
        wrapped = await browser.open_tab('test', URL)
        url = URL if scenario != 'redirect' else ORIGIN + '/g/g-p-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-demo/project'
        page = OriginPage(wrapped, url)
        result = await verify_chatgpt_project_destination(
            page, PROJECT, origin=ORIGIN, wait_seconds=.5, poll_seconds=.05)
        assert result.evidence == expected
        assert result.state == ('verified' if scenario in ('matching', 'delayed') else 'unverified')
        assert result.as_dict()['submitted'] is False
        assert result.as_dict()['selected_for_api'] is False
        await browser.close_tab('test')
