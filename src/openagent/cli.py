"""CLI entrypoint: loopback only by default."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn
from .api import create_app
from .providers.mock import MockProvider
from .registry import Registry
from .store import SessionStore


def main() -> None:
    parser = argparse.ArgumentParser(prog="openagent")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="serve the local HTTP bridge")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--database", type=Path, default=Path.home() / ".openagent" / "sessions.db")
    args = parser.parse_args()

    if args.command == "serve":
        token = os.environ.get("OPENAGENT_API_TOKEN") or None
        if args.host not in ("127.0.0.1", "::1", "localhost") and not token:
            parser.error("non-loopback host requires OPENAGENT_API_TOKEN")
        registry = Registry()
        registry.register(MockProvider())
        store = SessionStore(args.database)
        uvicorn.run(create_app(registry, store, api_token=token), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
