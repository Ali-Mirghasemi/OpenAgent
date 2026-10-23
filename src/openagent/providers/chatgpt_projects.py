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
    """Bounded DOM hydration wait, including late appended rows.

    Do not stop at the very first row: additional rows may mount asynchronously.
    This detects a brief *stable snapshot*, not proven account completeness.
    """
    if not 0 <= wait_seconds <= 30:
        raise ValueError("wait_seconds must be between 0 and 30")
    if poll_seconds <= 0:
        raise ValueError("poll_seconds must be positive")
    deadline = time.monotonic() + wait_seconds
    latest: list[ChatGPTProject] = []
    stable_since = time.monotonic()
    while True:
        projects = await discover_chatgpt_projects(page, origin=origin)
        if tuple(p.id for p in projects) != tuple(p.id for p in latest):
            stable_since = time.monotonic()
        latest = projects
        now = time.monotonic()
        if now >= deadline or (latest and now - stable_since >= min(.85, wait_seconds)):
            return latest
        await asyncio.sleep(min(poll_seconds, max(0.0, deadline - now)))


@dataclass(frozen=True)
class ProjectScanReport:
    """Best-effort UI inventory, not an authoritative account-wide project list."""

    projects: tuple[ChatGPTProject, ...]
    initial_count: int
    scroll_steps: int
    expand_clicks: int
    complete: bool = False

    def as_dict(self) -> dict:
        return {
            "projects": [p.as_dict() for p in self.projects],
            "initial_count": self.initial_count,
            "discovered_count": len(self.projects),
            "scroll_steps": self.scroll_steps,
            "expand_clicks": self.expand_clicks,
            "complete": False,
            "note": "Best-effort visible UI scan; the site does not expose a verified total.",
        }


async def _expand_projects_more(page) -> bool:
    """Click only an unmistakable Projects-list expansion control in navigation.

    Never click a Project, a chat, a generic 'Show more' elsewhere, or an
    ambiguous control. This function is only called with explicit --load-all.
    """
    buttons = page.get_by_role("button", name=re.compile(
        r"^(?:show more projects|load more projects|see all projects|view all projects|more projects|"
        r"show more|load more|see all|view all)$", re.IGNORECASE,
    ))
    for index in range(min(await buttons.count(), 40)):
        button = buttons.nth(index)
        if not await button.is_visible() or not await button.is_enabled():
            continue
        safe = await button.evaluate("""element => {
            const insideNavigation = !!element.closest('aside,nav,[role="navigation"],[data-testid*="sidebar"]');
            if (!insideNavigation) return false;
            const name = (element.getAttribute('aria-label') || element.textContent || '').trim();
            if (/projects/i.test(name)) return true;
            // Generic "Show more" requires an *explicit* Projects section.
            return !!element.closest('section[data-testid*="project"],section[aria-label="Projects"],'
                + '[data-testid="projects-section"],[data-sidebar-section="projects"]');
        }""")
        if not safe:
            continue
        await button.click(timeout=2000)
        return True
    return False


async def _scroll_projects_navigation(page) -> bool:
    """Scroll the nearest bounded, scrollable navigation ancestor of a Project.

    Scrolling the page body or arbitrary chat content is deliberately forbidden.
    Returns False if no suitable sidebar scroll container exists or at its end.
    """
    return bool(await page.evaluate("""() => {
      const marker = document.querySelector('[data-sidebar-project-container-id^="project:g-p-"]')
        || document.querySelector('a[href*="/g/g-p-"]');
      if (!marker) return false;
      const navigation = marker.closest('aside,nav,[role="navigation"],[data-testid*="sidebar"]');
      if (!navigation) return false;
      let element = marker;
      while (element && element !== navigation.parentElement) {
        const available = element.scrollHeight - element.clientHeight;
        if (element.clientHeight > 20 && available > 4 &&
            element.scrollTop < available - 2) {
          const before = element.scrollTop;
          element.scrollTop = Math.min(available, before + Math.max(100, element.clientHeight * 0.8));
          return element.scrollTop > before + 1;
        }
        element = element.parentElement;
      }
      return false;
    }"""))


async def scan_chatgpt_projects(page, *, origin: str = "https://chatgpt.com",
                                wait_seconds: float = 5.0, max_steps: int = 15,
                                poll_seconds: float = 0.35) -> ProjectScanReport:
    """Explicit best-effort scan of UI-loaded Projects (no private APIs).

    Accumulate unique IDs while the sidebar scrolls/virtualizes and only expand
    labeled Project-list controls. Stops when there is no UI action to perform
    or a fixed step bound is reached. Never claims account completeness.
    """
    if not 0 <= wait_seconds <= 30:
        raise ValueError("wait_seconds must be between 0 and 30")
    if not 1 <= max_steps <= 60:
        raise ValueError("max_steps must be between 1 and 60")
    projects = await wait_for_chatgpt_projects(
        page, origin=origin, wait_seconds=wait_seconds, poll_seconds=poll_seconds,
    )
    initial_count = len(projects)
    found: dict[str, ChatGPTProject] = {p.id: p for p in projects}
    clicks = 0
    scrolls = 0
    # Origin checked again before each UI action (a SPA can navigate meanwhile).
    async def collect() -> None:
        for project in await discover_chatgpt_projects(page, origin=origin):
            if len(found) >= 1000:
                break
            found.setdefault(project.id, project)

    for _ in range(max_steps):
        try:
            if _origin(page.url) != origin:
                break
        except (ValueError, BrowserNavigationError, TypeError, AttributeError):
            break
        # Scroll first; a click can itself scroll and unmount old virtualized rows.
        moved = await _scroll_projects_navigation(page)
        if moved:
            scrolls += 1
            await asyncio.sleep(poll_seconds)
            await collect()
        expanded = await _expand_projects_more(page)
        if expanded:
            clicks += 1
            await asyncio.sleep(poll_seconds)
            await collect()
        if not expanded and not moved or len(found) >= 1000:
            break
    return ProjectScanReport(tuple(found.values()), initial_count, scrolls, clicks)
