"""API behaviour that does not need the models: access PIN, Korean validation errors, health."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from omnipharm import api
from omnipharm.config import settings


@pytest.fixture
def client():
    return TestClient(api.app)  # no `with`: lifespan (model loading) is not run


def test_health_is_open(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200 and r.json()["version"]


def test_one_version_everywhere():
    """The package, the API and pyproject.toml must name the same release (the package said 0.1.0 until v2.21.1)."""
    import tomllib

    import omnipharm
    from omnipharm.config import PROJECT_ROOT, VERSION

    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert omnipharm.__version__ == VERSION == project["version"]


def test_pin_required_when_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "access_pin", "4321")
    assert client.get("/api/v1/diseases").status_code == 401
    assert client.get("/api/v1/diseases", headers={"X-Access-PIN": "0000"}).status_code == 401
    assert client.get("/api/v1/diseases", headers={"X-Access-PIN": "4321"}).status_code == 200
    assert client.get("/api/v1/health").status_code == 200  # monitoring stays open


def test_no_pin_by_default(client, monkeypatch):
    monkeypatch.setattr(settings, "access_pin", None)
    assert client.get("/api/v1/diseases").status_code == 200


def test_validation_error_is_korean(client, monkeypatch):
    monkeypatch.setattr(settings, "access_pin", None)
    r = client.post("/api/v1/assess", json={"age": 300})
    assert r.status_code == 422 and "나이" in r.json()["detail"]["message"]


def test_assess_endpoint(client, monkeypatch):
    monkeypatch.setattr(settings, "access_pin", None)
    r = client.post("/api/v1/assess", json={"age": 80, "sex": "F", "weight_kg": 45, "height_cm": 150, "scr": 1.4})
    j = r.json()
    assert r.status_code == 200 and j["kidney"]["crcl"] < 30 and j["capacity"]["level"] in ("저하", "매우 저하")
