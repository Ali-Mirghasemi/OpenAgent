from openagent import __version__
from openagent.api import create_app
from openagent.providers.mock import MockProvider
from openagent.registry import Registry
from openagent.store import SessionStore


def test_package_version():
    assert __version__ == "0.4.20"


def test_health_version(tmp_path):
    from fastapi.testclient import TestClient

    registry = Registry()
    registry.register(MockProvider())
    store = SessionStore(tmp_path / "sessions.db")
    try:
        client = TestClient(create_app(registry, store))
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["version"] == __version__
    finally:
        store.close()
