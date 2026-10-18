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
    # rows = the pharmacist's verdict: of the 2 '위험' answers, the program said 위험 once and 주의 once
    assert s["confusion(pharmacist->program)"]["위험"] == {"안전": 0, "주의": 1, "위험": 1}
    # Fleiss: three raters who always agree -> 1; random-looking disagreement -> low
    assert sr.fleiss([["위험"] * 3, ["안전"] * 3, ["주의"] * 3]) == 1.0
    assert sr.fleiss([["위험", "안전", "주의"], ["안전", "주의", "위험"], ["주의", "위험", "안전"]]) < 0


def test_filled_workbook_is_read_with_the_reviewer_name(tmp_path):
    """A pharmacist's copy of 약사_검수표_배포용.xlsx: the name on the guide sheet, normalised verdicts, both memo
    columns; blank, unknown and Excel lock files are skipped; a folder reads every workbook in it."""
    from openpyxl import Workbook

    wb = Workbook()
    g = wb.active
    g.title = "안내"
    g.append(["OMNI-PHARM 약 조합 검수표", None])
    g.append([None, None])
    g.append(["검수자 이름", "김약사"])
    ws = wb.create_sheet("사례")
    ws.append(["번호", "환자", "약물 (함께 쓰는 약)", "약사 판정", "가장 중요한 문제 (선택)", "약사 메모 (선택)"])
    ws.append([1, "건강한 성인", "와파린\n아미오다론", "위험 ", "INR 상승", None])
    ws.append([2, "건강한 성인", "아목시실린", "비교적 안전", None, None])
    ws.append([3, "건강한 성인", "세티리진", None, None, None])
    ws.append([4, "건강한 성인", "이부프로펜", "글쎄요", None, None])
    ws.append([5, "건강한 성인", "타이레놀", "모름", None, "자료 부족"])
    wb.save(tmp_path / "a.xlsx")
    (tmp_path / "~$a.xlsx").write_bytes(b"lock")
    who, ans = sr.read_sheet(tmp_path / "a.xlsx")
    assert who == "김약사"
    assert ans == {"1": {"verdict": "위험", "memo": "INR 상승"}, "2": {"verdict": "안전", "memo": ""},
                   "5": {"verdict": "모름", "memo": "자료 부족"}}
    assert sr._sheets([str(tmp_path)]) == [tmp_path / "a.xlsx"]


def test_rounds_have_their_own_case_numbers():
    """Round 2 (#201-) never reuses a round-1 number or combination, and every patient of a case list has a profile."""
    assert review.round_of(1) == 1 and review.round_of(200) == 1 and review.round_of(201) == 2
    if len(review.rounds()) < 2:
        pytest.skip("round 2 case list not drawn")
    r1, r2 = review.raw_cases(1), review.raw_cases(2)
    assert r1["번호"].max() < r2["번호"].min() == review.ROUND_START[2]
    assert not {frozenset(x.split(" + ")) for x in r1["약물"]} & {frozenset(x.split(" + ")) for x in r2["약물"]}
    assert set(r1["환자"]) <= set(review.REVIEW_PATIENTS_1) and set(r2["환자"]) <= set(review.REVIEW_PATIENTS_2)
    assert len(review.raw_cases()) == len(r1) + len(r2)


def test_review_page_never_receives_the_program_verdict():
    html = (ROOT / "src" / "omnipharm" / "static" / "review.html").read_text(encoding="utf-8")
    assert "/api/v1/review/cases" in html and "프로그램 판정" not in html and "/api/v1/predict" not in html
