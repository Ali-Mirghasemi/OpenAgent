"""CLI entrypoint. Local HTTP bridge plus offline browser discovery and probes."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
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


async def _wait_for_manual_confirmation() -> bool:
    """Read Enter without parking an input() thread across asyncio cancellation.

    Windows console input cannot be polled with select(). Check msvcrt.kbhit
    instead; the event loop remains free to service Playwright, and Ctrl+C
    can cancel the coroutine so the browser context gets closed cleanly.
    """
    if not sys.stdin.isatty():
        print("No interactive terminal detected; closing browser.", flush=True)
        return False

    if os.name == "nt":
        import msvcrt

        while True:
            if msvcrt.kbhit():
                key = msvcrt.getwch()
                if key in ("\r", "\n"):
                    print()
                    return True
                if key == "\x03":
                    raise asyncio.CancelledError()
                if key in ("\x00", "\xe0"):
                    # Windows extended key prefix (arrows/function keys).
                    msvcrt.getwch()
            await asyncio.sleep(0.08)
    else:
        import select

        while True:
            readable, _, _ = select.select([sys.stdin], [], [], 0)
            if readable:
                return bool(sys.stdin.readline())
            await asyncio.sleep(0.08)


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
            print("Use this browser normally. Press Enter to close, or Ctrl+C to cancel.", flush=True)
            await _wait_for_manual_confirmation()
            # Closing the persistent context retains the OpenAgent profile.
        return 0
    except asyncio.CancelledError:
        print("\nBrowser session cancelled; closing the owned browser.", flush=True)
        return 130
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
        print("Run `openagent browser profiles` to discover saved browser profiles.")
        print("Firefox site-cookie import is opt-in; login reuse is not guaranteed.")
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH") and not args.json:
        print("PLAYWRIGHT_BROWSERS_PATH is a cache ROOT containing versioned browser directories, not a chrome-win64 folder.")
    return 0


def _profiles(args: argparse.Namespace) -> int:
    """List profile folder locations without touching cookie or password stores."""
    from .browser import discover_profiles
    profiles = discover_profiles()
    if args.browser:
        profiles = [p for p in profiles if p.browser == args.browser]
    if args.json:
        print(json.dumps([p.as_dict() for p in profiles], indent=2))
        return 0
    if not profiles:
        print('No browser profiles found in standard locations.')
    for profile in profiles:
        print(f'{profile.browser}: {profile.name} [{profile.origin}]')
        print(f'  Path: {profile.path}')
        print(f'  Reuse: {profile.reuse}')
    print('Discovery does not access cookies, saved passwords, or browsing history.')
    return 0


async def _import_firefox(args: argparse.Namespace) -> int:
    """Opt-in one-site cookie transfer. Never outputs or persists raw cookie values."""
    from .browser import (BrowserManager, BrowserError, discover_profiles,
                          firefox_site_cookies, SessionImportError, ALLOWED_SITES)
    if not args.yes:
        print('No cookies read. Add --yes to explicitly authorize a site-only cookie import.')
        return 2
    source = args.profile.expanduser().resolve()
    # Never import from an arbitrary folder that merely contains a cookies.sqlite.
    listed = [p for p in discover_profiles(include_openagent=False)
              if p.browser == 'firefox' and Path(p.path) == source]
    if not listed:
        print('Source must be a Firefox profile listed by `openagent browser profiles --browser firefox`.')
        return 2
    if args.site not in ALLOWED_SITES:
        print('Site is not supported for cookie migration.')
        return 2
    try:
        config = _browser_settings(args)
        if config.mode != 'persistent':
            print('Cookie migration requires a dedicated OpenAgent persistent profile.')
            return 2
        if source == config.profile_dir.expanduser().resolve():
            print('Source and destination browser profiles must be different.')
            return 2
        cookies = firefox_site_cookies(source, args.site)
        if not cookies:
            print('No portable, unexpired cookies for that site in the selected Firefox profile.')
            return 2
        async with BrowserManager(config) as manager:
            count = await manager.install_site_cookies(args.site, cookies)
        print(f'Imported {count} {args.site} cookies into the OpenAgent {config.browser} profile.')
        print('Session authentication is NOT confirmed. Run `browser chatgpt-status --headful --details`.')
        print('Google sign-in restrictions and provider permissions are unaffected by this import.')
        return 0
    except (BrowserError, SessionImportError, ValueError, OSError) as exc:
        # Never include driver/database details, which could expose secret data.
        print(f'Cookie import failed ({type(exc).__name__}).')
        return 1


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


async def _diagnose_browser(args: argparse.Namespace) -> int:
    """Read-only UI diagnosis. Selector flags are provided by the operator."""
    from .browser import BrowserManager, BrowserError
    from .browser.diagnostics import UISelectors, inspect_ui

    try:
        selectors = UISelectors(
            composer=args.composer, send_button=args.send_button,
            assistant_message=args.assistant_message,
            login_required=args.login_selector,
            security_challenge=args.challenge_selector,
            rate_limited=args.rate_selector,
            generation_in_progress=args.busy_selector,
            response_complete=args.done_selector,
        )
        config = _browser_settings(args)
        async with BrowserManager(config) as manager:
            page = await manager.open_tab("diagnostics", args.url)
            observation = await inspect_ui(page, selectors)
            print(json.dumps(observation.as_dict(), indent=2))
            await manager.close_tab("diagnostics")
        return 0 if observation.state == "ready" else 2
    except (BrowserError, ValueError) as exc:
        print(f"Browser diagnostics failed: {exc}")
        return 1


async def _fixture_chat(args: argparse.Namespace) -> int:
    """Exercise the DOM adapter against a local, user-controlled HTTP fixture."""
    from urllib.parse import urlsplit
    from .browser import BrowserManager, BrowserError
    from .browser.diagnostics import UISelectors
    from .models import Message
    from .providers.browser_ui import BrowserSiteProfile, BrowserUIProvider, BrowserUIError

    address = urlsplit(args.url)
    if address.scheme != "http" or address.hostname not in ("127.0.0.1", "localhost", "::1"):
        print("ui-test requires a local HTTP fixture URL, never a public AI website")
        return 1
    try:
        config = _browser_settings(args)
        selectors = UISelectors(
            composer="#oa-composer", send_button="#oa-send",
            assistant_message=".oa-assistant", generation_in_progress="#oa-busy:not([hidden])",
            response_complete="#oa-done:not([hidden])",
        )
        site = BrowserSiteProfile("fixture", "fixture/ui", args.url, selectors)
        async with BrowserManager(config) as manager:
            provider = BrowserUIProvider(site, manager)
            result = await provider.complete(site.model_id, [Message(role="user", content=args.message)])
            print(result.content)
        return 0
    except (BrowserError, BrowserUIError, ValueError) as exc:
        print(f"UI fixture failed: {exc}")
        return 1


async def _chatgpt_status(args: argparse.Namespace) -> int:
    """Read-only public UI inspection; no login or prompt submission."""
    from urllib.parse import urlsplit
    from .browser import BrowserManager, BrowserError
    from .browser.surface import observe_status, inspect_surface, save_private_screenshot
    from .providers.chatgpt_web import CHATGPT_SELECTORS, CHATGPT_URL
    try:
        config = replace(_browser_settings(args), allowed_origins=("https://chatgpt.com",))
        async with BrowserManager(config) as manager:
            page = await manager.open_tab("chatgpt-ui-diagnostics", CHATGPT_URL)
            try:
                observation, controls, elapsed = await observe_status(
                    page, CHATGPT_SELECTORS, wait_seconds=args.wait_seconds,
                )
                # Report only whether navigation stayed on ChatGPT, not paths,
                # query parameters, page text or account details.
                on_chatgpt = urlsplit(page.url).hostname == "chatgpt.com"
                result = observation.as_dict()
                if args.details:
                    result.update({
                        "on_chatgpt_domain": on_chatgpt,
                        "waited_seconds": elapsed,
                        "controls": controls,
                        "surface": await inspect_surface(page),
                    })
                if args.screenshot is not None:
                    print("CAUTION: screenshot may contain private account/chat content. Review before sharing.",
                          file=sys.stderr)
                    await save_private_screenshot(page, args.screenshot)
                    result["screenshot_saved_locally"] = True
            finally:
                await manager.close_tab("chatgpt-ui-diagnostics")
            print(json.dumps(result, indent=2))
        return 0 if observation.state == "ready" else 2
    except (BrowserError, ValueError, OSError) as exc:
        # Avoid showing a browser exception that may contain private URL data.
        print(f"ChatGPT UI diagnostics failed ({type(exc).__name__}).")
        return 1



async def _chatgpt_compose_check(args: argparse.Namespace) -> int:
    """Dedicated tab preflight: harmless draft only; no Send clicks."""
    from .browser import BrowserManager, BrowserError
    from .providers.chatgpt_web import ChatGPTWebProvider
    try:
        config = replace(_browser_settings(args), allowed_origins=("https://chatgpt.com",))
        async with BrowserManager(config) as manager:
            result = await ChatGPTWebProvider(manager, timeout_s=10).check_composer_send()
        print(json.dumps(result, indent=2))
        return 0 if result["state"] == "send_ready" else 2
    except (BrowserError, ValueError, OSError) as exc:
        print(f"ChatGPT draft check failed ({type(exc).__name__}).")
        return 1


async def _chatgpt_projects(args: argparse.Namespace) -> int:
    """Find visible project links and expandable project rows without clicking."""
    from .browser import BrowserManager, BrowserError
    from .providers.chatgpt_web import CHATGPT_URL
    from .providers.chatgpt_projects import wait_for_chatgpt_projects, scan_chatgpt_projects
    try:
        config = replace(_browser_settings(args), allowed_origins=("https://chatgpt.com",))
        async with BrowserManager(config) as manager:
            page = await manager.open_tab("chatgpt-project-inventory", CHATGPT_URL)
            try:
                if args.load_all:
                    report = await scan_chatgpt_projects(
                        page, wait_seconds=args.wait_seconds, max_steps=args.max_steps,
                    )
                    projects = report.projects
                else:
                    projects = await wait_for_chatgpt_projects(page, wait_seconds=args.wait_seconds)
            finally:
                await manager.close_tab("chatgpt-project-inventory")
        if args.json:
            if args.load_all:
                print(json.dumps(report.as_dict(), indent=2))
            else:
                print(json.dumps([project.as_dict() for project in projects], indent=2))
        else:
            for project in projects:
                print(f"{project.name} [{project.id}]")
                print(f"  {project.url}")
            print(f"Discovered ChatGPT projects: {len(projects)}")
            if args.load_all:
                print(f"UI scan: {report.scroll_steps} scrolls, {report.expand_clicks} explicit expansions")
                print("Best-effort inventory only; completeness is NOT verified.")
            else:
                print("Only currently rendered sidebar links/rows are reported; use --load-all to scan more.")
            print("No Project is selected or opened.")
            if not projects:
                print("The Projects section may be collapsed, hidden, or not mounted in this layout.")
        return 0
    except (BrowserError, ValueError, OSError) as exc:
        print(f"ChatGPT project discovery failed ({type(exc).__name__}).")
        return 1

def _api_check(args: argparse.Namespace) -> int:
    """Check a loopback OpenAgent server without displaying or storing secrets."""
    from urllib.parse import urlsplit
    import httpx

    target = urlsplit(args.url)
    if target.scheme not in ("http", "https") or target.hostname not in (
        "localhost", "127.0.0.1", "::1"
    ) or target.username or target.password or target.query or target.fragment:
        print("api-check accepts only a loopback HTTP(S) OpenAgent server URL")
        return 1
    base = args.url.rstrip("/")
    try:
        health = httpx.get(f"{base}/health", timeout=5.0)
        health.raise_for_status()
        health_data = health.json()
        if health_data.get("service") != "openagent-bridge":
            print("Server responded, but it is not identified as OpenAgent Bridge")
            return 1
        print(f"OpenAgent Bridge reachable (version {health_data.get('version', 'unknown')})")

        token = os.environ.get("OPENAGENT_API_TOKEN", "")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        models = httpx.get(f"{base}/v1/models", headers=headers, timeout=5.0)
        if models.status_code == 401:
            print("API authentication failed (401). Set OPENAGENT_API_TOKEN in THIS "
                  "PowerShell terminal to match the token used when the server started.")
            print("If using Invoke-RestMethod, rebuild $headers after setting the token.")
            return 2
        models.raise_for_status()
        payload = models.json()
        model_ids = [row.get("id") for row in payload.get("data", [])
                     if isinstance(row, dict)]
        print(f"API authentication successful; models: {', '.join(str(x) for x in model_ids)}")
        return 0
    except (httpx.HTTPError, ValueError) as exc:
        # No secret or untrusted HTTP body is printed here.
        if isinstance(exc, httpx.ConnectError):
            print("Cannot connect to OpenAgent. Start 'serve' in another terminal "
                  "and use the port printed by Uvicorn.")
        else:
            print(f"OpenAgent API check failed ({type(exc).__name__})")
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(prog="openagent")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("api-check", help="test loopback server reachability and bearer authentication")
    check.add_argument("--url", default="http://127.0.0.1:12321", help="local OpenAgent HTTP server URL")

    serve = sub.add_parser("serve", help="serve the local HTTP bridge")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=_env_port())
    serve.add_argument("--strict-port", action="store_true", help="disable automatic fallback for reserved/busy ports")
    serve.add_argument("--database", type=Path, default=Path.home() / ".openagent" / "sessions.db")
    serve.add_argument("--enable-chatgpt-web", action="store_true", help="EXPERIMENTAL opt-in for ChatGPT public UI; requires a permitted session")
    serve.add_argument("--enable-chatgpt-conversations", action="store_true", help="EXPERIMENTAL session continuation using verified /c/<uuid> route; requires --enable-chatgpt-web")
    serve.add_argument("--mode", choices=["persistent", "cdp"], default="persistent")
    serve.add_argument("--browser", choices=["chromium", "chrome", "msedge", "firefox"])
    serve.add_argument("--browser-path", type=Path)
    serve.add_argument("--profile-dir", type=Path)
    serve.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    serve.add_argument("--headful", action="store_true")

    browser = sub.add_parser("browser", help="browser discovery, selection and diagnostics")
    browser_sub = browser.add_subparsers(dest="browser_command", required=True)
    discover = browser_sub.add_parser("discover", help="find installed and Playwright-managed browser executables")
    discover.add_argument("--search-dir", action="append", default=[], type=Path, metavar="PATH", help="also inspect a portable browser folder (may repeat)")
    discover.add_argument("--json", action="store_true", help="machine-readable candidate list")

    profiles = browser_sub.add_parser('profiles', help='discover saved browser profile locations; no cookie or password access')
    profiles.add_argument('--browser', choices=['firefox', 'chrome', 'msedge', 'chromium'])
    profiles.add_argument('--json', action='store_true')

    session = browser_sub.add_parser('session', help='explicit migration of a site session into an OpenAgent-managed browser')
    session_sub = session.add_subparsers(dest='session_command', required=True)
    transfer = session_sub.add_parser('import-firefox', help='copy only ChatGPT-domain Firefox cookies from one selected profile')
    transfer.add_argument('--profile', required=True, type=Path, help='Firefox profile PATH from browser profiles')
    transfer.add_argument('--site', choices=['chatgpt.com'], default='chatgpt.com')
    transfer.add_argument('--yes', action='store_true', help='required: authorize reading session cookies from the selected profile')
    transfer.add_argument('--browser', choices=['chromium', 'chrome', 'msedge', 'firefox'], help='destination Playwright engine; default saved selection')
    transfer.add_argument('--browser-path', type=Path, help='destination browser executable or directory')
    transfer.add_argument('--profile-dir', type=Path, help='destination OpenAgent persistent profile')
    transfer.set_defaults(mode='persistent', cdp_url='http://127.0.0.1:9222', headful=False)

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

    diagnose = browser_sub.add_parser("diagnose", help="read-only status of a page using explicit UI selectors")
    diagnose.add_argument("--url", required=True)
    diagnose.add_argument("--composer", default="#oa-composer", help="visible composer selector")
    diagnose.add_argument("--send-button", default="#oa-send", help="visible send button selector")
    diagnose.add_argument("--assistant-message", default=".oa-assistant")
    diagnose.add_argument("--login-selector")
    diagnose.add_argument("--challenge-selector")
    diagnose.add_argument("--rate-selector")
    diagnose.add_argument("--busy-selector")
    diagnose.add_argument("--done-selector")

    chatgpt_status = browser_sub.add_parser("chatgpt-status", help="EXPERIMENTAL read-only ChatGPT UI status, not a login proof")
    chatgpt_status.add_argument("--details", action="store_true", help="show safe selector and DOM-shape metadata (no page text or account data)")
    chatgpt_status.add_argument("--wait-seconds", type=float, default=8.0, help="wait up to 0-30 seconds for the app UI to render (default 8)")
    chatgpt_status.add_argument("--screenshot", type=Path, help="OPTIONAL local PNG: may contain private chat/account details; never shared automatically")

    compose_check = browser_sub.add_parser("chatgpt-compose-check", help="type a harmless draft and inspect Send WITHOUT submitting")
    projects_cmd = browser_sub.add_parser("chatgpt-projects", help="list visible ChatGPT project links/rows without clicking or submitting")
    projects_cmd.add_argument("--json", action="store_true", help="output visible project names and derived candidate URLs as JSON")
    projects_cmd.add_argument("--wait-seconds", type=float, default=5.0,
                              help="wait 0-30 seconds for sidebar rows to render (default 5)")
    projects_cmd.add_argument("--load-all", action="store_true",
                              help="opt-in bounded scrolling and explicitly labeled Projects-list expansion; best effort")
    projects_cmd.add_argument("--max-steps", type=int, default=15,
                              help="max 1-60 UI expansion/scroll steps with --load-all (default 15)")

    fixture = browser_sub.add_parser("ui-test", help="submit one turn ONLY to a locally hosted UI fixture")
    fixture.add_argument("--url", default="http://127.0.0.1:8999/chat_fixture.html")
    fixture.add_argument("--message", default="Hello from OpenAgent")

    for p in (diagnose, fixture, chatgpt_status, compose_check, projects_cmd):
        p.add_argument("--mode", choices=["persistent", "cdp"], default="persistent")
        p.add_argument("--browser", choices=["chromium", "chrome", "msedge", "firefox"])
        p.add_argument("--browser-path", type=Path)
        p.add_argument("--profile-dir", type=Path)
        p.add_argument("--cdp-url", default="http://127.0.0.1:9222")
        p.add_argument("--headful", action="store_true")

    args = parser.parse_args()
    if args.command == "api-check":
        raise SystemExit(_api_check(args))
    if args.command == "browser":
        if args.browser_command == "discover":
            raise SystemExit(_discovery(args))
        if args.browser_command == 'profiles':
            raise SystemExit(_profiles(args))
        if args.browser_command == 'session':
            raise SystemExit(asyncio.run(_import_firefox(args)))
        if args.browser_command == "select":
            raise SystemExit(_select_browser(args))
        if args.browser_command == "diagnose":
            raise SystemExit(asyncio.run(_diagnose_browser(args)))
        if args.browser_command == "ui-test":
            raise SystemExit(asyncio.run(_fixture_chat(args)))
        if args.browser_command == "chatgpt-status":
            raise SystemExit(asyncio.run(_chatgpt_status(args)))
        if args.browser_command == "chatgpt-compose-check":
            raise SystemExit(asyncio.run(_chatgpt_compose_check(args)))
        if args.browser_command == "chatgpt-projects":
            raise SystemExit(asyncio.run(_chatgpt_projects(args)))
        if args.browser_command == "open":
            try:
                code = asyncio.run(_open_browser(args))
            except KeyboardInterrupt:
                # First Ctrl+C cancels the main task; context cleanup runs
                # before asyncio.run returns control to this handler.
                print("\nBrowser session interrupted.", flush=True)
                code = 130
            raise SystemExit(code)
        raise SystemExit(asyncio.run(_probe_browser(args)))

    if args.enable_chatgpt_conversations and not args.enable_chatgpt_web:
        parser.error("--enable-chatgpt-conversations requires --enable-chatgpt-web")
    token = os.environ.get("OPENAGENT_API_TOKEN") or None
    # Unlike the mock, this adapter can act on a real signed-in account.
    # Require authentication even when the HTTP listener is loopback-only.
    if args.enable_chatgpt_web and not token:
        parser.error("--enable-chatgpt-web requires OPENAGENT_API_TOKEN (even on loopback)")
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
    browser_manager = None
    if args.enable_chatgpt_web:
        from .browser import BrowserManager
        from .providers.chatgpt_web import ChatGPTWebProvider
        try:
            config = replace(_browser_settings(args), allowed_origins=("https://chatgpt.com",))
            browser_manager = BrowserManager(config)
            registry.register(ChatGPTWebProvider(
                browser_manager, enable_conversation_continuation=args.enable_chatgpt_conversations,
            ))
        except (ValueError, OSError) as exc:
            parser.error(f"invalid ChatGPT browser configuration: {exc}")
        print("EXPERIMENTAL ChatGPT UI adapter: no plan/model verification, no native tools, no automatic login.", flush=True)
        print("Only use where the provider permits automated interaction; account challenges stop the request.", flush=True)
        if args.enable_chatgpt_conversations:
            print("EXPERIMENTAL: /c/<uuid> continuation is fixture-tested only; no live ChatGPT validation.", flush=True)
    store = SessionStore(args.database)
    try:
        uvicorn.run(create_app(registry, store, api_token=token, browser_manager=browser_manager),
                    host=args.host, port=port)
    finally:
        store.close()


if __name__ == "__main__":
    main()
