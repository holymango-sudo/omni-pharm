"""Recreational / controlled substances (harm reduction and research): names -> ingredients, curated structures,
the documented dangerous combinations, no amounts, and no names in the audit log."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from rdkit import Chem
from rdkit.Chem.rdMolDescriptors import CalcMolFormula

from omnipharm import api, recreational, security
from omnipharm.config import settings
from omnipharm.knowledge import FOOD_ALIAS
from omnipharm.recreational import SUBSTANCES

# molecular formulas from PubChem: a typo in a curated SMILES changes the formula
FORMULA = {"jwh-018": "C24H23NO", "mda": "C10H13NO2", "mephedrone": "C11H15NO", "cathinone": "C9H11NO",
           "alpha-pvp": "C15H21NO", "gamma-butyrolactone": "C4H6O2", "1,4-butanediol": "C4H10O2",
           "phencyclidine": "C17H25N", "methoxetamine": "C15H21NO2", "phenibut": "C10H13NO2",
           "isobutyl nitrite": "C4H9NO2", "carfentanil": "C24H30N2O3", "isotonitazene": "C23H30N4O3",
           "lysergide": "C20H25N3O", "psilocybin": "C12H17N2O4P", "psilocin": "C12H16N2O",
           "dimethyltryptamine": "C12H16N2", "5-meo-dmt": "C13H18N2O", "harmine": "C13H12N2O",
           "mescaline": "C11H17NO3", "2c-b": "C10H14BrNO2", "25i-nbome": "C18H22INO3", "ibogaine": "C20H26N2O",
           "salvinorin a": "C23H28O8", "muscimol": "C4H6N2O2"}


def test_curated_structures_match_their_formula():
    curated = {k for k, s in SUBSTANCES.items() if "smiles" in s}
    assert curated == set(FORMULA)
    for k in curated:
        m = Chem.MolFromSmiles(SUBSTANCES[k]["smiles"])
        assert m is not None and CalcMolFormula(m) == FORMULA[k], k


def test_every_substance_is_complete():
    for k, s in SUBSTANCES.items():
        assert sum(x in s for x in ("db", "smiles", "parts")) == 1, k
        assert s["law"] in recreational.LAW and s["kind"] in recreational.SIGNS, k
        for p in s.get("parts", []):
            assert p in SUBSTANCES, (k, p)


def test_aliases_are_unique_and_never_a_food():
    seen: dict[str, str] = {}
    for k, s in SUBSTANCES.items():
        for a in [k, *s["aliases"]]:
            n = recreational._norm(a)
            assert seen.setdefault(n, k) == k, (a, k, seen[n])
            assert n not in FOOD_ALIAS, a


def test_street_names_and_ordinary_words():
    for t, k in {"대마": "cannabis", "마리화나": "cannabis", "Cannabis": "cannabis", "엑스터시": "mdma", "MDMA": "mdma",
                 "필로폰": "methamphetamine", "물뽕": "ghb", "웃음가스": "laughing gas", "파퍼스": "poppers",
                 "LSD": "lysergide", "환각버섯": "psilocybin", "크라톰": "kratom", "케타민": "ketamine",
                 "코카인": "cocaine", "헤로인": "heroin", "매직 머쉬룸": "psilocybin"}.items():
        assert recreational.key_of(t) == k, t
    # medicines with ordinary medical use are not flagged by their own names; ordinary words are not street names
    for t in ("amphetamine", "dronabinol", "cannabidiol", "nitrous oxide", "codeine", "sodium oxybate", "coke", "spice",
              "ice", "k2", "salvia", "대마도", "러쉬", "메스", "타이레놀"):
        assert recreational.key_of(t) is None, t


def test_audit_mask_and_search():
    assert recreational.mask("대마") == recreational.mask(" MDMA ") == "[규제물질]"
    assert recreational.mask("타이레놀") == "타이레놀"
    rows = recreational.search("대마")
    assert rows[0]["name"] == "대마" and rows[0]["type"] == "규제물질"
    assert recreational.search("대") == []  # one letter lists nothing (only an exact one-letter name)


needs_models = pytest.mark.skipif(not (settings.artifacts_dir / "v2" / "pair_metrics.json").exists(),
                                  reason="v2 artifacts not built")


@pytest.fixture(scope="module")
def eng():
    from omnipharm.engine import Engine

    return Engine()


@needs_models
@pytest.mark.slow
def test_every_substance_resolves_offline(eng, monkeypatch):
    monkeypatch.setattr(settings, "online_lookup", False)
    for k in SUBSTANCES:
        ings, info = eng.resolve_full(k)
        assert ings and info["controlled"]["key"] == k, k
        assert all(g.smiles for g in ings), k  # every one has a structure for the AI models
    ings, info = eng.resolve_full("cannabis")  # not the cosmetic with 'Cannabis Sativa Seed Oil' in its name
    assert {g.key for g in ings} == {"dronabinol", "cannabidiol"} and info["product"] is None
    lsd = eng.resolve("LSD")[0]
    assert lsd.key == "ing:lysergide" and eng.ko(lsd).startswith("LSD")
    assert eng.ko(eng.resolve("sodium oxybate")[0]).startswith("GHB")  # not '물뽕' for a narcolepsy patient
    assert "controlled" not in eng.resolve_full("dronabinol")[1]
    assert any(r["type"] == "규제물질" for r in eng.search("엑스터"))


@needs_models
@pytest.mark.slow
@pytest.mark.parametrize("a, b, level", [
    ("물뽕", "술", "높음"), ("케타민", "술", "높음"), ("코카인", "술", "높음"), ("엑스터시", "phenelzine", "높음"),
    ("엑스터시", "ritonavir", "높음"), ("파퍼스", "비아그라", "높음"), ("LSD", "리튬", "높음"), ("헤로인", "자낙스", "높음"),
    ("트랑크", "헤로인", "높음"), ("필로폰", "phenelzine", "높음"), ("물뽕", "헤로인", "높음"),
    ("하르민", "프로작", "높음"),
    ("대마오일", "와파린", "주의"), ("크라톰", "옥시코돈", "주의"), ("엑스터시", "프로작", "주의"), ("합성대마", "술", "주의"),
])
def test_documented_dangerous_combinations(eng, a, b, level):
    A, B = eng.resolve(a)[0], eng.resolve(b)[0]
    rules = eng._pair_rules(A, B)
    assert rules and any(r["severe"] == (level == "높음") for r in rules), (a, b, rules)
    got, _ = eng.pair_level(A, B)
    assert got == level or (level == "주의" and got == "높음"), (a, b, got)  # labels may say more, never less


@needs_models
@pytest.mark.slow
def test_api_result_card_no_amounts_and_masked_audit(eng, monkeypatch, tmp_path):
    monkeypatch.setitem(api.E, "engine", eng)
    monkeypatch.setattr(settings, "logs_dir", tmp_path)
    monkeypatch.setattr(settings, "access_pin", "")
    monkeypatch.setattr(security, "USERS_FILE", tmp_path / "users.json")
    c = TestClient(api.app)
    r = c.post("/api/v1/predict", json={"drugs": [{"name": "물뽕", "dose": {"amount": 2000, "unit": "mg", "per_day": 2}},
                                                  {"name": "술"}], "patient": {"age": 25}})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["summary"]["verdict"] == "위험"
    ctl = res["controlled"]
    assert ctl and ctl[0]["key"] == "ghb" and "향정신성" in ctl[0]["law"] and ctl[0]["signs"]
    assert not (res.get("dosing") or {}).get("ingredients")  # the typed amount was not checked or echoed
    advice = res["summary"]["advice"]
    assert not any("용량" in a for a in advice) and any("1342" in a for a in advice), advice
    assert any(d["controlled"] for d in res["pathway"]["drugs"])  # body view: no repeated-dosing schedule
    log = (tmp_path / "audit").glob("*.jsonl")
    lines = [json.loads(x) for f in log for x in f.read_text(encoding="utf-8").splitlines()]
    assert lines and lines[-1]["drugs"] == ["[규제물질]", "술"]
    m = c.post("/api/v1/molecule", json={"name": "환각버섯"}).json()
    assert m["controlled"]["key"] == "psilocybin" and m["molecules"][0]["atoms"]
