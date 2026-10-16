"""v2.18: the Korean text parsers against a corpus of real label / product / question wording (tests/fixtures/parsers/
corpus.json). Each parser must get every case right; a failure prints the text, what was expected and what came out.
The cases were written by the developers after reading each text, not by a pharmacist (the corpus says so)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnipharm.config import settings
from omnipharm.pipeline.dur import min_age
from omnipharm.pipeline.kr_labels import avoid_with
from omnipharm.pipeline.supplements import declared_ingredients, medicines_to_watch
from omnipharm.rx_photo import parse_dose

CORPUS = json.loads((Path(__file__).parent / "fixtures" / "parsers" / "corpus.json").read_text(encoding="utf-8"))


def _misses(cases, fn, key="expected", compare=lambda a, b: a == b):
    return [(c, fn(c)) for c in cases if not compare(fn(c), c[key])]


def _report(name: str, cases: list, misses: list) -> None:
    assert not misses, f"{name}: {len(cases) - len(misses)}/{len(cases)} — " + " | ".join(
        f"{json.dumps({k: v for k, v in c.items() if k != 'expected'}, ensure_ascii=False)[:90]} → {got!r} "
        f"(expected {c['expected']!r})" for c, got in misses[:5])


def test_label_do_not_take_with_lists():
    cases = CORPUS["avoid_with"]
    _report("avoid_with", cases, _misses(cases, lambda c: avoid_with(c["text"])))


def test_label_minimum_age():
    cases = CORPUS["min_age"]
    _report("min_age", cases, _misses(cases, lambda c: min_age(c["usage"], c["cautions"])))


def test_health_food_ingredients_and_medicines_to_watch():
    ing = CORPUS["hff_ingredients"]
    _report("declared_ingredients", ing, _misses(ing, lambda c: declared_ingredients(c["product"]),
                                                 compare=lambda a, b: sorted(a) == sorted(b)))
    watch = CORPUS["hff_watch"]
    _report("medicines_to_watch", watch, _misses(watch, lambda c: medicines_to_watch(c["text"])))


def test_prescription_doses():
    cases = CORPUS["doses"]

    def run(c):
        d = parse_dose(c["item"])
        return None if d is None else [d["amount"], d["unit"], d["per_day"], d["days"], d["prn"]]
    _report("parse_dose", cases, _misses(cases, run))


@pytest.mark.slow
@pytest.mark.skipif(not (settings.artifacts_dir / "v2" / "pair_metrics.json").exists(), reason="v2 artifacts not built")
def test_chat_question_items():
    from omnipharm.assistant import find_items
    from omnipharm.engine import Engine

    eng = Engine()
    cases = CORPUS["chat_items"]
    _report("find_items", cases, _misses(cases, lambda c: find_items(eng, c["q"])))


def test_corpus_size():
    assert sum(len(v) for k, v in CORPUS.items() if isinstance(v, list)) >= 100
