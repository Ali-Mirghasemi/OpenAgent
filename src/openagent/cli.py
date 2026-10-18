"""CLI entrypoint. Loopback HTTP bridge and optional independent browser probe."""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

import uvicorn
from .api import create_app
from .network import select_available_port
from .providers.mock import MockProvider
from .registry import Registry
from .store import SessionStore


def _env_port() -> int:
    return int(os.environ.get("OPENAGENT_PORT", "12321"))


async def _probe_browser(args: argparse.Namespace) -> int:
    from .browser import BrowserConfig, BrowserManager, BrowserError

    try:
        config = BrowserConfig(
            mode=args.mode, profile_dir=args.profile_dir,
            cdp_url=args.cdp_url, headless=not args.headful,
        )
        async with BrowserManager(config) as manager:
            print(f"Browser probe: {manager.status().as_dict()}")
            print("No websites were visited and no messages were submitted.")
        return 0
    except (BrowserError, ValueError) as exc:
        print(f"Browser probe failed: {exc}")
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(prog="openagent")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="serve the local HTTP bridge")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=_env_port())
    serve.add_argument("--strict-port", action="store_true", help="disable automatic fallback for reserved/busy ports")
    serve.add_argument("--database", type=Path, default=Path.home() / ".openagent" / "sessions.db")

    browser = sub.add_parser("browser", help="browser transport diagnostics")
    browser_sub = browser.add_subparsers(dest="browser_command", required=True)
    probe = browser_sub.add_parser("probe", help="start and stop a browser; no provider access")
    probe.add_argument("--mode", choices=["persistent", "cdp"], default="persistent")
    probe.add_argument("--profile-dir", type=Path, default=Path.home() / ".openagent" / "browser-profile")
    probe.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    probe.add_argument("--headful", action="store_true", help="use a visible browser window")
    args = parser.parse_args()

    if args.command == "browser":
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
