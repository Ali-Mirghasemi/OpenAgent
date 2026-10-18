"""Port selection and reserved/busy binding fallback."""
import socket
import pytest
from openagent.network import select_available_port


def test_auto_port_keeps_zero():
    assert select_available_port("127.0.0.1", 0) == 0


def test_busy_port_uses_another_one():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        busy = s.getsockname()[1]
        if busy >= 65505:
            pytest.skip("OS assigned port too near upper bound")
        port = select_available_port("127.0.0.1", busy, search=30)
        assert busy < port < busy + 30


def test_bad_port_rejected():
    with pytest.raises(ValueError):
        select_available_port("127.0.0.1", 65536)
