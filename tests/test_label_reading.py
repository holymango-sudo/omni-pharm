"""v2.18: reading FDA label sentences correctly - '위험' only for what the label really says about using the two TOGETHER.

Found by reviewing the '위험' results on real FAERS medicine lists (scripts/alert_burden.py): about a third were label
sentences misread as contraindications (an allergy, the class's own contraindication, an intended combination, a
co-formulated product, a dose that needs NO adjustment ...). Every case below is a real label sentence that was misread,
or a real contraindication that a first fix lost (the official Korean DUR list switched off, as in
scripts/validate_external.py).
"""

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

    e = Engine()
    yield e


def level(eng, a: str, b: str, dur: bool = False) -> str:
    """The pair level of predict() (DUR switched off unless dur=True: the label reading alone)."""
    saved = eng.dur_pairs
    if not dur:
        eng.dur_pairs = {}
    try:
        r = eng.predict([{"input": a, "ingredients": eng.resolve(a)}, {"input": b, "ingredients": eng.resolve(b)}],
                        Patient(age=45, sex="M", weight_kg=70, height_cm=172, scr=0.9))
    finally:
        eng.dur_pairs = saved
    return r["pairs"][0]["level"]


@pytest.mark.parametrize(("a", "b"), [
    ("olanzapine", "fluoxetine"),        # 'When using ... in combination, also refer to ... Symbyax' (Symbyax)
    ("darunavir", "ritonavir"),          # pharmacokinetic booster: the interaction is the purpose
    ("cabotegravir", "rilpivirine"),     # Cabenuva: one co-packaged regimen
    ("bupivacaine", "epinephrine"),      # co-formulated injection
    ("midazolam", "sodium chloride"),    # 'Midazolam in 0.9% Sodium Chloride Injection': the vehicle
    ("dexamethasone", "hydrocortisone"),  # 'Topical corticosteroids are contraindicated in ... hypersensitivity'
    ("rosuvastatin", "calcium carbonate"),  # 'rosuvastatin CALCIUM' is the salt
    ("risperidone", "amitriptyline"),    # 'Dose adjustment is not recommended' = no adjustment needed
    ("cisplatin", "carboplatin"),        # 'avoid cisplatin overdose due to confusion with carboplatin' (names)
    ("estradiol", "ethinyl estradiol"),  # the label's own moiety, not the other drug
])
def test_misread_label_sentences_are_not_contraindications(eng, a, b):
    assert level(eng, a, b) != "높음"


@pytest.mark.parametrize(("a", "b"), [
    ("lithium", "hydrochlorothiazide"),   # 'should not be given with diuretics ... refer to the package insert'
    ("pimozide", "imipramine"),          # 'drugs that prolong the QT interval' (pimozide is one itself)
    ("dronedarone", "amitriptyline"),    # 'drugs or herbal products that prolong the QT interval'
    ("dronedarone", "cyclosporine"),     # list item '• Concomitant use of strong CYP3A inhibitors, such as ...'
    ("safinamide", "linezolid"),         # 'Patients taking ANY monoamine oxidase inhibitors' (own class)
    ("rasagiline", "selegiline"),        # 'MAO inhibitors, including OTHER selective MAO-B inhibitors'
    ("pitavastatin", "cyclosporine"),    # 'Concomitant use of cyclosporine with pitavastatin is contraindicated'
    ("fosaprepitant", "pimozide"),       # table row 'Intervention: Fosaprepitant is contraindicated'
    ("nitroglycerin", "ergotamine"),     # 'patients receiving nitroglycerin should avoid ergotamine'
    ("palbociclib", "voriconazole"),     # 'Avoid ... strong CYP3A inhibitors (e.g., ..., and voriconazole)'
    ("ephedrine", "selegiline"),         # OTC 'Do not use if you are now taking a prescription MAOI'
])
def test_real_contraindications_survive_without_dur(eng, a, b):
    assert level(eng, a, b) == "높음"


@pytest.mark.parametrize(("a", "b"), [
    ("udenafil", "nicorandil"),          # Korean PDE5 inhibitor + NO donor (no FDA label for either)
    ("isotretinoin", "doxycycline"),     # pseudotumor cerebri
    ("phentermine", "phendimetrazine"),  # two anorectics
    ("dihydroergotamine", "clarithromycin"),
    ("domperidone", "ondansetron"),      # EMA 2014: QT
    ("domperidone", "ketoconazole"),     # EMA 2014: strong CYP3A inhibitor
    ("cyclosporine", "tacrolimus"),
    ("rifampin", "midazolam"),           # strong inducer x sensitive substrate (exposure -80% or more)
])
def test_documented_rules_from_dur_development_half(eng, a, b):
    """Rules added from the DEVELOPMENT half of the DUR validation only (scripts/validate_external.py)."""
    assert level(eng, a, b) == "높음"


def test_intended_combination_is_capped_and_explained(eng):
    r = eng.predict([{"input": "olanzapine", "ingredients": eng.resolve("olanzapine")},
                     {"input": "fluoxetine", "ingredients": eng.resolve("fluoxetine")}], Patient(age=40))
    assert r["summary"]["verdict"] != "위험"
    assert any("허가된" in n and "복합제" in n for n in r["notes"])


def test_ai_patient_signal_does_not_raise_the_verdict(eng):
    """Only a signal of the COMBINATION (x3 over the riskier drug alone) drives the verdict; one that comes from the
    patient's age / body alone is shown as information."""
    from omnipharm.engine import COMBO_X, _combo

    r = eng.predict([{"input": n, "ingredients": eng.resolve(n)} for n in ("warfarin", "녹색채소")],
                    Patient(age=78, sex="F", weight_kg=50, height_cm=152, scr=1.3))
    assert all(_combo(e) >= COMBO_X for e in r["events"] if e["drives_verdict"])
    assert r["summary"]["action"]["level"] == "확인 필요"  # a documented interaction: check, not panic


@pytest.mark.parametrize(("names", "action"), [
    (["simvastatin", "itraconazole"], "즉시 상담"),
    (["warfarin", "acetaminophen"], "확인 필요"),
    (["amlodipine", "metformin"], "조치 없음"),
])
def test_action_levels(eng, names, action):
    r = eng.predict([{"input": n, "ingredients": eng.resolve(n)} for n in names], Patient(age=50))
    assert r["summary"]["action"]["level"] == action
