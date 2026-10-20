"""CLI entrypoint. Local HTTP bridge plus offline browser discovery and probes."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from dataclasses import replace

import uvicorn
from .api import create_app
from .network import select_available_port
from .providers.mock import MockProvider
from .registry import Registry
from .store import SessionStore


def _env_port() -> int:
    return int(os.environ.get("OPENAGENT_PORT", "12321"))


def _browser_settings(args: argparse.Namespace):
    from .browser import BrowserConfig, load_selection, profile_dir_for, resolve_executable

    selected = load_selection()
    browser = args.browser or selected["browser"] or "chromium"
    supplied_path = args.browser_path
    if supplied_path is not None:
        executable = resolve_executable(supplied_path, browser)
    elif args.browser is None and selected["executable_path"]:
        executable = resolve_executable(Path(selected["executable_path"]), browser)
    else:
        executable = None
    profile_dir = args.profile_dir or profile_dir_for(browser)
    return BrowserConfig(
        mode=args.mode,
        profile_dir=profile_dir,
        cdp_url=args.cdp_url,
        headless=not args.headful,
        browser=browser,
        executable_path=executable,
    )


async def _open_browser(args: argparse.Namespace) -> int:
    """One-time interactive login via the selected dedicated browser profile."""
    from .browser import BrowserManager, BrowserError
    try:
        config = _browser_settings(args)
        if config.mode != "persistent":
            raise ValueError("browser open uses persistent mode; CDP sessions are managed by the external browser")
        # Only a user-supplied site, never a provider-internal endpoint.
        config = replace(config, headless=False)
        async with BrowserManager(config) as manager:
            await manager.open_tab("interactive-login", args.url)
            print(f"Browser: {config.browser}; profile: {config.profile_dir}")
            print("Sign in normally in the opened browser. Press Enter here when finished.")
            try:
                await asyncio.to_thread(input)
            except EOFError:
                print("No interactive console; closing browser.")
            # Closing the persistent context saves the browser-managed profile.
        return 0
    except (BrowserError, ValueError) as exc:
        print(f"Browser open failed: {exc}")
        return 1


async def _probe_browser(args: argparse.Namespace) -> int:
    from .browser import BrowserConfig, BrowserManager, BrowserError
    try:
        config: BrowserConfig = _browser_settings(args)
        print(f"Browser: {config.browser}")
        print(f"Executable: {config.executable_path or ('Playwright-managed' if config.browser in ('chromium', 'firefox') else 'system channel')} ")
        if config.mode == "persistent":
            print(f"Dedicated profile: {config.profile_dir}")
        async with BrowserManager(config) as manager:
            print(f"Browser probe: {manager.status().as_dict()}")
            print("No websites were visited and no messages were submitted.")
        return 0
    except (BrowserError, ValueError) as exc:
        print(f"Browser probe failed: {exc}")
        return 1


def _discovery(args: argparse.Namespace) -> int:
    from .browser import discover_browsers
    found = discover_browsers(extra_paths=args.search_dir)
    if args.json:
        print(json.dumps([item.as_dict() for item in found], indent=2))
    elif not found:
        print("No browser executable detected. For a manually downloaded browser use --search-dir or --browser-path.")
    else:
        for item in found:
            state = "usable" if item.usable else "not supported by Playwright"
            print(f"{item.name} [{item.source}] — {state}")
            print(f"  Path: {item.executable}")
            print(f"  {item.note}")
        print("\nSelect a backend: openagent browser select chrome|msedge|chromium|firefox")
        print('Portable Chromium: openagent browser select chromium --browser-path "F:\\Browsers\\chrome-win64"')
        print("System Firefox requires a Playwright-compatible Firefox build; its existing login is not imported.")
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH") and not args.json:
        print("PLAYWRIGHT_BROWSERS_PATH is a cache ROOT containing versioned browser directories, not a chrome-win64 folder.")
    return 0


def _select_browser(args: argparse.Namespace) -> int:
    from .browser import discover_browsers, resolve_executable, save_selection, profile_dir_for
    browser = args.browser
    try:
        executable = resolve_executable(args.browser_path, browser) if args.browser_path else None
        found = discover_browsers(extra_paths=[executable] if executable else [])
        if executable is None:
            if browser == "firefox" and not any(x.id == "firefox" and x.usable for x in found):
                raise ValueError("Playwright Firefox is not installed. A normal Firefox installation is insufficient; install with: python -m playwright install firefox")
            if browser in ("chrome", "msedge") and not any(x.id == browser and x.usable for x in found):
                raise ValueError(f"{browser} not found. Supply --browser-path to its executable, or use 'browser discover'.")
        elif browser == "firefox":
            print("Warning: only Playwright's patched Firefox is supported; an ordinary firefox.exe may fail.")
        save_selection(browser, executable)
    except (ValueError, OSError) as exc:
        print(f"Browser selection failed: {exc}")
        return 1
    print(f"Selected: {browser}" + (f" ({executable})" if executable else ""))
    print(f"OpenAgent-managed profile: {profile_dir_for(browser)}")
    print("This reuses logins made inside that OpenAgent profile; it does not import an existing personal browser login.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="openagent")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="serve the local HTTP bridge")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=_env_port())
    serve.add_argument("--strict-port", action="store_true", help="disable automatic fallback for reserved/busy ports")
    serve.add_argument("--database", type=Path, default=Path.home() / ".openagent" / "sessions.db")

    browser = sub.add_parser("browser", help="browser discovery, selection and diagnostics")
    browser_sub = browser.add_subparsers(dest="browser_command", required=True)
    discover = browser_sub.add_parser("discover", help="find installed and Playwright-managed browser executables")
    discover.add_argument("--search-dir", action="append", default=[], type=Path, metavar="PATH", help="also inspect a portable browser folder (may repeat)")
    discover.add_argument("--json", action="store_true", help="machine-readable candidate list")

    select = browser_sub.add_parser("select", help="save a default browser backend for later probes/adapters")
    select.add_argument("browser", choices=["chromium", "chrome", "msedge", "firefox"])
    select.add_argument("--browser-path", type=Path, help="custom executable or extracted browser folder")

    probe = browser_sub.add_parser("probe", help="start and stop a browser; no provider access")
    probe.add_argument("--mode", choices=["persistent", "cdp"], default="persistent")
    probe.add_argument("--browser", choices=["chromium", "chrome", "msedge", "firefox"], help="override saved selection")
    probe.add_argument("--browser-path", type=Path, help="executable or extracted browser directory")
    probe.add_argument("--profile-dir", type=Path, help="dedicated reusable OpenAgent profile directory")
    probe.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    probe.add_argument("--headful", action="store_true", help="use a visible browser window")

    open_cmd = browser_sub.add_parser("open", help="open a web page in the persistent profile for manual sign-in")
    open_cmd.add_argument("--url", required=True, help="HTTPS URL to open, e.g. https://chatgpt.com")
    open_cmd.add_argument("--browser", choices=["chromium", "chrome", "msedge", "firefox"], help="override saved selection")
    open_cmd.add_argument("--browser-path", type=Path, help="custom browser executable or folder")
    open_cmd.add_argument("--profile-dir", type=Path, help="dedicated persistent OpenAgent profile")
    open_cmd.set_defaults(mode="persistent", cdp_url="http://127.0.0.1:9222", headful=True)

    args = parser.parse_args()
    if args.command == "browser":
        if args.browser_command == "discover":
            raise SystemExit(_discovery(args))
        if args.browser_command == "select":
            raise SystemExit(_select_browser(args))
        if args.browser_command == "open":
            raise SystemExit(asyncio.run(_open_browser(args)))
        raise SystemExit(asyncio.run(_probe_browser(args)))

    token = os.environ.get("OPENAGENT_API_TOKEN") or None
    if args.host not in ("127.0.0.1", "::1", "localhost") and not token:
        parser.error("non-loopback host requires OPENAGENT_API_TOKEN")
    try:
        if not 0 <= args.port <= 65535:
            parser.error("port must be between 0 and 65535")
        port = args.port if args.strict_port else select_available_port(args.host, args.port)
    except OSError as exc:
        parser.error(str(exc))
    if port != args.port:
        print(f"Port {args.port} unavailable/reserved; using {port} instead.", flush=True)

    registry = Registry()
    registry.register(MockProvider())
    store = SessionStore(args.database)
    try:
        uvicorn.run(create_app(registry, store, api_token=token), host=args.host, port=port)
    finally:
        store.close()


if __name__ == "__main__":
    main()
