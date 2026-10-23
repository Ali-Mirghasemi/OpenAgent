"""Read-only ChatGPT Project inventory from visible, same-origin UI elements.

A 2026 sidebar variant renders Projects as expandable *rows*, not anchors.
The row carries a ``data-sidebar-project-container-id="project:g-p-..."``
marker. We read that public DOM attribute only; we do not click rows or call
private endpoints. A derived Project URL is a candidate, not verified routing.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from ..browser.manager import _origin, BrowserNavigationError

_PROJECT_ID = re.compile(r"g-p-[a-f0-9]{32}(?:-[a-zA-Z0-9-]+)?\Z")
_ROW_SELECTOR = (
    '[role="button"][aria-expanded]:has('
    '[data-sidebar-project-container-id^="project:g-p-"]), '
    '[role="button"][aria-expanded][data-sidebar-project-container-id^="project:g-p-"]'
)
_MARKER_SELECTOR = '[data-sidebar-project-container-id^="project:g-p-"]'
_TITLE_SELECTOR = '[data-marquee-text="true"], [data-marquee-content="true"]'


@dataclass(frozen=True)
class ChatGPTProject:
    id: str
    name: str
    url: str
    source: str = "link"

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "name": self.name, "url": self.url, "source": self.source}


def parse_project_url(url: str, *, expected_origin: str = "https://chatgpt.com") -> str | None:
    """Accept only the canonical top-level Project overview route."""
    try:
        parts = urlsplit(url)
        if (_origin(url) != expected_origin or parts.query or parts.fragment or
            parts.path.count("/") != 3):
            return None
        segments = parts.path.split("/")
        if len(segments) != 4 or segments[1] != "g" or segments[3] != "project":
            return None
        identifier = segments[2]
        return identifier if _PROJECT_ID.fullmatch(identifier) else None
    except (ValueError, BrowserNavigationError, AttributeError, TypeError):
        return None


async def _row_project_name(row, identifier: str) -> str:
    """Prefer explicit UI title nodes; avoid dumping a row's nested chat list."""
    titles = row.locator(_TITLE_SELECTOR)
    for i in range(min(8, await titles.count())):
        title = titles.nth(i)
        if await title.is_visible():
            value = " ".join((await title.inner_text()).split())[:160]
            if value:
                return value
    # A project row with no visible title can still be discovered by ID.
    # Never use row.inner_text(), which could include chat names or menus.
    return identifier


async def discover_chatgpt_projects(page, *, origin: str = "https://chatgpt.com") -> list[ChatGPTProject]:
    """Read only visible links and identifiable Project sidebar rows; no clicks."""
    try:
        if _origin(page.url) != origin:
            return []
    except (ValueError, BrowserNavigationError, AttributeError, TypeError):
        return []
    projects: list[ChatGPTProject] = []
    ids: set[str] = set()

    links = page.locator('a[href*="/g/g-p-"]')
    for i in range(min(await links.count(), 128)):
        link = links.nth(i)
        if not await link.is_visible():
            continue
        href = await link.get_attribute("href")
        if not href:
            continue
        url = urljoin(origin + "/", href)
        project_id = parse_project_url(url, expected_origin=origin)
        if not project_id or project_id in ids:
            continue
        name = " ".join((await link.inner_text()).split())[:160] or project_id
        projects.append(ChatGPTProject(id=project_id, name=name, url=url))
        ids.add(project_id)

    rows = page.locator(_ROW_SELECTOR)
    for i in range(min(await rows.count(), 128)):
        row = rows.nth(i)
        if not await row.is_visible():
            continue
        if await row.get_attribute("data-sidebar-project-container-id"):
            marker = row
        else:
            markers = row.locator(_MARKER_SELECTOR)
            if not await markers.count():
                continue
            marker = markers.first
        value = await marker.get_attribute("data-sidebar-project-container-id")
        if not value or not value.startswith("project:"):
            continue
        identifier = value[len("project:"):]
        if not _PROJECT_ID.fullmatch(identifier) or identifier in ids:
            continue
        url = f"{origin}/g/{identifier}/project"
        # Derived from a DOM marker; do not assume opening the URL will work.
        projects.append(ChatGPTProject(
            id=identifier,
            name=await _row_project_name(row, identifier),
            url=url,
            source="sidebar-row",
        ))
        ids.add(identifier)
    return projects


async def wait_for_chatgpt_projects(page, *, origin: str = "https://chatgpt.com",
                                    wait_seconds: float = 5.0,
                                    poll_seconds: float = 0.4) -> list[ChatGPTProject]:
    """Bounded read-only wait for sidebar hydration; never expands a section."""
    if not 0 <= wait_seconds <= 30:
        raise ValueError("wait_seconds must be between 0 and 30")
    deadline = time.monotonic() + wait_seconds
    while True:
        projects = await discover_chatgpt_projects(page, origin=origin)
        if projects or time.monotonic() >= deadline:
            return projects
        await asyncio.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))
