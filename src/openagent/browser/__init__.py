"""Provider-neutral browser runtime. No model-specific selectors or authentication code."""

from .diagnostics import UISelectors, UIObservation, inspect_ui
from .discovery import BrowserInstallation, discover_browsers, resolve_executable
from .profiles import BrowserProfile, discover_profiles
from .firefox_session import firefox_site_cookies, SessionImportError, ALLOWED_SITES
from .selection import load_selection, save_selection, profile_dir_for
from .manager import (
    BrowserConfig,
    BrowserDisconnected,
    BrowserError,
    BrowserManager,
    BrowserNavigationError,
    BrowserSessionError,
    BrowserStartupError,
    BrowserStatus,
)

__all__ = [
    "UISelectors", "UIObservation", "inspect_ui",
    "BrowserConfig", "BrowserDisconnected", "BrowserError", "BrowserManager",
    "BrowserNavigationError", "BrowserSessionError", "BrowserStartupError",
    "BrowserStatus",
    "BrowserInstallation", "discover_browsers", "resolve_executable",
    "load_selection", "save_selection", "profile_dir_for",
    "BrowserProfile", "discover_profiles", "firefox_site_cookies",
    "SessionImportError", "ALLOWED_SITES",
]
