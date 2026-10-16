"""Alert burden on REAL medicine combinations: how often does the program warn, and why?

The random sweep draws from a pool of interaction-prone drugs; it cannot say how noisy the program is for real patients.
This script takes the medicine lists of real FAERS reports (the newest quarters, every drug mapped, 2-10 drugs, with
age and sex), analyses each list for that reporter's age / sex / weight, and breaks every '주의' down by what caused it.
FAERS reports come from people who had an adverse event (sicker, more medicines than average): the burden here is an
upper bound for the general public.

    python scripts/alert_burden.py [N=1500]   -> logs/validation/alert_burden.md (+ .csv)

The same hidden-error checks as the random sweep run on every analysis.
"""

from __future__ import annotations

import collections
import importlib.util
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("OMNIPHARM_ONLINE_LOOKUP", "0")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from omnipharm.clinical import Patient  # noqa: E402
from omnipharm.engine import COMBO_X, Engine, _combo  # noqa: E402

_spec = importlib.util.spec_from_file_location("sweep_random", ROOT / "scripts" / "sweep_random.py")
sw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sw)

FIRST_QUARTER = 2025 * 4 + 2  # 2025Q3 on: the future-period test reports (quarter = year * 4 + q - 1)
CAUSES = ["문서·규칙 상호작용", "약리 기전(시험관·같은 작용)", "같은 성분 중복(경미)", "같은 계열 중복", "질환·연령·임신 주의",
          "용량 주의", "AI: 조합 신호", "AI: 환자 상태 신호"]


def regimens(eng: Engine, n: int, seed: int) -> list[tuple[list, dict]]:
    z = np.load(ROOT / "data" / "processed" / "faers_reports.npz")
    drugs, age, sex, wt, q = z["drugs"], z["age"], z["sex"], z["wt"], z["quarter"]
    nd = (drugs >= 0).sum(1)
    ok = np.where((z["src"] == 0) & (q >= FIRST_QUARTER) & (z["n_unmapped"] == 0) & (nd >= 2) & ~np.isnan(age)
                  & (age >= 1) & (age <= 100) & (sex > 0))[0]
    rng = random.Random(seed)
    out = []
    for i in rng.sample(list(ok), min(n, len(ok))):
        idx = list(dict.fromkeys(int(d) for d in drugs[i] if d >= 0))  # a drug reported twice is taken once
        if len(idx) < 2:
            continue
        ings = [eng._db(d) for d in idx]
        pt = {"age": int(age[i]), "sex": "M" if sex[i] == 1 else "F"}
        if not np.isnan(wt[i]) and 20 <= wt[i] <= 250:
            pt["weight_kg"] = float(wt[i])
        out.append((ings, pt))
    return out


def causes(r: dict) -> set[str]:
    """Why the result is not '비교적 안전' (every cause present)."""
    c = set()
    for p in r["pairs"]:
        if p["level"] == "낮음":
            continue
        kinds = {m["kind"] for m in p.get("mechanism", [])}
        documented = (p.get("dur") or any(e.get("context") != "co_therapy" for e in p.get("label_evidence", []))
                      or kinds & {"규칙", "CYP-FDA", "식약처허가", "식약처표시"})
        c.add("문서·규칙 상호작용" if documented else "약리 기전(시험관·같은 작용)")
    if any(d["minor"] for d in r["duplicates"]):
        c.add("같은 성분 중복(경미)")
    if any(not d["minor"] for d in r["duplicates"]) or r["class_duplicates"]:
        c.add("같은 계열 중복")
    if r["disease_alerts"]:
        c.add("질환·연령·임신 주의")
    if (r.get("dosing") or {}).get("alerts"):
        c.add("용량 주의")
    for e in r["events"]:
        if e.get("drives_verdict"):
            c.add("AI: 조합 신호" if _combo(e) >= COMBO_X else "AI: 환자 상태 신호")
    return c


def _kind(x: dict) -> str:
    """The kind of a 'high' reason, from its icon / wording."""
    t = x["text"]
    if x["icon"] == "💊":
        return "같은 성분 중복"
    if x["icon"] == "🔁":
        return "같은 계열 중복(" + t.split("(", 1)[1].split(")")[0][:30] + ")" if "(" in t else "같은 계열 중복"
    if x["icon"] == "🩺":
        return "질환·연령 금기: " + t.split(" 환자에게")[0][:25]
    if "DUR" in t:
        return "식약처 DUR 병용금기"
    if "함께 쓰지 말라고" in t:
        return "허가 문서 병용금기"
    if "중대한 상호작용" in t:
        return "허가 문서 중대 상호작용"
    if x["icon"] == "⚖️":
        return "용량 초과"
    return "기타(" + x["icon"] + ")"


def main(n: int = 1500, seed: int = 5) -> None:
    eng = Engine()
    regs = regimens(eng, n, seed)
    rows, probs, t0 = [], [], time.time()
    for ings, pt in regs:
        prods = [{"input": eng.ko(g), "ingredients": [g]} for g in ings]
        r = eng.predict(prods, Patient(**pt))
        s = r["summary"]
        cs = causes(r)
        rows.append({"n_drugs": len(ings), "age": pt["age"], "verdict": s["verdict"],
                     "action": (s.get("action") or {}).get("level", ""), "causes": " | ".join(sorted(cs)),
                     "high": " | ".join(sorted({_kind(x) for x in s["reasons"] if x["sev"] == "high"})),
                     "only_cause": next(iter(cs)) if len(cs) == 1 else "",
                     "drugs": " + ".join(eng.ko(g) for g in ings)})
        for p in sw.problems(r):
            probs.append({"drugs": rows[-1]["drugs"], "verdict": s["verdict"], "problem": p})
    df = pd.DataFrame(rows)
    out = ROOT / "logs" / "validation"
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "alert_burden.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(probs).to_csv(out / "alert_burden_problems.csv", index=False, encoding="utf-8-sig")

    def dist(d: pd.DataFrame) -> str:
        v = d["verdict"].value_counts(normalize=True)
        return " / ".join(f"{k} {v.get(k, 0):.0%}" for k in ("위험", "주의", "비교적 안전"))

    caution = df[df["verdict"] == "주의"]
    lines = [f"# 실제 처방 조합의 경고 부담 (FAERS {len(df):,}건, 2025Q3 이후 보고, 2~10제)", "",
             f"전체: {dist(df)}  ", f"오류 검사: 문제 {len(probs)}건 ({time.time() - t0:.0f}초)", "",
             "| 집단 | 건수 | 판정 분포 |", "|---|---|---|"]
    for label, d in [("2제", df[df.n_drugs == 2]), ("3~4제", df[df.n_drugs.between(3, 4)]),
                     ("5제 이상", df[df.n_drugs >= 5]), ("65세 미만", df[df.age < 65]), ("65세 이상", df[df.age >= 65])]:
        lines.append(f"| {label} | {len(d):,} | {dist(d)} |")
    if "action" in df and df["action"].astype(bool).any():
        lines += ["", "조치 수준: " + " / ".join(f"{k} {v:.0%}" for k, v in
                                               df["action"].value_counts(normalize=True).items())]
    lines += ["", f"## '주의' {len(caution):,}건의 원인 (여러 개일 수 있음)", "", "| 원인 | 포함 | 유일한 원인 |", "|---|---|---|"]
    for c in CAUSES:
        inc = caution["causes"].str.contains(c, regex=False).mean() if len(caution) else 0
        only = (caution["only_cause"] == c).mean() if len(caution) else 0
        lines.append(f"| {c} | {inc:.0%} | {only:.0%} |")
    danger = df[df["verdict"] == "위험"]
    lines += ["", f"## '위험' {len(danger):,}건의 근거 (여러 개일 수 있음)", "", "| 근거 | 포함 |", "|---|---|"]
    for k, v in collections.Counter(h for x in danger["high"] for h in x.split(" | ") if h).most_common(12):
        lines.append(f"| {k} | {v / max(len(danger), 1):.0%} |")
    if probs:
        lines += ["", "## 발견된 문제", ""] + [f"- {p['problem']} — {p['drugs'][:80]}" for p in probs[:30]]
    (out / "alert_burden.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:30]))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 1500)
