"""Provider-neutral browser runtime. No model-specific selectors or authentication code."""

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
]
