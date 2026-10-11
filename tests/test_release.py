"""Extended / delayed-release products: recognised from the product name, with their own peak time, daily maximum,
single-dose limit, dosing frequency and handling advice - and the strength table they rely on."""

from __future__ import annotations

import pytest

from omnipharm.clinical import Patient
from omnipharm.config import settings
from omnipharm.dosing import Dose, check_doses
from omnipharm.pipeline.strengths import _kr_weight
from omnipharm.release import release_of


@pytest.mark.parametrize(("name", "form"), [
    ("타이레놀8시간이알서방정(아세트아미노펜)", "ER"), ("글루코파지엑스알1000밀리그램서방정", "ER"), ("유니필서방정400mg(테오필린)", "ER"),
    ("콘서타OROS서방정", "ER"), ("APO-OXYCODONE CR", "ER"), ("Wellbutrin XL", "ER"), ("Metformin ER 750 mg", "ER"),
    ("ALFATIL LP 500 mg", "ER"), ("OxyContin", "ER"), ("Pregabalin Extended Release", "ER"),
    ("아스피린장용정", "DR"), ("Omeprazole Delayed-Release", "DR"), ("Omeprazole DR 20 mg", "DR"),
    ("타이레놀정500밀리그람", "IR"), ("프리베나13주(폐렴구균 씨알엠(CRM197) 단백접합백신)", "IR"), ("LA ROCHE POSAY Cicaplast", "IR"),
    ("SUPER CRÈME WITH SPF", "IR"), ("JABON SULFUROSO DEL DR HOLST", "IR"), ("Dr. Mercola Broad Spectrum", "IR"), ("Ultram", "IR"),
])
def test_release_form_from_name(name, form):
    assert release_of(name) == form


def test_tablet_weight_parsing():
    assert _kr_weight("1정615.04밀리그램") == pytest.approx(615.04)
    assert _kr_weight("1정 (약 787.95 밀리그램) 중-속방층") == pytest.approx(787.95)
    assert _kr_weight("1포20밀리리터") is None


CTX = {"crcl": 100, "age": 40, "sex": "M", "weight": 70, "hepatic": False, "all_keys": []}
ER_TYLENOL = {"unit": "정", "per": "unit", "mg": {"acetaminophen": 650.0}}


def _check(items):
    return check_doses(items, CTX, lambda k: {k}, lambda k: k)


def test_er_limits_and_frequency():
    # Tylenol 8-hour ER: 2 tablets (1,300 mg) every 8 hours is the labelled dose - no single-dose alarm
    ok = _check([{"input": "타이레놀8시간이알서방정", "keys": ["acetaminophen"], "release": "ER", "strength": ER_TYLENOL,
                  "dose": Dose(amount=2, per_day=3)}])
    assert ok["alerts"] == []
    # 4 times a day: frequency caution + over the daily maximum
    four = _check([{"input": "타이레놀8시간이알서방정", "keys": ["acetaminophen"], "release": "ER", "strength": ER_TYLENOL,
                    "dose": Dose(amount=2, per_day=4)}])
    assert any("서방형인데 하루 4회" in a["text"] for a in four["alerts"])
    assert any(a["sev"] == "금기" for a in four["alerts"])
    # metformin ER: 2,550 mg/day is fine for ordinary tablets but over the ER maximum of 2,000 mg
    met = {"unit": "정", "per": "unit", "mg": {"metformin": 850.0}}
    ir = _check([{"input": "metformin", "keys": ["metformin"], "release": "IR", "strength": met, "dose": Dose(amount=1, per_day=3)}])
    er = _check([{"input": "metformin XR", "keys": ["metformin"], "release": "ER", "strength": met, "dose": Dose(amount=1, per_day=3)}])
    assert ir["alerts"] == [] and any("서방형" in a["text"] and "2,000mg" in a["text"] for a in er["alerts"])
    # pregabalin ER is not used when CrCl < 30
    low = check_doses([{"input": "lyrica cr", "keys": ["pregabalin"], "release": "ER", "dose": Dose(amount=330, unit="mg", per_day=1)}],
                      {**CTX, "crcl": 25}, lambda k: {k}, lambda k: k)
    assert any("서방형은 쓰지 않고" in a["text"] for a in low["alerts"])


needs_models = pytest.mark.skipif(not (settings.artifacts_dir / "v2" / "pair_metrics.json").exists(),
                                  reason="v2 artifacts not built")


@pytest.fixture(scope="module")
def eng():
    from omnipharm.engine import Engine

    return Engine()


@needs_models
@pytest.mark.slow
def test_product_choice_strength_and_journey(eng):
    # the product whose name matches best (tablet, not the powder of the same composition)
    _, info = eng.resolve_full("타이레놀정500밀리그람")
    assert info["product"].startswith("타이레놀정500") and info["strength"]["mg"]["acetaminophen"] == 500
    ings, info = eng.resolve_full("타이레놀8시간이알서방정")
    assert info["release"] == "ER" and info["strength"]["mg"]["acetaminophen"] == 650  # not 650 + 325 layers
    prods = [{"input": "타이레놀8시간이알서방정", "ingredients": ings, "product": info["product"], "release": "ER",
              "strength": info["strength"], "dose": Dose(amount=2, per_day=3, days=3)}]
    r = eng.predict(prods, Patient(age=40, sex="M", weight_kg=72, height_cm=175, scr=0.9))
    d = r["pathway"]["drugs"][0]
    assert d["release"] == "ER" and d["pk"]["tmax_h"] > 1 and d["regimen"]["per_day"] == 3 and d["regimen"]["source"] == "입력값"
    assert any("통째로 삼키세요" in a for a in r["summary"]["advice"])
    assert any(i["kind"] == "er" for i in r["pathway"]["stages"][0]["items"])


@needs_models
@pytest.mark.slow
def test_default_regimen_follows_half_life(eng):
    prods = [{"input": n, "ingredients": eng.resolve(n)} for n in ("amlodipine", "ibuprofen")]
    r = eng.predict(prods, Patient(age=40, sex="M", weight_kg=72, height_cm=175, scr=0.9))
    reg = {d["key"]: d["regimen"] for d in r["pathway"]["drugs"]}
    assert reg["amlodipine"]["per_day"] == 1 and reg["ibuprofen"]["per_day"] >= 3  # t1/2 ~40 h vs ~2 h
    assert all(v["source"].startswith("추정") for v in reg.values())
