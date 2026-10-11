"""Patient-state rules: CKD-EPI 2021, KDIGO staging, liver categories, feature flags."""

from __future__ import annotations

import pytest

from omnipharm.clinical import Patient, assess, ckd_epi_2021, kdigo_stage
from omnipharm.knowledge import disease_ids, normalize_ingredient
from omnipharm.models.patient import FEATURES, PATIENT_DIM, patient_vector


@pytest.mark.parametrize(("scr", "age", "sex", "expected"), [
    (1.0, 50, "M", 91.7),  # hand-computed from the published CKD-EPI 2021 equation
    (1.0, 50, "F", 68.6),
    (2.0, 70, "M", 35.2),
])
def test_ckd_epi_2021_reference_values(scr, age, sex, expected):
    assert ckd_epi_2021(scr, age, sex) == pytest.approx(expected, abs=0.6)


def test_kdigo_stages():
    assert kdigo_stage(95)[0] == "G1"
    assert kdigo_stage(59.9)[0] == "G3a"
    assert kdigo_stage(29)[0] == "G4"
    assert kdigo_stage(10)[0] == "G5"


def test_renal_impairment_switches_on_model_flag_and_alert():
    a = assess(Patient(age=70, sex="M", scr=2.5))
    assert a["kidney"]["impaired"] and a["flags"]["renal"]
    assert any("콩팥" in x for x in a["alerts"])


def test_weight_changes_drug_clearance():
    """Cockcroft-Gault depends on body weight: a lighter patient clears renally excreted drugs more slowly."""
    light = assess(Patient(age=70, sex="F", weight_kg=45, height_cm=155, scr=1.0))
    heavy = assess(Patient(age=70, sex="F", weight_kg=70, height_cm=155, scr=1.0))
    assert light["kidney"]["crcl"] < heavy["kidney"]["crcl"]
    assert light["kidney"]["egfr"] == heavy["kidney"]["egfr"]  # eGFR is normalised to 1.73 m2


def test_obesity_uses_adjusted_body_weight():
    b = assess(Patient(age=50, sex="M", weight_kg=130, height_cm=170, scr=1.0))["body"]
    assert b["bmi_cat"] == "고도비만" and b["dosing_weight_kind"].startswith("보정") and b["dosing_weight"] < 130


def test_capacity_drops_with_age_and_organs():
    young = assess(Patient(age=30, sex="M", weight_kg=70, height_cm=175, scr=0.9, ast=20, alt=20))
    old = assess(Patient(age=85, sex="F", weight_kg=42, height_cm=150, scr=1.6, ast=150, alt=160,
                         total_bilirubin=2.5, conditions=["heart_failure"]))
    assert young["capacity"]["level"] == "좋음" and old["capacity"]["score"] < 40


def test_disease_text_and_chips():
    a = assess(Patient(conditions=["glaucoma"], diagnosis="간질환, 전립선비대"))
    assert {"glaucoma", "hepatic", "bph"} <= set(a["diseases"]) and "epilepsy" not in a["diseases"]


def test_direct_egfr_overrides_creatinine():
    a = assess(Patient(age=40, sex="F", scr=5.0, egfr=95))
    assert a["kidney"]["stage"] == "G1" and not a["flags"]["renal"]


def test_hys_law_range_is_severe_liver():
    a = assess(Patient(ast=200, alt=250, total_bilirubin=3.0))
    assert a["liver"]["severity"] == 3 and a["flags"]["hepatic"]


def test_normal_patient_has_no_flags():
    a = assess(Patient(age=35, sex="M", scr=0.9, ast=20, alt=20, total_bilirubin=0.6))
    assert not any(a["flags"].values()) and not a["alerts"]


def test_korean_diagnosis_keywords():
    ids = disease_ids([], "간경변, 심방세동, 당뇨")
    assert {"hepatic", "arrhythmia", "diabetes"} <= ids and "renal" not in ids


def test_patient_vector_layout():
    v = patient_vector(80, "F", None, {"renal": True}, 3)
    assert v.shape == (PATIENT_DIM,)
    assert v[FEATURES.index("age")] == pytest.approx(0.8)
    assert v[FEATURES.index("wt_missing")] == 1
    assert v[FEATURES.index("renal")] == 1 and v[FEATURES.index("hepatic")] == 0
    assert v[FEATURES.index("sex_f")] == 1


@pytest.mark.parametrize(("raw", "expected"), [
    ("WARFARIN SODIUM", "warfarin"),
    ("clopidogrel bisulfate", "clopidogrel"),
    ("Divalproex Sodium", "valproate"),
    ("paracetamol", "acetaminophen"),
    ("unknownumab", None),
])
def test_normalize_ingredient(raw, expected):
    known = frozenset({"warfarin", "clopidogrel", "valproate", "acetaminophen"})
    assert normalize_ingredient(raw, known) == expected
