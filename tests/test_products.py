"""Korean marketed products: name normalisation, product resolution, same-class duplication."""

from __future__ import annotations

import pytest

from omnipharm.clinical import Patient
from omnipharm.config import settings
from omnipharm.knowledge import drug_class
from omnipharm.pipeline.mfds import base_name


@pytest.mark.parametrize(("name", "base"), [
    ("알러샷연질캡슐(세티리진염산염)", "알러샷"),
    ("타이레놀정500밀리그람", "타이레놀"),
    ("콜대원코프에스시럽", "콜대원코프에스"),
    ("판콜에이내복액", "판콜에이"),
])
def test_base_name(name, base):
    assert base_name(name) == base


def test_drug_class():
    assert drug_class("pseudoephedrine") == "decongestant"
    assert drug_class("ing:methylephedrine") == "decongestant"
    assert drug_class("acetaminophen") is None


needs_db = pytest.mark.skipif(not (settings.processed_dir / "kr_products.parquet").exists(),
                              reason="run `pipeline.py mfds` first")


@pytest.fixture(scope="module")
def eng():
    from omnipharm.engine import Engine

    return Engine()


@needs_db
@pytest.mark.slow
def test_full_product_name_resolves_to_its_ingredients(eng):
    ings, info = eng.resolve_full("알러샷연질캡슐(세티리진염산염)")
    assert [g.key for g in ings] == ["cetirizine"] and info["product"]


@needs_db
@pytest.mark.slow
def test_ambiguous_brand_offers_candidates(eng):
    from omnipharm.engine import ResolveError

    with pytest.raises(ResolveError) as e:
        eng.resolve_full("콜대원")
    assert len(e.value.candidates) >= 2


needs_world = pytest.mark.skipif(not (settings.processed_dir / "world_products.parquet").exists(),
                                 reason="run `pipeline.py world` first")


@needs_world
@pytest.mark.slow
@pytest.mark.parametrize(("name", "expected"), [
    ("Doliprane", {"acetaminophen"}),        # France (BDPM, French INN paracétamol)
    ("Advil", {"ibuprofen"}),                 # US brand
    ("ロキソニン", {"loxoprofen"}),            # Japan (MHLW list + KEGG translation)
    ("カロナール", {"acetaminophen"}),
    ("ロキソプロフェンナトリウム水和物", {"loxoprofen"}),  # Japanese ingredient name typed directly
])
def test_foreign_products(eng, name, expected):
    ings, _ = eng.resolve_full(name)
    assert expected <= {g.key.removeprefix("ing:") for g in ings}


@needs_world
@pytest.mark.slow
def test_script_selects_country():
    from omnipharm.engine import script

    assert script("콜대원") == "KR" and script("ロキソニン") == "JP" and script("葛根湯") == "JP"
    assert script("NyQuil") == "LATIN"


@needs_db
@pytest.mark.slow
def test_two_cold_medicines_flag_duplication(eng):
    def prod(n):
        ings, _ = eng.resolve_full(n)
        return {"input": n, "ingredients": ings}

    r = eng.predict([prod("콜대원코프에스시럽"), prod("타이레놀")], Patient(age=40, sex="F"))
    assert r["duplicates"], "acetaminophen in both products"
    assert r["overall_level"] == "높음"
    # pairs inside the single combination product are not reported as interactions
    keys = {frozenset((p["a_key"], p["b_key"])) for p in r["pairs"]}
    assert frozenset(("guaifenesin", "pseudoephedrine")) not in keys
