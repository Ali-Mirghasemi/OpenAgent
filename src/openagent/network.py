"""Pick a local listening port before invoking Uvicorn.

A probe is not a reservation: another program might take the port between the
probe and Uvicorn's bind. An actual bind error still needs a clear report.
"""
from __future__ import annotations

import socket


def select_available_port(host: str, preferred: int, *, search: int = 30) -> int:
    if not 0 <= preferred <= 65535:
        raise ValueError("port must be between 0 and 65535")
    if not 1 <= search <= 1000:
        raise ValueError("search must be between 1 and 1000")
    # Port zero asks the OS to choose an ephemeral port. Uvicorn supports 0,
    # and reports the actual listening address in its startup message.
    if preferred == 0:
        return 0
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    for port in range(preferred, min(preferred + search, 65536)):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as sock:
                sock.bind((host, port))
            return port
        except OSError:
            continue
    raise OSError(f"no permitted free TCP port in {preferred}..{min(preferred + search - 1, 65535)}")
