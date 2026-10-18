from __future__ import annotations
import pytest
from openagent.api import create_app
from openagent.providers.mock import MockProvider
from openagent.registry import Registry
from openagent.store import SessionStore

@pytest.fixture
def registry():
    r = Registry()
    r.register(MockProvider())
    return r

@pytest.fixture
def store(tmp_path):
    s = SessionStore(tmp_path / "sessions.db")
    yield s
    s.close()

@pytest.fixture
def app(registry, store):
    return create_app(registry, store)
