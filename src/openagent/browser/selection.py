"""Non-secret preference for the user's selected browser backend.

This is not an authentication store. Only a browser type and an optional binary
path are persisted; user profiles/cookies are never read or copied here.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


DEFAULT_SELECTION = Path.home() / ".openagent" / "browser.json"
VALID_BROWSERS = frozenset(("chromium", "chrome", "msedge", "firefox"))


def load_selection(file: Path = DEFAULT_SELECTION) -> dict[str, str | None]:
    try:
        data: Any = json.loads(file.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("browser") not in VALID_BROWSERS:
            raise ValueError("invalid browser selection")
        path = data.get("executable_path")
        if path is not None and not isinstance(path, str):
            raise ValueError("invalid executable path")
        return {"browser": data["browser"], "executable_path": path}
    except FileNotFoundError:
        return {"browser": "chromium", "executable_path": None}


def save_selection(browser: str, executable_path: Path | None = None, file: Path = DEFAULT_SELECTION) -> None:
    if browser not in VALID_BROWSERS:
        raise ValueError("unknown browser")
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_name(file.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump({"browser": browser, "executable_path": str(executable_path) if executable_path else None}, handle, indent=2)
            handle.write("\n")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, file)
    finally:
        tmp.unlink(missing_ok=True)


def profile_dir_for(browser: str) -> Path:
    """Preserve v0.2 Chromium profile, isolate other browser types."""
    if browser == "chromium":
        return Path.home() / ".openagent" / "browser-profile"
    return Path.home() / ".openagent" / "browser-profiles" / browser
