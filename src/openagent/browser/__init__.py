"""Provider-neutral browser runtime. No model-specific selectors or authentication code."""

from .discovery import BrowserInstallation, discover_browsers, resolve_executable
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
    "BrowserConfig", "BrowserDisconnected", "BrowserError", "BrowserManager",
    "BrowserNavigationError", "BrowserSessionError", "BrowserStartupError",
    "BrowserStatus",
    "BrowserInstallation", "discover_browsers", "resolve_executable",
    "load_selection", "save_selection", "profile_dir_for",
]
