"""Read-only browser surface summary for diagnosing missing provider controls.

No page text, title, full URL, cookies, browser storage, response bodies,
script sources, or DOM HTML are returned. Detection is intentionally limited
and cannot prove authentication or provider permission.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from .diagnostics import UISelectors, UIObservation, inspect_ui, inspect_ui_controls

# Browser DOM presence/shape only; never inspect page text or account details.
_SURFACE_SCRIPT = """() => {
  const visible = (el) => {
    const css = window.getComputedStyle(el);
    return css.display !== 'none' && css.visibility !== 'hidden'
      && el.getClientRects().length > 0;
  };
  return {
    ready_state: document.readyState,
    body_present: !!document.body,
    main_present: !!document.querySelector('main'),
    visible_editable_candidates: [...document.querySelectorAll(
      'textarea, [contenteditable="true"], [role="textbox"]'
    )].filter(visible).length,
    visible_buttons: [...document.querySelectorAll('button')].filter(visible).length,
    iframe_count: document.querySelectorAll('iframe').length,
  };
}"""


async def inspect_surface(page: Any) -> dict[str, object]:
    """Return only bounded, non-content DOM metadata, or a safe failure marker."""
    if page.is_closed():
        return {"available": False, "reason": "page_closed"}
    try:
        raw = await page.evaluate(_SURFACE_SCRIPT)
        if not isinstance(raw, dict):
            raise TypeError("unexpected result")
        return {
            "available": True,
            "ready_state": raw.get("ready_state") if raw.get("ready_state") in ("loading", "interactive", "complete") else "unknown",
            "body_present": raw.get("body_present") is True,
            "main_present": raw.get("main_present") is True,
            "visible_editable_candidates": _safe_count(raw.get("visible_editable_candidates")),
            "visible_buttons": _safe_count(raw.get("visible_buttons")),
            "iframe_count": _safe_count(raw.get("iframe_count")),
        }
    except Exception:
        return {"available": False, "reason": "surface_unavailable"}


def _safe_count(raw: object) -> int:
    return min(raw, 1000) if type(raw) is int and 0 <= raw else 0


async def observe_status(page: Any, selectors: UISelectors, *, wait_seconds: float = 8.0,
                         poll_seconds: float = 0.25) -> tuple[UIObservation, dict[str, str], float]:
    """Allow SPA hydration to finish before declaring controls missing.

    Read-only. Returns early on a visible definitive state; never submits,
    clicks, changes authentication or retries model requests.
    """
    if not 0 <= wait_seconds <= 30 or not 0.05 <= poll_seconds <= 5:
        raise ValueError("diagnostic wait interval out of range")
    start = time.monotonic()
    deadline = start + wait_seconds
    while True:
        observation = await inspect_ui(page, selectors)
        if observation.state != "ui_changed" or time.monotonic() >= deadline:
            controls = await inspect_ui_controls(page, selectors)
            return observation, controls, round(time.monotonic() - start, 2)
        await asyncio.sleep(min(poll_seconds, max(0, deadline - time.monotonic())))


async def save_private_screenshot(page: Any, path: Any) -> None:
    """Explicit opt-in: write a screenshot locally, without overwriting files.

    User must inspect this image before sharing; it can contain account content.
    """
    import os
    from pathlib import Path
    target = Path(path).expanduser()
    # Refuse ambiguous or device/file targets, and do not create directories.
    if not target.parent.is_dir() or target.suffix.lower() != ".png":
        raise ValueError("screenshot needs a .png path in an existing directory")
    try:
        binary = await page.screenshot(full_page=False, type="png")
    except Exception as exc:
        raise ValueError("screenshot capture failed") from exc
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    fd = os.open(target, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(binary)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
