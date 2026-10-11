"""Random sweep: many random combinations x patient profiles. Finds crashes and inconsistent messages that the
curated audit sets cannot (contradictory wording, a 'high' reason under a safe verdict, empty reasons for a
danger verdict, alarming vitamin text, label excerpts that do not name the other drug ...).

  python scripts/sweep_random.py [N=1000]   -> logs/validation/sweep_problems.csv
"""

from __future__ import annotations

import collections
import importlib.util
import os
import random
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("OMNIPHARM_ONLINE_LOOKUP", "0")

import pandas as pd  # noqa: E402

from omnipharm.clinical import Patient  # noqa: E402
from omnipharm.engine import Engine  # noqa: E402

_spec = importlib.util.spec_from_file_location("review_sheet", ROOT / "scripts" / "review_sheet.py")
rs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rs)
_spec2 = importlib.util.spec_from_file_location("audit_combos", ROOT / "scripts" / "audit_combos.py")
ac = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(ac)

EXTRA = ["콜대원코프에스시럽", "판콜에이내복액", "게보린정", "박카스디액", "정로환에프정", "아로나민골드정", "탁센연질캡슐",
         "위고비", "보톡스", "휴미라", "인사돌플러스정", "광동쌍화탕", "멜라토닌", "감초", "은행잎", "홍삼", "칼슘", "철분",
         "Lipitor", "Xarelto", "Keytruda", "ozempic", "Tylenol", "Advil", "nyquil"]


def problems(r: dict) -> list[str]:
    s, out = r["summary"], list(ac.message_problems(r))
    if s["verdict"] == "위험" and not any(x["sev"] == "high" for x in s["reasons"]):
        out.append("위험 판정인데 '높음' 이유 없음")
    if s["verdict"] == "주의" and not any(x["sev"] in ("high", "mid") for x in s["reasons"]):
        out.append("주의 판정인데 이유 없음")
    doc = any(p["level"] != "낮음" for p in r["pairs"])
    if doc and any("이것만으로 상호작용이라고 보지는" in x["text"] for x in s["reasons"]):
        out.append("문서화된 상호작용과 모순되는 AI 문구")
    if not s["advice"]:
        out.append("권고 없음")
    j = r.get("pathway")
    if j is None:
        out.append("인체 이동 경로 생성 실패")
    else:  # the body view must agree with the verdict (red organ <=> '위험')
        probs = next(x for x in j["stages"] if x["id"] == "problem")["problems"]
        if (s["verdict"] == "위험") != any(p["sev"] == "high" for p in probs):
            out.append("인체 화면의 위험 장기와 판정 불일치")
        if s["verdict"] != "비교적 안전" and not probs:
            out.append("주의·위험인데 인체 화면에 문제 부위 없음")
    return out


def main(n: int = 1000, seed: int = 11) -> None:
    rng = random.Random(seed)
    eng = Engine()
    pool = rs.POOL + EXTRA
    pts = list(rs.PATIENTS.values())
    rows, crashes, cnt, t0 = [], 0, collections.Counter(), time.time()
    for i in range(n):
        names = rng.sample(pool, rng.choice([2, 2, 3, 3, 4, 5]))
        pt = pts[i % len(pts)]
        try:
            prods = []
            for x in names:
                try:
                    prods.append({"input": x, "ingredients": eng.resolve(x)})
                except Exception:  # ambiguous product names: skipped here, the UI asks the user
                    pass
            if len({g.key for p in prods for g in p["ingredients"]}) < 2:
                continue
            r = eng.predict(prods, Patient(**pt))
        except Exception:
            crashes += 1
            rows.append({"drugs": " + ".join(names), "problem": "CRASH " + traceback.format_exc()[-300:]})
            continue
        cnt[r["summary"]["verdict"]] += 1
        for p in problems(r):
            rows.append({"drugs": " + ".join(names), "verdict": r["summary"]["verdict"], "problem": p})
    out = ROOT / "logs" / "validation"
    out.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    df.to_csv(out / "sweep_problems.csv", index=False, encoding="utf-8-sig")
    print(f"{sum(cnt.values())} analyses in {time.time() - t0:.0f}s | verdicts {dict(cnt)} | crashes {crashes}")
    if len(df):
        print(df["problem"].str.slice(0, 60).value_counts().head(15).to_string())


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 1000)
