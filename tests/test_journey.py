"""Body-journey data (res["pathway"]) behind the animated body view: it must tell the same story as the verdict,
place every finding on a body site the UI can draw, and use only documented / computed evidence."""

from __future__ import annotations

import pytest

from omnipharm.clinical import Patient
from omnipharm.config import settings
from omnipharm.journey import SITES, _moa_ko

HEALTHY = {"age": 40, "sex": "M", "weight_kg": 72, "height_cm": 175, "scr": 0.9, "ast": 22, "alt": 20,
           "total_bilirubin": 0.6}
STAGES = ["intake", "absorb", "liver", "blood", "target", "problem", "excrete"]


def test_mechanism_wording_is_translated():
    assert _moa_ko("Human immunodeficiency virus type 1 protease inhibitor") == "HIV-1 단백분해효소 억제"
    assert _moa_ko("SARS coronavirus 3C-like proteinase inhibitor") == "코로나바이러스 3CL 단백분해효소 억제"
    assert _moa_ko("Mu opioid receptor agonist").endswith("수용체 작용")


needs_models = pytest.mark.skipif(not (settings.artifacts_dir / "v2" / "pair_metrics.json").exists(),
                                  reason="v2 artifacts not built")


@pytest.fixture(scope="module")
def eng():
    from omnipharm.engine import Engine

    return Engine()


def journey(eng, names, **patient):
    prods = [{"input": n, "ingredients": eng.resolve(n)} for n in names]
    r = eng.predict(prods, Patient(**{**HEALTHY, **patient}))
    assert r["pathway"] is not None, "journey failed (see log)"
    return r, r["pathway"], {s["id"]: s for s in r["pathway"]["stages"]}


@needs_models
@pytest.mark.slow
@pytest.mark.parametrize("names", [
    ["simvastatin", "clarithromycin"], ["warfarin", "ibuprofen", "sertraline"], ["oxycodone", "alprazolam", "술"],
    ["ciprofloxacin", "우유"], ["위고비", "인슐린"], ["finasteride", "minoxidil"], ["acetaminophen", "타이레놀"],
    ["levothyroxine", "calcium carbonate"], ["팍스로비드", "simvastatin"],
])
def test_structure_and_consistency(eng, names):
    r, j, st = journey(eng, names)
    assert [s["id"] for s in j["stages"]] == STAGES
    keys = {i["key"] for i in r["ingredients"]}
    assert {d["key"] for d in j["drugs"]} == keys
    for s in j["stages"]:
        assert s["site"] in SITES and s["sev"] in ("normal", "low", "mid", "high") and s["text"]
    for b in st["target"]["binds"]:
        assert b["site"] in SITES and b["drug"] in keys
    for p in st["problem"]["problems"]:
        assert p["sites"] and all(x in SITES for x in p["sites"]) and set(p["drugs"]) <= keys
        assert [c["k"] for c in p["chain"]] == ["원인", "기전", "결과", "근거"] and all(c["v"] for c in p["chain"])
    for lv in st["blood"]["levels"]:
        assert lv["drug"] in keys and lv["dir"] in ("up", "down")
        assert (lv["fold"] > 1) == (lv["dir"] == "up") and lv["fold_label"]  # the PK sketch follows the direction
    for b in st["target"]["binds"]:
        assert b["protein"] in ("gpcr", "channel", "transporter", "nuclear", "enzyme", "pathogen", "other")
    # the body view never contradicts the verdict: a red organ exactly when the verdict is '위험'
    assert (r["summary"]["verdict"] == "위험") == any(p["sev"] == "high" for p in st["problem"]["problems"])
    assert j["verdict"] == r["summary"]["verdict"]


@needs_models
@pytest.mark.slow
def test_enzyme_inhibition_story(eng):
    _, _, st = journey(eng, ["simvastatin", "clarithromycin"])
    c = st["liver"]["clashes"][0]
    assert (c["enzyme"], c["perpetrator"], c["victim"], c["dir"], c["sev"]) == \
        ("CYP3A4", "clarithromycin", "simvastatin", "up", "high")
    assert any(lv["drug"] == "simvastatin" and lv["dir"] == "up" for lv in st["blood"]["levels"])
    assert st["problem"]["problems"][0]["sites"][0] == "muscle"  # accumulated statin -> rhabdomyolysis
    chain = {c["k"]: c["v"] for c in st["problem"]["problems"][0]["chain"]}
    assert "CYP3A4 억제" in chain["기전"] and "AUC ≥5×" in chain["기전"] and "횡문근융해" in chain["결과"]
    lv = next(x for x in st["blood"]["levels"] if x["drug"] == "simvastatin")
    assert lv["fold"] == 5.0


@needs_models
@pytest.mark.slow
def test_prodrug_activation_blocked(eng):
    _, _, st = journey(eng, ["clopidogrel", "omeprazole"])
    c = next(c for c in st["liver"]["clashes"] if c["victim"] == "clopidogrel")
    assert c["enzyme"] == "CYP2C19" and c["dir"] == "down"


@needs_models
@pytest.mark.slow
def test_absorption_steps(eng):
    _, _, st = journey(eng, ["ciprofloxacin", "우유"])
    assert any(i["kind"] == "chelation" for i in st["intake"]["items"])
    assert any(lv["drug"] == "ciprofloxacin" and lv["dir"] == "down" for lv in st["blood"]["levels"])
    _, _, st = journey(eng, ["simvastatin", "자몽주스"])
    gut = st["absorb"]["items"][0]
    assert gut["kind"] == "gut_cyp" and gut["sev"] == "high"
    assert st["problem"]["problems"][0]["sites"][0] == "muscle"


@needs_models
@pytest.mark.slow
def test_overlapping_targets_and_breathing(eng):
    _, _, st = journey(eng, ["oxycodone", "alprazolam"])
    assert any(o["site"] == "brain" for o in st["target"]["overlaps"])
    sites = {s for p in st["problem"]["problems"] for s in p["sites"]}
    assert {"brain", "lung"} <= sites
    chain = {c["k"]: c["v"] for c in st["problem"]["problems"][0]["chain"]}
    assert "동시 작용" in chain["기전"] and "호흡 억제" in chain["결과"]
    kinds = {b["drug"]: b["protein"] for b in st["target"]["binds"]}
    assert kinds == {"oxycodone": "gpcr", "alprazolam": "channel"}  # mu-opioid GPCR, GABA-A chloride channel
    # parts of one combination product do not 'overlap' (ritonavir boosts nirmatrelvir by design)
    _, _, st = journey(eng, ["팍스로비드"])
    assert not st["target"]["overlaps"]


@needs_models
@pytest.mark.slow
def test_kidney_accumulation(eng):
    _, _, st = journey(eng, ["metformin", "gabapentin"], scr=2.6, age=78)
    assert st["excrete"]["sev"] == "high" and {"metformin", "gabapentin"} <= set(st["excrete"]["renal"])
    assert {lv["drug"] for lv in st["blood"]["levels"] if lv["dir"] == "up"} >= {"metformin", "gabapentin"}
    _, _, st = journey(eng, ["metformin", "gabapentin"])
    assert st["excrete"]["sev"] == "normal"


@needs_models
@pytest.mark.slow
def test_routes_and_safe_combination(eng):
    _, j, _ = journey(eng, ["위고비", "인슐린"])
    assert {d["route"] for d in j["drugs"]} == {"injection"}
    _, j, _ = journey(eng, ["흡연", "clozapine"])
    assert next(d for d in j["drugs"] if d["kind"] == "food")["route"] == "inhaled"
    r, j, st = journey(eng, ["finasteride", "minoxidil"])
    assert r["summary"]["verdict"] == "비교적 안전" and not st["problem"]["problems"] and not j["site_state"]


def test_duration_wording():
    from omnipharm.journey import fmt_h

    assert [fmt_h(x) for x in (0.25, 1.0, 1.85, 30, 50, 24 * 21)] == ["15분", "1시간", "1시간 50분", "30시간", "2.1일", "3주"]


@needs_models
@pytest.mark.slow
def test_time_after_the_dose(eng):
    _, j, st = journey(eng, ["simvastatin", "clarithromycin"])
    c = j["clock"]
    assert c["lead"] == "simvastatin" and 1 <= c["tmax_h"] <= 3 and c["fold"] == 5.0 and c["t_half_eff_h"] == 5 * c["t_half_h"]
    assert all(0 <= s["time"]["start_h"] < s["time"]["end_h"] for s in j["stages"])
    assert st["blood"]["time"]["start_h"] == c["tmax_h"]  # the peak sits at Tmax
    assert not c["acute"] and st["problem"]["time"]["start_h"] >= 24  # muscle damage builds up over days
    _, j, st = journey(eng, ["oxycodone", "alprazolam"])
    assert j["clock"]["acute"] and st["problem"]["time"]["end_h"] < 24  # breathing: hours after the dose
    _, j, st = journey(eng, ["ciprofloxacin", "우유"])
    assert st["problem"]["time"]["start_h"] == 0  # the absorption block starts when they are taken together
    _, j, _ = journey(eng, ["acetaminophen", "타이레놀"])
    assert j["clock"]["source"] == "성인 표준 참고치"  # OTC labels carry no PK numbers


@needs_models
@pytest.mark.slow
def test_body_follows_the_patient(eng):
    _, j, _ = journey(eng, ["simvastatin", "clarithromycin"])
    assert j["subject"]["sex"] == "M" and not j["subject"]["pregnant"] and j["subject"]["age"] == 40
    _, j, _ = journey(eng, ["simvastatin", "clarithromycin"], sex="F", conditions=["pregnancy"])
    assert j["subject"]["sex"] == "F" and j["subject"]["pregnant"]
