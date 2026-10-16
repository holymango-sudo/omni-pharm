"""Blinded pharmacist review (v2.18): the cases of docs/validation/약사_검수표.xlsx shown one at a time on /review
(phone or PC), the reviewer's verdict saved per reviewer in data/review/, and the agreement with the program scored
against the CURRENT engine (scripts/score_review.py): Cohen's kappa (unweighted / linear-weighted), sensitivity and
positive predictive value for '위험', and the agreement between reviewers (Cohen for two, Fleiss for more).

The program's verdict is never sent to the review page (blinded); it is computed only when scoring.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import pandas as pd

from omnipharm.config import PROJECT_ROOT, settings

SHEET = PROJECT_ROOT / "docs" / "validation" / "약사_검수표.xlsx"
VERDICTS = ("위험", "주의", "안전", "모름")
# the patient profiles of the review cases (scripts/review_sheet.py writes the sheet with these names)
REVIEW_PATIENTS = {
    "건강한 성인 (45세 남, 72kg)": dict(age=45, sex="M", weight_kg=72, height_cm=174, scr=0.9),
    "고령·신장저하 (82세 여, 48kg, Cr 1.6)": dict(age=82, sex="F", weight_kg=48, height_cm=150, scr=1.6),
    "임신 중 (31세 여)": dict(age=31, sex="F", weight_kg=60, height_cm=162, scr=0.7, conditions=["pregnancy"]),
    "간경변 (63세 남)": dict(age=63, sex="M", weight_kg=68, height_cm=170, scr=1.0, ast=110, alt=80,
                         total_bilirubin=2.8, conditions=["hepatic"]),
    "심부전·당뇨 (70세 남)": dict(age=70, sex="M", weight_kg=80, height_cm=168, scr=1.3,
                            conditions=["heart_failure", "diabetes"]),
    "소아 (9세, 30kg)": dict(age=9, sex="M", weight_kg=30, height_cm=133, scr=0.5),
}
_lock = threading.Lock()


def review_dir() -> Path:
    d = settings.data_dir / "review"
    d.mkdir(parents=True, exist_ok=True)
    return d


def reviewer_id(name: str) -> str:
    """A safe file name for a reviewer ('약사 A', 'kim_01' ...)."""
    rid = re.sub(r"[^0-9A-Za-z가-힣_-]", "", (name or "").replace(" ", "_"))[:30]
    if not rid:
        raise ValueError("검수자 이름(또는 코드)을 입력하세요.")
    return rid


def raw_cases() -> pd.DataFrame:
    return pd.read_excel(SHEET, sheet_name="사례")[["번호", "환자", "약물"]]


def cases(eng) -> list[dict]:
    """Cases for the review page: patient and medicines with Korean names - no program verdict."""
    out = []
    for r in raw_cases().itertuples():
        names = []
        for d in str(r.약물).split(" + "):
            try:
                names.append(", ".join(eng.ko(g) for g in eng.resolve(d)))
            except Exception:
                names.append(d)
        out.append({"id": int(r.번호), "patient": r.환자, "drugs": names})
    return out


def _file(rid: str) -> Path:
    return review_dir() / f"answers_{rid}.json"


def answers(name: str) -> dict[str, dict]:
    f = _file(reviewer_id(name))
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def save_answer(name: str, case_id: int, verdict: str, memo: str = "") -> dict:
    if verdict not in VERDICTS:
        raise ValueError(f"판정은 {', '.join(VERDICTS)} 중 하나여야 합니다.")
    rid = reviewer_id(name)
    with _lock:
        a = answers(rid)
        a[str(int(case_id))] = {"verdict": verdict, "memo": (memo or "")[:500], "at": time.strftime("%Y-%m-%d %H:%M")}
        _file(rid).write_text(json.dumps(a, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"reviewer": rid, "answered": len(a)}


def all_answers() -> dict[str, dict[str, dict]]:
    return {f.stem.removeprefix("answers_"): json.loads(f.read_text(encoding="utf-8"))
            for f in sorted(review_dir().glob("answers_*.json"))}
