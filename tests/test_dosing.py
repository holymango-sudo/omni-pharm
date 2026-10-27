"""Dose / duration rules (no model data needed)."""

from __future__ import annotations

import pytest

from omnipharm.dosing import Dose, check_doses, effective_limit, limit_text
from omnipharm.knowledge import drug_groups

TYLENOL = {"unit": "정", "per": "unit", "mg": {"acetaminophen": 500}}
COLD_SYRUP = {"unit": "포", "per": "unit", "mg": {"acetaminophen": 325, "pseudoephedrine": 15, "caffeine": 25}}
CTX = {"crcl": 90, "age": 40, "sex": "M", "weight": 70, "scr": 0.9, "hepatic": False}


def run(items, **ctx):
    c = {**CTX, **ctx}
    c["all_keys"] = [k for it in items for k in it["keys"]]
    return check_doses(items, c, drug_groups, lambda k: k)


def item(name, strength, dose, keys=None):
    return {"input": name, "keys": keys or list(strength["mg"]) if strength else keys, "dose": dose, "strength": strength}


def test_daily_total_is_summed_across_products():
    r = run([item("타이레놀", TYLENOL, Dose(amount=2, per_day=4, days=3)),          # 4000 mg
             item("감기약", COLD_SYRUP, Dose(amount=1, per_day=3, days=3))])        # + 975 mg
    apap = next(x for x in r["ingredients"] if x["key"] == "acetaminophen")
    assert round(apap["daily_mg"]) == 4975 and apap["status"] == "초과"
    assert any("합계" in a["text"] and a["sev"] == "금기" for a in r["alerts"])


def test_normal_dose_is_ok():
    r = run([item("타이레놀", TYLENOL, Dose(amount=1, per_day=3, days=3))])
    assert r["ingredients"][0]["status"] == "적정" and not r["alerts"]


def test_liver_impairment_lowers_the_limit():
    r = run([item("타이레놀", TYLENOL, Dose(amount=1, per_day=6, days=2))], hepatic=True)  # 3000 > 2000
    assert r["ingredients"][0]["status"] == "초과" and "간기능 저하" in r["ingredients"][0]["why"]


def test_renal_limit_tramadol():
    assert effective_limit("tramadol", {**CTX, "crcl": 25})[0] == 200
    assert effective_limit("tramadol", {**CTX, "age": 80})[0] == 300


def test_mg_entry_for_single_ingredient_without_product():
    r = run([{"input": "tramadol", "keys": ["tramadol"], "dose": Dose(amount=100, unit="mg", per_day=4, days=2),
              "strength": None}], crcl=20)
    assert r["ingredients"][0]["status"] == "초과"


def test_dose_dependent_interaction_simvastatin_amlodipine():
    r = run([{"input": "simvastatin", "keys": ["simvastatin"], "dose": Dose(amount=40, unit="mg", per_day=1, days=30),
              "strength": None},
             {"input": "amlodipine", "keys": ["amlodipine"], "dose": None, "strength": None}])
    assert any("20mg 이하" in a["text"] and a["sev"] == "금기" for a in r["alerts"])


def test_methotrexate_daily_is_flagged_weekly_is_not():
    daily = run([{"input": "mtx", "keys": ["methotrexate"], "dose": Dose(amount=2.5, unit="mg", per_day=1, days=7),
                  "strength": None}])
    weekly = run([{"input": "mtx", "keys": ["methotrexate"],
                   "dose": Dose(amount=7.5, unit="mg", per_day=1, days=28, weekly=True), "strength": None}])
    assert any("주 1회" in a["text"] for a in daily["alerts"])
    assert not any("주 1회" in a["text"] for a in weekly["alerts"])


def test_long_term_nsaid_and_prn_exemption():
    reg = run([{"input": "ibuprofen", "keys": ["ibuprofen"], "dose": Dose(amount=400, unit="mg", per_day=3, days=30),
                "strength": None}])
    prn = run([{"input": "ibuprofen", "keys": ["ibuprofen"],
                "dose": Dose(amount=400, unit="mg", per_day=3, days=30, prn=True), "strength": None}])
    assert any("10일" in a["text"] for a in reg["alerts"])
    assert not any("10일" in a["text"] for a in prn["alerts"])


def test_regular_acetaminophen_with_warfarin_inr_warning():
    r = run([item("타이레놀", TYLENOL, Dose(amount=2, per_day=3, days=5)),
             {"input": "warfarin", "keys": ["warfarin"], "dose": None, "strength": None}])
    assert any("INR" in a["text"] for a in r["alerts"])


def test_apixaban_dose_reduction_criteria():
    r = run([{"input": "apixaban", "keys": ["apixaban"], "dose": Dose(amount=5, unit="mg", per_day=2, days=30),
              "strength": None}], age=82, weight=55)
    assert any("2.5mg" in a["text"] for a in r["alerts"])


def mg(name, amount, per_day, days=30):
    return {"input": name, "keys": [name], "dose": Dose(amount=amount, unit="mg", per_day=per_day, days=days), "strength": None}


def test_rivaroxaban_vte_loading_is_not_an_overdose():
    """15 mg twice a day for the first 21 days of VTE treatment is the labelled regimen, not '1일 최대 초과(금기)'."""
    first = run([mg("rivaroxaban", 15, 2, days=21)])
    assert first["ingredients"][0]["status"] == "주의" and not any(a["sev"] == "금기" for a in first["alerts"])
    assert any("21일" in a["text"] for a in first["alerts"])
    late = run([mg("rivaroxaban", 15, 2, days=60)])
    assert late["ingredients"][0]["status"] == "초과" and any(a["sev"] == "금기" for a in late["alerts"])
    other = run([mg("rivaroxaban", 20, 2, days=7)])  # not the loading pattern: still an overdose
    assert other["ingredients"][0]["status"] == "초과"


def test_apixaban_vte_loading_skips_the_af_reduction_rule():
    r = run([mg("apixaban", 10, 2, days=7)], age=82, weight=55)
    assert r["ingredients"][0]["status"] == "주의" and not any("2.5mg" in a["text"] for a in r["alerts"])


def test_prescription_ceiling_is_not_called_otc():
    """alprazolam / rosuvastatin are prescription medicines: '일반약 기준' was the wrong reason."""
    for name, amount in (("alprazolam", 2.5), ("rosuvastatin", 40)):
        r = run([mg(name, amount, 2 if name == "alprazolam" else 1)])
        texts = " ".join(a["text"] for a in r["alerts"])
        assert r["ingredients"][0]["status"] == "주의" and "일반약" not in texts
    otc = run([mg("ibuprofen", 400, 4, days=3)])  # 1,600 mg: above the OTC ceiling
    assert any("일반약 기준" in a["text"] for a in otc["alerts"])
    assert "일반약" not in limit_text("로수바스타틴", effective_limit("rosuvastatin", CTX), "rosuvastatin")


@pytest.mark.parametrize(("key", "ctx", "er", "expected"), [
    ("famotidine", {"crcl": 45}, False, 20),   # label renal table: CrCl 30-60 at most 20 mg/day
    ("famotidine", {"crcl": 20}, False, 10),
    ("levocetirizine", {"crcl": 20}, False, 0.72),  # 2.5 mg twice a week
    ("paroxetine", {}, False, 60),             # OCD / panic disorder
    ("paroxetine", {}, True, 75),              # CR, panic disorder
    ("paroxetine", {"crcl": 20}, True, 50),
    ("gabapentin", {"crcl": 45}, True, 1800),  # once-daily form: 600-1800 mg at CrCl 30-60
])
def test_label_limits(key, ctx, er, expected):
    assert effective_limit(key, {**CTX, **ctx}, er=er)[0] == expected


def test_gabapentin_er_not_used_below_crcl_30():
    assert effective_limit("gabapentin", {**CTX, "crcl": 25}, er=True)[0] < 0.01


@pytest.mark.parametrize(("key", "child", "er", "expected"), [
    ("venlafaxine", "A", False, 187.5),   # label: hepatic impairment 50% of the dose
    ("venlafaxine", "B", True, 112.5),
    ("bupropion", "B", True, 75),         # XL/SR: Child-Pugh 7-15 at most 150 mg every other day
    ("bupropion", "B", False, 450),       # IR: only severe cirrhosis
    ("bupropion", "C", False, 75),
    ("paroxetine", "C", False, 40),
    ("tramadol", "A", False, 100),        # cirrhosis: 50 mg every 12 h
    ("tadalafil", "B", False, 10),
    ("metoclopramide", "B", False, 20),
    ("venlafaxine", None, False, 375),    # class unknown: no hepatic step
])
def test_child_pugh_dose_steps(key, child, er, expected):
    assert effective_limit(key, {**CTX, "child": child}, er=er)[0] == expected


def test_not_recommended_in_child_pugh_c_is_not_a_dose():
    r = run([mg("tadalafil", 5, 1, days=7)], child="C", hepatic=True)
    texts = " ".join(a["text"] for a in r["alerts"])
    assert r["ingredients"][0]["status"] == "초과" and "권장되지 않는" in texts and "0.001" not in texts
    assert "서방형" not in texts  # an ordinary tablet: not the 'use the ordinary form instead' message
    line = limit_text("타다라필", effective_limit("tadalafil", {**CTX, "child": "C"}), "tadalafil")
    assert "0.001" not in line and "권장되지 않습니다" in line


def test_syrup_dosed_in_ml():
    syrup = {"unit": "mL", "per": "mL", "mg": {"acetaminophen": 32}}  # 160 mg / 5 mL
    r = run([item("시럽", syrup, Dose(amount=10, unit="mL", per_day=4, days=2))])
    assert round(r["ingredients"][0]["daily_mg"]) == 1280


def test_unknown_strength_is_reported_not_guessed():
    r = run([{"input": "복합제", "keys": ["acetaminophen", "caffeine"], "dose": Dose(amount=1, per_day=3, days=3),
              "strength": None}])
    assert r["missing"] == ["복합제"] and not r["ingredients"]
