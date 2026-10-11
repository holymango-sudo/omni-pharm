"""End-to-end engine tests on the real processed data (skipped when artifacts are not built)."""

from __future__ import annotations

import pytest

from omnipharm.clinical import Patient
from omnipharm.config import settings

pytestmark = pytest.mark.slow
if not (settings.artifacts_dir / "v2" / "pair_metrics.json").exists():
    pytest.skip("v2 artifacts not built", allow_module_level=True)


@pytest.fixture(scope="module")
def eng():
    from omnipharm.engine import Engine

    return Engine()


def test_resolve_korean_brand_ingredient_and_english(eng):
    assert [g.key for g in eng.resolve("타이레놀")] == ["acetaminophen"]
    assert [g.key for g in eng.resolve("와파린")] == ["warfarin"]
    assert [g.key for g in eng.resolve("Warfarin")] == ["warfarin"]
    assert {g.key for g in eng.resolve("아세트아미노펜+카페인")} == {"acetaminophen", "caffeine"}


def test_modern_combination_brand(eng):
    assert {g.key for g in eng.resolve("팍스로비드")} == {"nirmatrelvir", "ritonavir"}


def test_paxlovid_simvastatin_is_label_contraindication(eng):
    r = eng.predict(_products(eng, "팍스로비드", "심바스타틴"), Patient(age=60, sex="M"))
    sim = [p for p in r["pairs"] if "simvastatin" in (p["a_key"], p["b_key"])]
    assert any(e["section"] == "병용금기/금기" for p in sim for e in p["label_evidence"])
    assert r["overall_level"] == "높음"


def test_known_structure_typed_as_smiles_maps_to_db_drug(eng):
    assert eng.resolve("CC(=O)Nc1ccc(O)cc1")[0].key == "acetaminophen"


def test_novel_smiles_is_accepted(eng):
    g = eng.resolve("CC(C)(C)c1ccc(cc1)C(=O)NCCN1CCOCC1")[0]
    assert g.idx is None and g.key.startswith("smiles:")


def test_unknown_name_is_rejected_with_reason(eng):
    from omnipharm.engine import ResolveError

    with pytest.raises(ResolveError):
        eng.resolve("존재하지않는약")


def _products(eng, *names):
    return [{"input": n, "ingredients": eng.resolve(n)} for n in names]


def test_predict_structure_and_symmetry(eng):
    p = Patient(age=70, sex="M", weight_kg=70)
    a = eng.predict(_products(eng, "warfarin", "aspirin"), p)
    b = eng.predict(_products(eng, "aspirin", "warfarin"), p)
    assert a["overall_level"] in ("높음", "주의", "낮음")
    assert len(a["pairs"]) == 1
    assert len(a["events"]) == (eng.C if eng.patient_model is not None else 0)
    pa = {m["ko"]: m["prob"] for m in a["pairs"][0]["model"]}
    pb = {m["ko"]: m["prob"] for m in b["pairs"][0]["model"]}
    assert pa.keys() == pb.keys() and all(abs(pa[k] - pb[k]) < 1e-5 for k in pa)
    if eng.patient_model is not None:
        ea = {e["id"]: e["prob"] for e in a["events"]}
        eb = {e["id"]: e["prob"] for e in b["events"]}
        assert all(abs(ea[k] - eb[k]) < 1e-4 for k in ea)  # regimen is an unordered set


def test_warfarin_aspirin_shows_bleeding_evidence(eng):
    r = eng.predict(_products(eng, "warfarin", "aspirin"), Patient(age=75, sex="F"))
    pair = r["pairs"][0]
    assert pair["label_evidence"], "warfarin label documents aspirin interaction"
    if eng.patient_model is not None:
        blood = [e for e in r["events"] if e["organ"] in ("blood", "gi")]
        assert max(e["lift"] for e in blood) > 1.5


def test_duplicate_ingredient_is_high(eng):
    r = eng.predict(_products(eng, "타이레놀", "아세트아미노펜+카페인"), Patient())
    assert r["duplicates"] and r["overall_level"] == "높음"


def test_patient_state_changes_prediction(eng):
    if eng.patient_model is None:
        pytest.skip("patient model not trained")
    prods = _products(eng, "warfarin", "amiodarone")
    healthy = eng.predict(prods, Patient(age=40, sex="M", scr=0.9))
    sick = eng.predict(prods, Patient(age=80, sex="M", scr=3.0, ast=200, alt=220, total_bilirubin=3))
    h = {e["id"]: e["prob"] for e in healthy["events"]}
    s = {e["id"]: e["prob"] for e in sick["events"]}
    assert any(abs(s[k] - h[k]) > 0.005 for k in h)
    assert sick["patient"]["flags"]["renal"] and sick["patient"]["flags"]["hepatic"]


def test_drug_disease_makes_the_same_drugs_more_dangerous(eng):
    prods = _products(eng, "ibuprofen", "cetirizine")
    healthy = eng.predict(prods, Patient(age=40, sex="M"))
    ulcer = eng.predict(prods, Patient(age=40, sex="M", diagnosis="위궤양"))
    assert not any(d["drug"].endswith("(ibuprofen)") for d in healthy["disease_alerts"])
    hit = [d for d in ulcer["disease_alerts"] if "ibuprofen" in d["drug"]]
    assert hit and hit[0]["severity"] == "금기" and ulcer["overall_level"] == "높음"
    assert ulcer["summary"]["verdict"] == "위험" and ulcer["summary"]["reasons"]


def test_glaucoma_and_sedating_antihistamine(eng):
    r = eng.predict(_products(eng, "chlorpheniramine", "acetaminophen"), Patient(age=50, conditions=["glaucoma"]))
    assert any(d["disease"] == "녹내장" for d in r["disease_alerts"])


def test_offline_template_report_renders(eng):
    from omnipharm.models.llm import fact_sheet, template_report

    p = Patient(age=80, sex="F", scr=2.0, ast=150, alt=160, total_bilirubin=2.5)
    r = eng.predict(_products(eng, "warfarin", "amiodarone", "ibuprofen"), p)
    text = template_report(fact_sheet(r, p.model_dump()))
    assert "### 1." in text and "종합 판정: **위험**" in text


def test_novel_molecule_gets_pair_prediction(eng):
    prods = _products(eng, "warfarin", "CC(C)(C)c1ccc(cc1)C(=O)NCCN1CCOCC1")
    r = eng.predict(prods, Patient())
    assert r["pairs"][0]["novel"]
    assert r["ingredients"][1]["novel"]
