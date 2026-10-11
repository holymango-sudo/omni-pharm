"""The clinical combination audit (scripts/audit_combos.py) as regression tests: 109 reference combinations,
60 held-out combinations and 18 patient scenarios with established answers."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from omnipharm.clinical import Patient
from omnipharm.config import settings

pytestmark = pytest.mark.slow
if not (settings.artifacts_dir / "v2" / "pair_metrics.json").exists():
    pytest.skip("v2 artifacts not built", allow_module_level=True)

_spec = importlib.util.spec_from_file_location("audit_combos", Path(__file__).parents[1] / "scripts" / "audit_combos.py")
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)


@pytest.fixture(scope="module")
def eng():
    from omnipharm.engine import Engine

    return Engine()


def _run(eng, cases):
    wrong, problems = [], []
    for cat, names, expected in cases:
        r = eng.predict([{"input": n, "ingredients": eng.resolve(n)} for n in names], Patient(**audit.ADULT))
        v = r["summary"]["verdict"]
        if v not in expected:
            wrong.append((cat, names, v))
        problems += audit.message_problems(r)
    return wrong, problems


def test_reference_combinations(eng):
    wrong, problems = _run(eng, audit.CASES)
    assert not wrong, wrong
    assert not problems, problems


def test_held_out_combinations(eng):
    wrong, problems = _run(eng, audit.HOLDOUT)
    assert len(wrong) <= 3, wrong  # >= 95 %: the held-out set is not tuned to 100 %
    assert not problems, problems


def test_food_drink_smoking(eng):
    wrong, problems = _run(eng, audit.FOOD_CASES)
    assert not wrong, wrong
    assert not problems, problems


def test_patient_scenarios(eng):
    wrong = []
    for desc, names, pt, expected in audit.PATIENT_CASES:
        r = eng.predict([{"input": n, "ingredients": eng.resolve(n)} for n in names], Patient(**{**audit.ADULT, **pt}))
        if r["summary"]["verdict"] not in expected:
            wrong.append((desc, r["summary"]["verdict"]))
    assert not wrong, wrong
