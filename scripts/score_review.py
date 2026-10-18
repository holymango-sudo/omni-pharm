"""Score the pharmacist review against the program's CURRENT verdicts (v2.18).

  python scripts/score_review.py                   answers saved by the /review page (data/review/answers_*.json)
  python scripts/score_review.py 약사A.xlsx 약사B.xlsx   filled copies of 약사_검수표_배포용.xlsx (or the original sheet)
  python scripts/score_review.py docs/validation/검수결과   every .xlsx in a folder (one per pharmacist)

Per reviewer: exact agreement, Cohen's kappa (unweighted / linear-weighted), sensitivity and positive predictive
value of '위험', confusion matrix. Between reviewers: Cohen's kappa for each pair and Fleiss' kappa for all of them
(how much pharmacists agree with each other is the ceiling for the program). '모름' answers are left out.
The program's verdicts are recomputed with the engine of this version, so the score belongs to this version.
-> logs/validation/review_score.json, review_disagreements.csv
"""

from __future__ import annotations

import itertools
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("OMNIPHARM_ONLINE_LOOKUP", "0")

import pandas as pd  # noqa: E402

from omnipharm import review  # noqa: E402

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


def fleiss(table: list[list[str]]) -> float:
    """Fleiss' kappa: rows = cases, each with the verdicts of the raters who judged it (>= 2 per case)."""
    rows = [r for r in table if len(r) >= 2]
    if not rows:
        return float("nan")
    p_j = {v: 0.0 for v in LEVELS}
    pis = []
    for r in rows:
        n = len(r)
        counts = {v: r.count(v) for v in LEVELS}
        for v in LEVELS:
            p_j[v] += counts[v] / n
        pis.append((sum(c * c for c in counts.values()) - n) / (n * (n - 1)))
    p_bar = sum(pis) / len(rows)
    pe = sum((x / len(rows)) ** 2 for x in p_j.values())
    return (p_bar - pe) / (1 - pe) if pe < 1 else 1.0


def program_verdicts() -> dict[int, str]:
    from omnipharm.clinical import Patient
    from omnipharm.engine import Engine

    eng = Engine()
    out = {}
    for r in review.raw_cases().itertuples():
        prods = [{"input": d, "ingredients": eng.resolve(d)} for d in str(r.약물).split(" + ")]
        v = eng.predict(prods, Patient(**review.REVIEW_PATIENTS[r.환자]))["summary"]["verdict"]
        out[int(r.번호)] = {"비교적 안전": "안전"}.get(v, v)
    return out


def score(a: list[str], b: list[str]) -> dict:
    tp = sum(x == "위험" and y == "위험" for x, y in zip(a, b, strict=True))
    return {"n": len(a), "agreement": round(sum(x == y for x, y in zip(a, b, strict=True)) / len(a), 3),
            "kappa": round(kappa(a, b, False), 3), "kappa_linear_weighted": round(kappa(a, b, True), 3),
            "danger_sensitivity": round(tp / max(a.count("위험"), 1), 3),
            "danger_ppv": round(tp / max(b.count("위험"), 1), 3),
            # {pharmacist's verdict: {program's verdict: cases}} (to_dict() alone would key by the program's verdict)
            "confusion(pharmacist->program)": pd.crosstab(pd.Series(a, name="약사"), pd.Series(b, name="프로그램"))
            .reindex(index=LEVELS, columns=LEVELS, fill_value=0).to_dict(orient="index")}


def _text(x) -> str:
    return "" if x is None or (isinstance(x, float) and x != x) else str(x).strip()


def read_sheet(path: Path) -> tuple[str, dict[str, dict]]:
    """A filled workbook -> (reviewer, {case number: {verdict, memo}}). The reviewer is the name written on the guide
    sheet ('검수자 이름'), else the file name. Verdicts are normalised ('위험 ', '비교적 안전'); anything else is dropped."""
    who = ""
    try:
        guide = pd.read_excel(path, sheet_name="안내", header=None)
        row = guide[guide[0].astype(str).str.strip() == "검수자 이름"]
        who = _text(row.iloc[0, 1]) if len(row) and guide.shape[1] > 1 else ""
    except (ValueError, KeyError):
        pass
    df = pd.read_excel(path, sheet_name="사례")
    col = next(c for c in df.columns if str(c).startswith("약사 판정"))
    memo_cols = [c for c in df.columns if str(c).startswith(("가장 중요한 문제", "약사 메모"))]
    ans = {}
    for _, r in df.iterrows():
        v = _text(r[col]).replace(" ", "")
        v = {"비교적안전": "안전"}.get(v, v)
        if v in review.VERDICTS and _text(r["번호"]):
            ans[str(int(r["번호"]))] = {"verdict": v, "memo": " / ".join(m for m in (_text(r[c]) for c in memo_cols) if m)}
    return who or Path(path).stem, ans


def _sheets(paths: list[str]) -> list[Path]:
    out = []
    for p in map(Path, paths):
        out += sorted(f for f in p.glob("*.xlsx") if not f.name.startswith("~$")) if p.is_dir() else [p]
    return out


def main(paths: list[str]) -> None:
    if paths:  # filled spreadsheets, one per pharmacist
        given = {}
        for f in _sheets(paths):
            who, ans = read_sheet(f)
            # the same pharmacist's files of different rounds are one reviewer; the same cases twice are two
            while who in given and set(given[who]) & set(ans):
                who += "_2"
            given.setdefault(who, {}).update(ans)
            print(f"{f.name}: {who} - {len(ans)}건 입력")
    else:
        given = review.all_answers()
    given = {k: {c: x for c, x in v.items() if x.get("verdict") in LEVELS} for k, v in given.items()}
    given = {k: v for k, v in given.items() if v}
    if not given:
        sys.exit("약사 판정이 입력된 사례가 없습니다 (/review 페이지 또는 검수표 엑셀).")
    prog = program_verdicts()
    res, rows = {}, []
    # each review round separately: a round reviewed before the rules were changed on it is a development set
    for rno in sorted({review.round_of(int(c)) for v in given.values() for c in v}):
        part = {who: {c: x for c, x in ans.items() if review.round_of(int(c)) == rno} for who, ans in given.items()}
        part = {k: v for k, v in part.items() if v}
        out_r = res[f"{rno}차"] = {"reviewers": {}, "between_reviewers": {}}
        for who, ans in part.items():
            ids = sorted(int(c) for c in ans)
            a, b = [ans[str(i)]["verdict"] for i in ids], [prog[i] for i in ids]
            out_r["reviewers"][who] = score(a, b)
            rows += [{"회차": rno, "검수자": who, "번호": i, "약사": x, "프로그램": y, "메모": ans[str(i)].get("memo", "")}
                     for i, x, y in zip(ids, a, b, strict=True) if x != y]
        for x, y in itertools.combinations(part, 2):
            common = sorted(set(part[x]) & set(part[y]), key=int)
            if len(common) >= 5:
                a, b = [part[x][c]["verdict"] for c in common], [part[y][c]["verdict"] for c in common]
                out_r["between_reviewers"][f"{x} vs {y}"] = {"n": len(common), "kappa": round(kappa(a, b, False), 3)}
        if len(part) >= 2:
            table = [[g[c]["verdict"] for g in part.values() if c in g] for c in {c for g in part.values() for c in g}]
            out_r["between_reviewers"]["fleiss_kappa(all)"] = round(fleiss(table), 3)
    out = ROOT / "logs" / "validation"
    out.mkdir(parents=True, exist_ok=True)
    (out / "review_score.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    pd.DataFrame(rows).to_csv(out / "review_disagreements.csv", index=False, encoding="utf-8-sig")
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
