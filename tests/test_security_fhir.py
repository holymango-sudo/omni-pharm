"""User accounts, audit log and FHIR parsing (no models needed)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from omnipharm import api, fhir, security
from omnipharm.config import settings


@pytest.fixture
def accounts(tmp_path, monkeypatch):
    monkeypatch.setattr(security, "USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(settings, "logs_dir", tmp_path / "logs")
    monkeypatch.setattr(settings, "access_pin", None)
    security._sessions.clear()
    security._fails.clear()
    security.set_user("dr.kim", "correct-horse-1", "clinician")
    security.set_user("admin", "admin-pass-99", "admin")
    return TestClient(api.app)


def test_password_is_hashed(accounts):
    raw = security.USERS_FILE.read_text(encoding="utf-8")
    assert "correct-horse-1" not in raw and json.loads(raw)["users"]["dr.kim"]["hash"]


def test_login_required_and_session(accounts):
    c = accounts
    assert c.get("/api/v1/diseases").status_code == 401
    assert c.post("/api/v1/login", json={"username": "dr.kim", "password": "wrong"}).status_code == 401
    tok = c.post("/api/v1/login", json={"username": "dr.kim", "password": "correct-horse-1"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert c.get("/api/v1/diseases", headers=h).status_code == 200
    assert c.get("/api/v1/me", headers=h).json() == {"user": "dr.kim", "role": "clinician"}
    assert c.get("/api/v1/audit", headers=h).status_code == 403  # clinicians cannot read the audit log
    c.post("/api/v1/logout", headers=h)
    assert c.get("/api/v1/diseases", headers=h).status_code == 401


def test_audit_log_records_logins_without_passwords(accounts):
    c = accounts
    c.post("/api/v1/login", json={"username": "dr.kim", "password": "wrong-pw"})
    tok = c.post("/api/v1/login", json={"username": "admin", "password": "admin-pass-99"}).json()["token"]
    ev = c.get("/api/v1/audit", headers={"Authorization": f"Bearer {tok}"}).json()["events"]
    kinds = [e["event"] for e in ev]
    assert "login_failed" in kinds and "login" in kinds
    assert "wrong-pw" not in json.dumps(ev)


def test_login_lockout(accounts):
    c = accounts
    for _ in range(5):
        c.post("/api/v1/login", json={"username": "dr.kim", "password": "nope"})
    assert c.post("/api/v1/login", json={"username": "dr.kim", "password": "correct-horse-1"}).status_code == 429


def test_token_in_url_only_for_the_streamed_report(accounts):
    """EventSource cannot send headers, so /report may carry ?token=; other APIs must not (URLs are logged / kept)."""
    c = accounts
    tok = c.post("/api/v1/login", json={"username": "dr.kim", "password": "correct-horse-1"}).json()["token"]
    assert c.get(f"/api/v1/diseases?token={tok}").status_code == 401
    assert c.get(f"/api/v1/report/none?token={tok}").status_code == 404  # authenticated, report unknown


def test_unknown_user_costs_a_hash(accounts, monkeypatch):
    """No timing shortcut that tells which user names exist."""
    calls = []
    real = security._hash
    monkeypatch.setattr(security, "_hash", lambda pw, salt: calls.append(1) or real(pw, salt))
    assert security.login("nobody", "whatever", "x") is None
    assert calls


def test_security_headers_on_rejections(accounts):
    c = accounts
    r = c.get("/api/v1/diseases")
    assert r.status_code == 401 and r.headers["x-frame-options"] == "DENY" and r.headers["cache-control"] == "no-store"
    chunked = c.post("/api/v1/login", content=iter([b'{"username":"a","password":"b"}']),
                     headers={"Content-Type": "application/json"})
    assert chunked.status_code == 411 and chunked.headers["x-content-type-options"] == "nosniff"


def test_reviewer_is_the_logged_in_account(accounts, tmp_path, monkeypatch):
    """With accounts a typed reviewer name cannot file (or read) answers under someone else's name."""
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    c = accounts
    tok = c.post("/api/v1/login", json={"username": "dr.kim", "password": "correct-horse-1"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    r = c.post("/api/v1/review/answer", json={"reviewer": "약사B", "case": 3, "verdict": "주의"}, headers=h)
    assert r.status_code == 200 and r.json()["reviewer"] == "drkim"
    own = c.get("/api/v1/review/answers?reviewer=약사B", headers=h).json()
    assert own["reviewer"] == "dr.kim" and "3" in own["answers"]
    assert not (tmp_path / "data" / "review" / "answers_약사B.json").exists()


def test_fhir_albumin_inr_and_cirrhosis_code():
    bundle = {"resourceType": "Bundle", "entry": [
        {"resource": {"resourceType": "Observation", "code": {"coding": [{"code": "1751-7"}]},
                      "valueQuantity": {"value": 30, "unit": "g/L"}}},
        {"resource": {"resourceType": "Observation", "code": {"coding": [{"code": "6301-6"}]},
                      "valueQuantity": {"value": 1.9}}},
        {"resource": {"resourceType": "Condition", "code": {"coding": [{"code": "K74.6"}]}}},
    ]}
    _, pt, _ = fhir.parse(bundle)
    assert pt["albumin"] == pytest.approx(3.0) and pt["inr"] == pytest.approx(1.9)
    assert {"hepatic", "cirrhosis"} <= set(pt["conditions"])


def test_fhir_si_units():
    bundle = {"resourceType": "Bundle", "entry": [
        {"resource": {"resourceType": "Observation", "code": {"coding": [{"code": "1975-2"}]},
                      "valueQuantity": {"value": 34.2, "unit": "µmol/L"}}},
        {"resource": {"resourceType": "Observation", "code": {"coding": [{"code": "8302-2"}]},
                      "valueQuantity": {"value": 65, "unit": "[in_i]"}}},
        {"resource": {"resourceType": "Observation", "code": {"coding": [{"code": "2160-0"}]},
                      "valueQuantity": {"value": 88.42, "unit": "μmol/L"}}},  # Greek mu
    ]}
    _, pt, _ = fhir.parse(bundle)
    assert pt["total_bilirubin"] == pytest.approx(2.0, abs=0.01)  # was read as 34 mg/dL -> 'Hy's law'
    assert pt["height_cm"] == pytest.approx(165.1, abs=0.1) and pt["scr"] == pytest.approx(1.0, abs=0.01)


def test_fhir_bundle_parsing():
    bundle = {"resourceType": "Bundle", "entry": [
        {"resource": {"resourceType": "Patient", "birthDate": "1950-03-01", "gender": "female",
                      "name": [{"text": "홍길동"}]}},
        {"resource": {"resourceType": "Observation", "code": {"coding": [{"system": "http://loinc.org", "code": "29463-7"}]},
                      "valueQuantity": {"value": 52, "unit": "kg"}}},
        {"resource": {"resourceType": "Observation", "code": {"coding": [{"code": "2160-0"}]},
                      "valueQuantity": {"value": 150, "unit": "umol/L"}}},
        {"resource": {"resourceType": "Condition", "code": {"coding": [{"system": "http://hl7.org/fhir/sid/icd-10",
                                                                         "code": "I48.0"}]}}},
        {"resource": {"resourceType": "MedicationRequest", "status": "active",
                      "medicationCodeableConcept": {"text": "와파린정 2mg"},
                      "dosageInstruction": [{"timing": {"repeat": {"frequency": 1, "period": 1, "periodUnit": "d"}},
                                             "doseAndRate": [{"doseQuantity": {"value": 1, "unit": "tablet"}}]}],
                      "dispenseRequest": {"expectedSupplyDuration": {"value": 30, "unit": "d"}}}},
        {"resource": {"resourceType": "MedicationRequest", "status": "stopped",
                      "medicationCodeableConcept": {"text": "aspirin"}}},
    ]}
    drugs, pt, _ = fhir.parse(bundle)
    assert [d["name"] for d in drugs] == ["와파린정 2mg"]  # stopped orders are ignored
    assert drugs[0]["dose"] == {"amount": 1.0, "unit": "unit", "per_day": 1.0, "days": 30, "prn": False, "weekly": False}
    assert pt["sex"] == "F" and pt["age"] > 70 and pt["weight_kg"] == 52 and abs(pt["scr"] - 1.696) < 0.01
    assert pt["conditions"] == ["arrhythmia"] and "name" not in pt
