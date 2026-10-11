"""Hostile / unusual input must never crash the API: it answers 200 with a result or a clear 4xx message."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from omnipharm import api
from omnipharm.config import settings

pytestmark = pytest.mark.slow
if not (settings.artifacts_dir / "v2" / "pair_metrics.json").exists():
    pytest.skip("v2 artifacts not built", allow_module_level=True)


@pytest.fixture(scope="module")
def client():
    settings.access_pin = None
    settings.online_lookup = False
    with TestClient(api.app) as c:  # runs the lifespan: loads the engine once
        yield c


WEIRD_NAMES = ["", " ", "a", "x" * 2000, "'; DROP TABLE label; --", '"warfarin" OR "aspirin"', "warfarin*",
               "NEAR(warfarin aspirin)", "💊🧪", "ワルファリン", "와파린\"", "C1=CC=CC=C1", "C(((", "+", "++warfarin",
               "warfarin+", "타이레놀+", "\x00\x01", "<script>alert(1)</script>", "../../etc/passwd", "AND", "NOT"]


@pytest.mark.parametrize("name", WEIRD_NAMES)
def test_molecule_endpoint_never_500(client, name):
    r = client.post("/api/v1/molecule", json={"name": name})
    assert r.status_code in (200, 422), (name, r.status_code, r.text[:200])


@pytest.mark.parametrize("name", WEIRD_NAMES)
def test_predict_with_weird_name_never_500(client, name):
    r = client.post("/api/v1/predict", json={"drugs": [{"name": name}, {"name": "warfarin"}, {"name": "aspirin"}]})
    assert r.status_code in (200, 422), (name, r.status_code, r.text[:200])


def test_search_weird_queries(client):
    for q in [*WEIRD_NAMES, "%", "_", "\\", "(", "[", "*"]:
        r = client.get("/api/v1/search", params={"q": q})
        assert r.status_code == 200, (q, r.text[:200])


@pytest.mark.parametrize("patient", [
    {"age": 0, "weight_kg": 2.5, "height_cm": 45},
    {"age": 120, "weight_kg": 30, "height_cm": 140, "scr": 15},
    {"age": 30, "weight_kg": 250, "height_cm": 160},
    {"age": 50, "egfr": 5},
    {"age": 50, "ast": 5000, "alt": 5000, "total_bilirubin": 40},
    {"conditions": list(api.DISEASES)},
    {"conditions": ["no_such_disease"]},
    {},
])
def test_extreme_patients(client, patient):
    r = client.post("/api/v1/predict", json={"drugs": [{"name": "warfarin"}, {"name": "amiodarone"}],
                                             "patient": patient})
    assert r.status_code in (200, 422), (patient, r.text[:300])


def test_fifteen_drugs_with_doses(client):
    names = ["warfarin", "amiodarone", "metformin", "atorvastatin", "amlodipine", "lisinopril", "omeprazole",
             "sertraline", "tramadol", "acetaminophen", "ibuprofen", "levothyroxine", "furosemide", "spironolactone",
             "clarithromycin"]
    drugs = [{"name": n, "dose": {"amount": 1, "unit": "unit", "per_day": 2, "days": 30}} for n in names]
    r = client.post("/api/v1/predict", json={"drugs": drugs, "patient": {"age": 80, "weight_kg": 50, "scr": 2}})
    assert r.status_code == 200
    assert r.json()["summary"]["verdict"] == "위험"


def test_too_many_drugs_rejected(client):
    r = client.post("/api/v1/predict", json={"drugs": [{"name": "aspirin"}] * 16})
    assert r.status_code == 422


def test_bad_dose_rejected(client):
    r = client.post("/api/v1/predict", json={"drugs": [{"name": "aspirin", "dose": {"amount": -1}},
                                                        {"name": "warfarin"}]})
    assert r.status_code == 422


def test_fhir_garbage(client):
    for body in [{}, {"resourceType": "Bundle"}, {"resourceType": "Bundle", "entry": [{"resource": None}]},
                 {"resourceType": "Bundle", "entry": [{"resource": {"resourceType": "Patient", "birthDate": "x"}}]}]:
        r = client.post("/api/v1/fhir/assess", json=body)
        assert r.status_code in (200, 422), (body, r.text[:200])


def test_oversized_body_and_security_headers(client):
    r = client.post("/api/v1/fhir/assess", content=b"{" + b" " * 2_100_000 + b"}",
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    h = client.get("/api/v1/health").headers
    assert h["X-Frame-Options"] == "DENY" and h["X-Content-Type-Options"] == "nosniff"
    assert h["Cache-Control"] == "no-store"


def test_unknown_report_id_and_routes(client):
    assert client.get("/api/v1/report/nonexistent").status_code == 404
    assert client.get("/api/v1/no_such_route").status_code == 404  # JSON 404, not the web page
    r = client.get("/../../windows/win.ini")  # path traversal: only the app shell, never a file outside dist
    assert r.status_code == 200 and "<html" in r.text.lower() and "[fonts]" not in r.text.lower()
