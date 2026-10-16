"""v2.18 blinded pharmacist review: answers are stored per reviewer, the page never gets the program's verdict, and the
agreement statistics are right on small known examples."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from omnipharm import review

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("score_review", ROOT / "scripts" / "score_review.py")
sr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sr)


def test_answers_are_saved_per_reviewer(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "review_dir", lambda: tmp_path)
    assert review.save_answer("약사 A", 3, "위험", "와파린 + 아미오다론")["answered"] == 1
    review.save_answer("약사 A", 3, "주의")  # changed mind: the latest answer counts
    review.save_answer("kim_01", 3, "안전")
    assert review.answers("약사 A")["3"]["verdict"] == "주의"
    assert set(review.all_answers()) == {"약사_A", "kim_01"}
    with pytest.raises(ValueError):
        review.save_answer("약사 A", 4, "아마도")
    with pytest.raises(ValueError):
        review.reviewer_id("../../")


def test_agreement_statistics():
    a = ["위험", "주의", "안전", "위험", "주의", "안전"]
    assert sr.kappa(a, a, False) == 1.0
    assert sr.kappa(a, list(reversed(a)), False) < 0.5
    s = sr.score(a, ["위험", "위험", "안전", "주의", "주의", "안전"])
    assert s["danger_sensitivity"] == 0.5 and s["danger_ppv"] == 0.5 and s["agreement"] == round(4 / 6, 3)
    # Fleiss: three raters who always agree -> 1; random-looking disagreement -> low
    assert sr.fleiss([["위험"] * 3, ["안전"] * 3, ["주의"] * 3]) == 1.0
    assert sr.fleiss([["위험", "안전", "주의"], ["안전", "주의", "위험"], ["주의", "위험", "안전"]]) < 0


def test_review_page_never_receives_the_program_verdict():
    html = (ROOT / "src" / "omnipharm" / "static" / "review.html").read_text(encoding="utf-8")
    assert "/api/v1/review/cases" in html and "프로그램 판정" not in html and "/api/v1/predict" not in html
