"""Score a filled pharmacist review sheet (scripts/review_sheet.py) against the program's verdicts.

  python scripts/score_review.py docs/validation/약사_검수표.xlsx
-> exact agreement, Cohen's kappa (unweighted / linear-weighted), sensitivity & PPV for '위험',
   confusion matrix; written to logs/validation/review_score.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
LEVELS = ["안전", "주의", "위험"]


def kappa(a: list[str], b: list[str], weighted: bool) -> float:
    k = len(LEVELS)
    idx = {v: i for i, v in enumerate(LEVELS)}
    n = len(a)
    obs = [[0] * k for _ in range(k)]
    for x, y in zip(a, b, strict=True):
        obs[idx[x]][idx[y]] += 1
    ra = [sum(r) / n for r in obs]
    rb = [sum(obs[i][j] for i in range(k)) / n for j in range(k)]
    w = [[(abs(i - j) / (k - 1) if weighted else float(i != j)) for j in range(k)] for i in range(k)]
    po = sum(w[i][j] * obs[i][j] / n for i in range(k) for j in range(k))
    pe = sum(w[i][j] * ra[i] * rb[j] for i in range(k) for j in range(k))
    return 1 - po / pe if pe else 1.0


def main(path: str) -> None:
    cases = pd.read_excel(path, sheet_name="사례")
    prog = pd.read_excel(path, sheet_name="프로그램 결과")
    df = cases.merge(prog, on="번호")
    col = next(c for c in df.columns if c.startswith("약사 판정"))
    df = df[df[col].isin(LEVELS)]
    if df.empty:
        sys.exit("약사 판정이 입력된 사례가 없습니다.")
    a, b = df[col].tolist(), df["프로그램 판정"].tolist()
    tp = sum(x == "위험" and y == "위험" for x, y in zip(a, b, strict=True))
    res = {"n": len(df), "agreement": round(sum(x == y for x, y in zip(a, b, strict=True)) / len(df), 3),
           "kappa": round(kappa(a, b, False), 3), "kappa_linear_weighted": round(kappa(a, b, True), 3),
           "danger_sensitivity": round(tp / max(a.count("위험"), 1), 3),
           "danger_ppv": round(tp / max(b.count("위험"), 1), 3),
           "confusion(pharmacist->program)": pd.crosstab(pd.Series(a, name="약사"), pd.Series(b, name="프로그램"))
           .reindex(index=LEVELS, columns=LEVELS, fill_value=0).to_dict()}
    out = ROOT / "logs" / "validation"
    out.mkdir(parents=True, exist_ok=True)
    (out / "review_score.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    disagree = df[[x != y for x, y in zip(a, b, strict=True)]]
    disagree.to_csv(out / "review_disagreements.csv", index=False, encoding="utf-8-sig")
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "docs" / "validation" / "약사_검수표.xlsx"))
