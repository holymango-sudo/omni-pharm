"""Clinical sanity suite: well-known combinations must get the verdict a pharmacist would give.

These are regression tests for the decision rules (not for model accuracy). Expected verdicts follow
standard references (FDA labelling, Lexicomp/Micromedex-style severity, Beers 2023):
  위험 = avoid / major (contraindicated, dose change required, or duplicated ingredient)
  주의 = moderate (monitor), 비교적 안전 = no meaningful signal
"""

from __future__ import annotations

import pytest

from omnipharm.clinical import Patient
from omnipharm.config import settings

pytestmark = pytest.mark.slow
if not (settings.artifacts_dir / "v2" / "pair_metrics.json").exists():
    pytest.skip("v2 artifacts not built", allow_module_level=True)

HEALTHY = {"age": 40, "sex": "M", "weight_kg": 72, "height_cm": 175, "scr": 0.9, "ast": 22, "alt": 20,
           "total_bilirubin": 0.6}


@pytest.fixture(scope="module")
def eng():
    from omnipharm.engine import Engine

    return Engine()


def verdict(eng, names, **patient):
    prods = [{"input": n, "ingredients": eng.resolve(n)} for n in names]
    r = eng.predict(prods, Patient(**{**HEALTHY, **patient}))
    return r["summary"]["verdict"], r


@pytest.mark.parametrize(("names", "expected", "patient"), [
    # moderate, documented: regular acetaminophen can raise INR on warfarin -> monitor
    (["acetaminophen", "warfarin"], {"주의"}, {}),
    # major: amiodarone label says reduce the warfarin dose
    (["warfarin", "amiodarone"], {"위험"}, {}),
    # contraindicated (strong CYP3A4 inhibitor + simvastatin)
    (["clarithromycin", "simvastatin"], {"위험"}, {}),
    (["팍스로비드", "simvastatin"], {"위험"}, {}),
    # FDA index table: strong CYP3A inhibitor x sensitive substrate
    (["ketoconazole", "midazolam"], {"위험"}, {}),
    # label says avoid (CYP2C19 inhibition blocks clopidogrel activation)
    (["clopidogrel", "omeprazole"], {"위험"}, {}),
    # OTC "ask a doctor if taking warfarin" text in a contraindication field must not become 위험
    (["ibuprofen", "cetirizine"], {"비교적 안전", "주의"}, {}),
    # biologics (rule/label based): GLP-1 + sulfonylurea -> hypoglycaemia; botulinum + aminoglycoside
    (["위고비", "아마릴"], {"주의", "위험"}, {}),
    (["마운자로", "야즈"], {"주의", "위험"}, {}),
    (["보톡스", "gentamicin"], {"주의", "위험"}, {}),
    # drug-disease: GLP-1 with medullary thyroid cancer (boxed warning)
    (["삭센다", "metformin"], {"위험"}, {"conditions": ["thyroid_cancer"]}),
    # standard RA combination: the boxed warning only names methotrexate as background -> not 위험
    (["휴미라", "methotrexate"], {"주의"}, {}),
    # boxed warning about the combination itself (opioid + benzodiazepine)
    (["oxycodone", "alprazolam"], {"위험"}, {}),
    # lifestyle drugs: nitrate + PDE5 inhibitor is contraindicated
    (["비아그라", "nitroglycerin"], {"위험"}, {}),
    # duplicated ingredient across products
    (["타이레놀", "콜대원코프에스시럽"], {"위험"}, {}),
    # drug-disease: NSAID with peptic ulcer
    (["ibuprofen", "acetaminophen"], {"위험"}, {"conditions": ["peptic_ulcer"]}),
    # moderate: hyperkalaemia risk
    (["lisinopril", "spironolactone"], {"주의", "위험"}, {}),
    # serotonergic combination
    (["sertraline", "tramadol"], {"주의", "위험"}, {}),
    # no meaningful interaction for a healthy adult
    (["cetirizine", "acetaminophen"], {"비교적 안전", "주의"}, {}),
])
def test_known_combinations(eng, names, expected, patient):
    v, r = verdict(eng, names, **patient)
    assert v in expected, (names, v, [x["text"] for x in r["summary"]["reasons"]])


def _prod(eng, name, dose=None):
    from omnipharm.dosing import Dose

    ings, info = eng.resolve_full(name)
    return {"input": name, "ingredients": ings, "dose": Dose(**dose) if dose else None, "strength": info.get("strength")}


@pytest.mark.parametrize(("name", "key"), [
    ("위고비", "bio:semaglutide"), ("위고비프리필드펜2.4밀리그램", "bio:semaglutide"), ("Ozempic", "bio:semaglutide"),
    ("삭센다", "bio:liraglutide"), ("마운자로", "bio:tirzepatide"), ("보톡스", "bio:botulinum toxin"),
    ("나보타", "bio:botulinum toxin"), ("란투스", "bio:insulin glargine"), ("휴미라", "bio:adalimumab"),
    ("로아큐탄", "isotretinoin"), ("프로페시아", "finasteride"), ("트라넥삼산", "tranexamic acid"),
])
def test_lifestyle_and_biologic_names_resolve(eng, name, key):
    assert eng.resolve(name)[0].key == key


@pytest.mark.parametrize("name", ["레미케이드", "키트루다", "광동쌍화탕", "인사돌", "infliximab", "pembrolizumab",
                                  "인플릭시맵", "제미글로", "인사돌플러스정"])
def test_structureless_products_are_usable(eng, name):
    """Antibodies, herbal and vitamin products are no longer rejected: label/rule-only ingredients."""
    try:
        ings = eng.resolve(name)
    except Exception as e:  # ambiguous product names return candidates, which is fine
        assert getattr(e, "candidates", None), (name, str(e))
        return
    assert ings


@pytest.mark.parametrize(("names", "expected"), [
    (["감초", "hydrochlorothiazide"], {"주의", "위험"}),      # licorice -> hypokalaemia
    (["ginkgo biloba leaf extract", "warfarin"], {"주의", "위험"}),
    (["calcium carbonate", "levofloxacin"], {"주의", "위험"}),  # chelation: separate doses
    (["potassium chloride", "spironolactone"], {"위험"}),      # hyperkalaemia
])
def test_herbal_and_mineral_rules(eng, names, expected):
    v, r = verdict(eng, names)
    assert v in expected, (names, v, [x["text"] for x in r["summary"]["reasons"]])


@pytest.mark.skipif(not (settings.processed_dir / "dur.json").exists(), reason="DUR not built")
def test_mfds_dur_rules(eng):
    """Official Korean DUR: contraindicated pair -> 위험; age / pregnancy / elderly checks use the patient."""
    v, r = verdict(eng, ["돔페리돈", "citalopram"])
    assert v == "위험" and any("DUR 병용금기" in x["text"] for x in r["summary"]["reasons"])
    v, r = verdict(eng, ["acarbose", "metformin"], age=12, weight_kg=40, height_cm=150)
    assert any("특정연령대금기" in d["reason"] for d in r["disease_alerts"]), r["disease_alerts"]
    v, r = verdict(eng, ["isotretinoin", "acetaminophen"], sex="F", conditions=["pregnancy"])
    assert v == "위험" and any("임부금기" in d["reason"] or "DUR" in d["source"] for d in r["disease_alerts"])
    v, r = verdict(eng, ["chlordiazepoxide", "acetaminophen"], age=78)
    assert any("노인주의" in d["reason"] for d in r["disease_alerts"])


def test_dur_daily_maximum_used_when_no_curated_limit(eng):
    from omnipharm import dosing

    assert "triazolam" in dosing.EXTRA_LIMITS or "triazolam" in dosing.DOSE_LIMITS


def test_quasi_drug_products_resolve(eng):
    if not (settings.processed_dir / "kr_quasi_products.parquet").exists():
        pytest.skip("quasi-drugs not built")
    try:
        assert eng.resolve("박카스")
    except Exception as e:  # several 박카스 products -> candidates
        assert getattr(e, "candidates", None), str(e)


@pytest.mark.skipif(not (settings.processed_dir / "kr_quasi_products.parquet").exists(), reason="quasi-drugs not built")
def test_tonic_drink_and_painkiller(eng):
    """박카스(caffeine + vitamins) + 타이레놀 is fine for a healthy adult; caffeine in two products is a note, not an
    overdose; paracetamol in two products (타이레놀 + 게보린) IS the real danger."""
    v, r = verdict(eng, ["박카스디액", "타이레놀정500밀리그람(아세트아미노펜)"])
    assert v == "비교적 안전", [x["text"] for x in r["summary"]["reasons"]]
    assert not any("니코틴산아미드" in x["text"] for x in r["summary"]["reasons"])  # vitamin: no alarming AI text
    v, r = verdict(eng, ["박카스디액", "게보린정"])
    assert v != "위험", [x["text"] for x in r["summary"]["reasons"]]
    v, _ = verdict(eng, ["타이레놀정500밀리그람(아세트아미노펜)", "게보린정"])
    assert v == "위험"


def test_class_phrases_cover_label_wording():
    from omnipharm.knowledge import class_phrases

    assert "strong CYP3A4 inducers" in class_phrases("rifampin")
    assert "MAOIs" in class_phrases("linezolid")
    assert any("QT" in p for p in class_phrases("haloperidol"))


def test_no_alert_fatigue_for_unrelated_drugs(eng):
    """Drugs without an interaction must not be flagged just because each has its own side effects."""
    for names in (["naproxen", "cetirizine"], ["finasteride", "amlodipine"]):
        v, r = verdict(eng, names)
        assert v == "비교적 안전", (names, [x["text"] for x in r["summary"]["reasons"]])


def test_fhir_bundle_end_to_end(eng):
    from omnipharm import fhir

    bundle = {"resourceType": "Bundle", "entry": [
        {"resource": {"resourceType": "Patient", "birthDate": "1948-01-01", "gender": "female"}},
        {"resource": {"resourceType": "Observation", "code": {"coding": [{"code": "29463-7"}]},
                      "valueQuantity": {"value": 50, "unit": "kg"}}},
        {"resource": {"resourceType": "MedicationRequest", "status": "active",
                      "medicationCodeableConcept": {"text": "warfarin"}}},
        {"resource": {"resourceType": "MedicationRequest", "status": "active",
                      "medicationCodeableConcept": {"coding": [{"display": "amiodarone"}]}}}]}
    drugs, pt, _ = fhir.parse(bundle)
    v, r = verdict(eng, [d["name"] for d in drugs], **{k: v for k, v in pt.items() if k != "conditions"})
    assert v == "위험"
    assert any(i["severity"] == "high" for i in fhir.detected_issues(r))


def test_commercial_safe_mode_drops_research_licensed_parts(monkeypatch):
    from omnipharm.engine import Engine

    monkeypatch.setattr(settings, "commercial_safe", True)
    e = Engine()
    assert not e.pair_models and not e.observed and not e.jp_names
    assert "JP" not in set(e.products["country"])
    prods = [{"input": n, "ingredients": e.resolve(n)} for n in ("warfarin", "amiodarone")]
    assert e.predict(prods, Patient(**HEALTHY))["summary"]["verdict"] == "위험"  # documented evidence still works


def test_biologic_skips_structure_models(eng):
    _, r = verdict(eng, ["위고비", "아마릴"])
    pair = r["pairs"][0]
    assert pair["biologic"] and not pair["novel"] and pair["model"] == []
    assert any("생물의약품" in n for n in r["notes"])


def test_overdose_across_products_uses_real_strengths(eng):
    """타이레놀 500mg 2정 x4 (4,000mg) + 콜대원코프에스시럽 1포 x3 (325mg x3) -> 하루 4,975mg > 4,000mg."""
    prods = [_prod(eng, "타이레놀정500밀리그람(아세트아미노펜)", {"amount": 2, "per_day": 4, "days": 3}),
             _prod(eng, "콜대원코프에스시럽", {"amount": 1, "per_day": 3, "days": 3})]
    r = eng.predict(prods, Patient(**HEALTHY))
    apap = next(x for x in r["dosing"]["ingredients"] if x["key"] == "acetaminophen")
    assert round(apap["daily_mg"]) == 4975 and apap["status"] == "초과"
    assert r["summary"]["verdict"] == "위험"


def test_safe_dose_stays_safe(eng):
    prods = [_prod(eng, "타이레놀정500밀리그람(아세트아미노펜)", {"amount": 1, "per_day": 3, "days": 3}),
             _prod(eng, "cetirizine", {"amount": 10, "unit": "mg", "per_day": 1, "days": 3})]
    r = eng.predict(prods, Patient(**HEALTHY))
    assert all(x["status"] == "적정" for x in r["dosing"]["ingredients"])
    assert r["summary"]["verdict"] != "위험"


def test_same_dose_exceeds_limit_in_kidney_failure(eng):
    prods = [_prod(eng, "pregabalin", {"amount": 150, "unit": "mg", "per_day": 2, "days": 14}),
             _prod(eng, "acetaminophen", {"amount": 500, "unit": "mg", "per_day": 2, "days": 3})]
    ok = eng.predict(prods, Patient(**HEALTHY))
    ckd = eng.predict(prods, Patient(**{**HEALTHY, "age": 78, "weight_kg": 55, "scr": 2.4}))
    pg = lambda r: next(x for x in r["dosing"]["ingredients"] if x["key"] == "pregabalin")  # noqa: E731
    assert pg(ok)["status"] == "적정" and pg(ckd)["status"] == "초과"


def test_ai_signal_alone_never_makes_danger(eng):
    """Statistical signals cap at 주의: '위험' needs documented / rule evidence (see docs/MODEL_CARD.md)."""
    v, r = verdict(eng, ["cetirizine", "acetaminophen"])
    assert v != "위험"
    assert all(x["sev"] != "high" for x in r["summary"]["reasons"] if "AI 예측" in x["text"])


def test_inherent_risk_is_explained_as_such(eng):
    """Warfarin's own bleeding profile is described as the drug's inherent risk, not as an interaction."""
    _, r = verdict(eng, ["acetaminophen", "warfarin"])
    inr = next(e for e in r["events"] if e["condition"] == "International normalised ratio increased")
    assert inr["level_word"] in ("약 자체 위험", "약간 높음", "높음", "매우 높음")


def test_same_drugs_more_dangerous_for_frail_patient(eng):
    v_young, _ = verdict(eng, ["ibuprofen", "lisinopril"])
    v_old, _ = verdict(eng, ["ibuprofen", "lisinopril"], age=84, sex="F", weight_kg=44, height_cm=150, scr=1.6,
                       conditions=["heart_failure"])
    order = {"비교적 안전": 0, "주의": 1, "위험": 2}
    assert order[v_old] > order[v_young] or v_old == "위험"
