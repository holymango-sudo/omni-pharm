"""Dose / duration rules (no model data needed)."""

from __future__ import annotations

from omnipharm.dosing import Dose, check_doses, effective_limit
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


def test_syrup_dosed_in_ml():
    syrup = {"unit": "mL", "per": "mL", "mg": {"acetaminophen": 32}}  # 160 mg / 5 mL
    r = run([item("시럽", syrup, Dose(amount=10, unit="mL", per_day=4, days=2))])
    assert round(r["ingredients"][0]["daily_mg"]) == 1280


def test_unknown_strength_is_reported_not_guessed():
    r = run([{"input": "복합제", "keys": ["acetaminophen", "caffeine"], "dose": Dose(amount=1, per_day=3, days=3),
              "strength": None}])
    assert r["missing"] == ["복합제"] and not r["ingredients"]
