"""HL7 FHIR R4 input for EMR / OCS integration.

POST /api/v1/fhir/assess with a FHIR Bundle (or a plain list of resources) containing:
  Patient                    birthDate, gender
  Observation (LOINC)        29463-7 body weight, 8302-2 body height, 2160-0 creatinine, 33914-3 / 62238-1 / 98979-8
                             eGFR, 1920-8 AST, 1742-6 ALT, 1975-2 total bilirubin
  Condition                  ICD-10 / KCD codes (or text) -> OMNI-PHARM disease ids
  MedicationRequest /        medicationCodeableConcept (text / coding display) or a contained Medication;
  MedicationStatement        dosageInstruction / dosage: doseQuantity (value + unit), timing.repeat frequency/period,
                             asNeededBoolean, dispenseRequest.expectedSupplyDuration (days)
Only clinical values are read; names and identifiers in the bundle are ignored and never stored.
"""

from __future__ import annotations

import re
from datetime import date

LOINC = {"29463-7": "weight_kg", "3141-9": "weight_kg", "8302-2": "height_cm", "2160-0": "scr", "38483-4": "scr",
         "33914-3": "egfr", "62238-1": "egfr", "98979-8": "egfr", "48642-3": "egfr", "48643-1": "egfr",
         "1920-8": "ast", "1742-6": "alt", "1975-2": "total_bilirubin"}
# ICD-10 / KCD prefixes -> disease ids of knowledge.DISEASES
ICD = [("N18", "renal"), ("N19", "renal"), ("K70", "hepatic"), ("K71", "hepatic"), ("K72", "hepatic"),
       ("K73", "hepatic"), ("K74", "hepatic"), ("K75", "hepatic"), ("K76", "hepatic"), ("B18", "hepatic"),
       ("I50", "heart_failure"), ("I47", "arrhythmia"), ("I48", "arrhythmia"), ("I49", "arrhythmia"),
       ("I45.8", "arrhythmia"), ("I20", "cardiac"), ("I21", "cardiac"), ("I22", "cardiac"), ("I24", "cardiac"),
       ("I25", "cardiac"), ("I10", "hypertension"), ("I11", "hypertension"), ("I12", "hypertension"),
       ("I13", "hypertension"), ("I15", "hypertension"), ("E10", "diabetes"), ("E11", "diabetes"), ("E13", "diabetes"),
       ("E14", "diabetes"), ("K25", "peptic_ulcer"), ("K26", "peptic_ulcer"), ("K27", "peptic_ulcer"),
       ("K28", "peptic_ulcer"), ("K92.2", "peptic_ulcer"), ("D66", "bleeding"), ("D67", "bleeding"), ("D68", "bleeding"),
       ("D69", "bleeding"), ("J44", "asthma"), ("J45", "asthma"), ("H40", "glaucoma"), ("N40", "bph"),
       ("G40", "epilepsy"), ("F00", "dementia"), ("F01", "dementia"), ("F02", "dementia"), ("F03", "dementia"),
       ("G30", "dementia"), ("G20", "parkinson"), ("Z33", "pregnancy"), ("O", "pregnancy"), ("E05", "hyperthyroid"),
       ("M10", "gout"), ("G70", "myasthenia"), ("K85", "pancreatitis"), ("K86.1", "pancreatitis"),
       ("A15", "infection"), ("A16", "infection"), ("A17", "infection"), ("A18", "infection"), ("A19", "infection"),
       ("A41", "infection"), ("E31.2", "thyroid_cancer")]
FREQ_PER_DAY = {"d": 1, "h": 24, "wk": 1 / 7, "min": 1440}


def _resources(data: dict | list) -> list[dict]:
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if data.get("resourceType") == "Bundle":
        return [e.get("resource") or {} for e in data.get("entry", [])]
    return [data]


def _codes(cc: dict | None) -> list[str]:
    return [c.get("code", "") for c in (cc or {}).get("coding", []) if c.get("code")]


def _med_name(res: dict, contained: dict[str, dict]) -> str | None:
    cc = res.get("medicationCodeableConcept")
    ref = (res.get("medicationReference") or {}).get("reference", "")
    if not cc and ref:
        med = contained.get(ref.lstrip("#")) or contained.get(ref.split("/")[-1])
        cc = (med or {}).get("code")
    if not cc:
        return None
    return cc.get("text") or next((c.get("display") for c in cc.get("coding", []) if c.get("display")), None)


def _dose(res: dict) -> dict | None:
    di = (res.get("dosageInstruction") or res.get("dosage") or [None])[0]
    if not di:
        return None
    q = None
    for dr in di.get("doseAndRate", []):
        q = dr.get("doseQuantity") or q
    if not q or q.get("value") is None:
        return None
    unit = (q.get("unit") or q.get("code") or "").lower()
    unit = ("mg" if unit in ("mg", "milligram") else "g" if unit in ("g", "gram") else
            "mL" if unit in ("ml", "milliliter") else "unit")
    rep = (di.get("timing") or {}).get("repeat") or {}
    per_day = 1.0
    if rep.get("frequency") and rep.get("periodUnit") in FREQ_PER_DAY:
        per_day = rep["frequency"] / (rep.get("period") or 1) * FREQ_PER_DAY[rep["periodUnit"]]
    weekly = rep.get("periodUnit") == "wk" and rep.get("period", 1) == 1 and rep.get("frequency", 1) <= 1
    days = None
    sup = ((res.get("dispenseRequest") or {}).get("expectedSupplyDuration") or {})
    if sup.get("value"):
        days = int(sup["value"] * (7 if sup.get("unit", "d").startswith("w") else 1))
    bound = (rep.get("boundsDuration") or {})
    if not days and bound.get("value"):
        days = int(bound["value"] * (7 if bound.get("unit", "d").startswith("w") else 1))
    return {"amount": float(q["value"]), "unit": unit, "per_day": 1 if weekly else max(min(per_day, 24), 0.01),
            "days": days, "prn": bool(di.get("asNeededBoolean")), "weekly": weekly}


def parse(data: dict | list) -> tuple[list[dict], dict, list[str]]:
    """FHIR -> (drugs [{"name", "dose"}], patient dict, warnings)."""
    res = _resources(data)
    contained = {}
    for r in res:
        for c in r.get("contained", []):
            contained[c.get("id", "")] = c
        if r.get("resourceType") == "Medication":
            contained[r.get("id", "")] = r
    patient: dict = {"conditions": []}
    drugs, warn = [], []
    for r in res:
        t = r.get("resourceType")
        if t == "Patient":
            if r.get("birthDate"):
                b = date.fromisoformat(r["birthDate"][:10] if len(r["birthDate"]) >= 10 else r["birthDate"][:4] + "-07-01")
                patient["age"] = round((date.today() - b).days / 365.25, 1)
            g = (r.get("gender") or "").lower()
            if g in ("male", "female"):
                patient["sex"] = "M" if g == "male" else "F"
        elif t == "Observation" and (r.get("status") or "final") not in ("entered-in-error", "cancelled"):
            field = next((LOINC[c] for c in _codes(r.get("code")) if c in LOINC), None)
            v = (r.get("valueQuantity") or {})
            if field and v.get("value") is not None:
                val, unit = float(v["value"]), (v.get("unit") or v.get("code") or "").lower()
                if field == "weight_kg" and unit in ("lb", "[lb_av]"):
                    val *= 0.45359237
                if field == "height_cm" and unit in ("m",):
                    val *= 100
                if field == "scr" and unit in ("umol/l", "µmol/l"):
                    val /= 88.42
                patient[field] = round(val, 3)
        elif t == "Condition":
            cs = (r.get("clinicalStatus") or {}).get("coding", [{}])
            if cs and cs[0].get("code") in ("resolved", "inactive", "remission"):
                continue
            codes = [c.replace(".", "") for c in _codes(r.get("code"))]
            hit = [d for c in codes for p, d in ICD if c.upper().startswith(p.replace(".", ""))]
            if hit:
                patient["conditions"] += hit
            elif (r.get("code") or {}).get("text"):
                patient["diagnosis"] = ((patient.get("diagnosis") or "") + " " + r["code"]["text"]).strip()
        elif t in ("MedicationRequest", "MedicationStatement"):
            if r.get("status") in ("stopped", "cancelled", "entered-in-error", "completed", "not-taken"):
                continue
            name = _med_name(r, contained)
            if not name:
                warn.append(f"{t}: 약 이름(medicationCodeableConcept.text/coding.display)이 없어 건너뜀")
                continue
            name = re.sub(r"\s+", " ", name).strip()[:200]
            drugs.append({"name": name, "dose": _dose(r)})
    patient["conditions"] = sorted(set(patient["conditions"]))
    return drugs, patient, warn


def detected_issues(res: dict) -> list[dict]:
    """Result -> FHIR DetectedIssue resources (for an EMR / CDS display)."""
    sev = {"높음": "high", "주의": "moderate", "낮음": "low"}
    out = []
    for p in res.get("pairs", []):
        if p["level"] == "낮음":
            continue
        detail = "식약처 DUR 병용금기" if p.get("dur") else (p["label_evidence"][0]["section"] if p["label_evidence"]
                                                       else (p.get("mechanism") or [{}])[0].get("text", "AI 신호"))
        out.append({"resourceType": "DetectedIssue", "status": "final", "severity": sev[p["level"]],
                    "code": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "DRG",
                                         "display": "Drug Interaction Alert"}]},
                    "detail": f"{p['a']} + {p['b']}: {detail}"})
    for d in res.get("disease_alerts", []):
        out.append({"resourceType": "DetectedIssue", "status": "final",
                    "severity": "high" if d["severity"] == "금기" else "moderate",
                    "code": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "DSEASE",
                                         "display": "Drug and Disease Interaction Alert"}]},
                    "detail": f"{d['disease']} + {d['drug']}: {d['reason']}"})
    for a in (res.get("dosing") or {}).get("alerts", []):
        out.append({"resourceType": "DetectedIssue", "status": "final",
                    "severity": "high" if a["sev"] == "금기" else "moderate",
                    "code": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "DOSEH",
                                         "display": "High Dose Alert"}]}, "detail": a["text"]})
    return out
