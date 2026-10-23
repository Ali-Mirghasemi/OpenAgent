"""ChatGPT experimental readiness, send controls, and read-only project inventory."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from openagent.browser import BrowserConfig, BrowserManager
from openagent.models import Message
from openagent.providers.browser_ui import BrowserUIError
from openagent.providers.chatgpt_web import CHATGPT_MODEL, CHATGPT_SELECTORS, ChatGPTWebProvider
from openagent.providers.chatgpt_projects import discover_chatgpt_projects, parse_project_url, wait_for_chatgpt_projects
from test_ui_real_dom import LocalContentBackend, find_chromium_executable


EXAMPLE_PROJECT = 'g-p-6a8010ed0dbc8191bb1a04fd5c4ec0c1-demo'


@pytest.mark.parametrize('url,valid', [
    (f'https://chatgpt.com/g/{EXAMPLE_PROJECT}/project', True),
    (f'https://chatgpt.com/g/{EXAMPLE_PROJECT}/project?share=1', False),
    (f'https://chatgpt.com/g/{EXAMPLE_PROJECT}/project#fragment', False),
    (f'https://chatgpt.com/g/{EXAMPLE_PROJECT}/c/aabbcc', False),
    (f'https://evil.example/g/{EXAMPLE_PROJECT}/project', False),
    (f'https://chatgpt.com/g/{EXAMPLE_PROJECT}/project/more', False),
    ('https://chatgpt.com/g/g-p-../project', False),
    ('https://chatgpt.com/g/g-p-short/project', False),
])
def test_project_url_validator(url,valid):
    assert (parse_project_url(url) == EXAMPLE_PROJECT) is valid


class Link:
    def __init__(self, href, name, visible=True):
        self.href, self.name, self.visible = href, name, visible

    async def is_visible(self): return self.visible
    async def get_attribute(self, name): return self.href if name == 'href' else None
    async def inner_text(self): return self.name


class LinkSet:
    def __init__(self, links): self.links = links
    async def count(self): return len(self.links)
    def nth(self, index): return self.links[index]


class ProjectPage:
    def __init__(self, links, url='https://chatgpt.com/'):
        self.url = url
        self.links = links
        self.clicks = 0

    def locator(self, selector):
        if selector == 'a[href*="/g/g-p-"]':
            return LinkSet(self.links)
        if '[role="button"]' in selector:
            return LinkSet([])
        raise AssertionError(selector)


@pytest.mark.asyncio
async def test_project_discovery_only_visible_links_no_navigation_or_click():
    page = ProjectPage([
        Link(f'/g/{EXAMPLE_PROJECT}/project', 'My project'),
        Link(f'/g/{EXAMPLE_PROJECT}/project', 'duplicate'),
        Link(f'/g/g-p-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-other/project', 'Hidden', False),
        Link('https://malicious.test/g/'+EXAMPLE_PROJECT+'/project', 'malicious'),
        Link('/g/g-p-abc/project', 'invalid'),
    ])
    projects = await discover_chatgpt_projects(page)
    assert len(projects) == 1
    assert projects[0].id == EXAMPLE_PROJECT
    assert projects[0].name == 'My project'
    assert projects[0].url.startswith('https://chatgpt.com/g/')
    assert page.clicks == 0
    assert await discover_chatgpt_projects(ProjectPage(page.links, 'https://example.com/')) == []


@pytest.mark.asyncio
async def test_chromium_submit_id_variant_and_draft_check(tmp_path):
    executable = find_chromium_executable()
    if not executable:
        pytest.skip('Chromium executable unavailable')
    pytest.importorskip('playwright')
    html = (Path(__file__).parents[1] / 'examples' / 'chatgpt_submit_variant_fixture.html').read_text('utf-8')
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'profile'),
                             backend=LocalContentBackend(executable, html))
    provider = ChatGPTWebProvider(browser, timeout_s=3, poll_s=.04,
                                  stable_observations=3,
                                  url='http://127.0.0.1:8999/submit-variant.html')
    async with browser:
        preflight = await provider.check_composer_send()
        assert preflight == {'state': 'send_ready', 'submitted': False}
        answer = await provider.complete(CHATGPT_MODEL, [Message(role='user', content='Hello')])
        assert answer.content == 'Response from composer-submit variant'
        assert browser.status().open_tabs == 0


@pytest.mark.asyncio
async def test_chromium_rejects_other_form_submit(tmp_path):
    executable = find_chromium_executable()
    if not executable:
        pytest.skip('Chromium executable unavailable')
    pytest.importorskip('playwright')
    html = (Path(__file__).parents[1] / 'examples' / 'chatgpt_missing_send_fixture.html').read_text('utf-8')
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'profile'),
                             backend=LocalContentBackend(executable, html))
    provider = ChatGPTWebProvider(browser, timeout_s=1, poll_s=.04,
                                  url='http://127.0.0.1:8999/missing-send.html')
    async with browser:
        assert await provider.check_composer_send() == {'state': 'submit_unavailable', 'submitted': False}
        with pytest.raises(BrowserUIError) as err:
            await provider.complete(CHATGPT_MODEL, [Message(role='user', content='Hello')])
        assert err.value.code == 'submit_unavailable'
        assert browser.status().open_tabs == 0

@pytest.mark.asyncio
async def test_chromium_ambiguous_submit_controls_fail_closed(tmp_path):
    executable = find_chromium_executable()
    if not executable:
        pytest.skip('Chromium executable unavailable')
    pytest.importorskip('playwright')
    html = (Path(__file__).parents[1] / 'examples' / 'chatgpt_ambiguous_submit_fixture.html').read_text('utf-8')
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'profile'),
                             backend=LocalContentBackend(executable, html))
    provider = ChatGPTWebProvider(browser, timeout_s=1, poll_s=.04,
                                  url='http://127.0.0.1:8999/ambiguous.html')
    async with browser:
        assert await provider.check_composer_send() == {'state': 'submit_unavailable', 'submitted': False}
        assert browser.status().open_tabs == 0
