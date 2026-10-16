"""External validation against the official Korean DUR contraindicated-combination list (MFDS).

DUR is an independent source: the engine's other evidence (FDA labels, FDA CYP tables, clinical rules, AI) was
built without it. With the DUR layer switched OFF we measure how many DUR-contraindicated ingredient pairs the
rest of the system still flags (recall of '위험' and of '위험 or 주의'), and list the misses for review.

  python scripts/validate_external.py            -> logs/validation/dur_recall.json + dur_missed.csv

v2.18: the pairs are split into a DEVELOPMENT and a TEST half by their anchor drug (the member that appears in more
DUR pairs; md5 of its name decides), so that improving the detection of, say, domperidone pairs on the development
half cannot leak into the test half. Rules may only be improved by looking at dev misses (dur_missed_dev.csv); the
test half is reported before and after, never examined for tuning.
"""

from __future__ import annotations

import collections
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402

from omnipharm.clinical import Patient  # noqa: E402
from omnipharm.engine import Engine  # noqa: E402

OUT = ROOT / "logs" / "validation"
ADULT = dict(age=45, sex="M", weight_kg=70, height_cm=172, scr=0.9)


def split_of(pairs: list[dict]) -> dict[tuple[str, str], str]:
    """'dev' / 'test' per pair, by its anchor drug (stable: md5 of the name)."""
    freq = collections.Counter(x for p in pairs for x in (p["a"], p["b"]))
    out = {}
    for p in pairs:
        anchor = max((p["a"], p["b"]), key=lambda x: (freq[x], x))
        out[(p["a"], p["b"])] = "dev" if int(hashlib.md5(anchor.encode()).hexdigest(), 16) % 2 == 0 else "test"
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    eng = Engine()
    pairs = eng.dur.get("pairs", [])
    split = split_of(pairs)
    saved, eng.dur_pairs = eng.dur_pairs, {}  # DUR layer off: what does the rest of the system say?
    rows, t0 = [], time.time()
    for p in pairs:
        try:
            a, b = eng._ext_ingredient(p["a"]), eng._ext_ingredient(p["b"])
        except Exception:
            continue
        r = eng.predict([{"input": p["a_ko"] or a.name, "ingredients": [a]},
                         {"input": p["b_ko"] or b.name, "ingredients": [b]}], Patient(**ADULT))
        pr = r["pairs"][0] if r["pairs"] else {}
        rows.append({"a": p["a_ko"], "b": p["b_ko"], "a_key": p["a"], "b_key": p["b"], "dur_reason": p["content"],
                     "split": split[(p["a"], p["b"])],
                     "verdict": r["summary"]["verdict"], "pair_level": pr.get("level"),
                     "structureless": a.biologic or b.biologic,
                     "evidence": "; ".join(x["text"] for x in r["summary"]["reasons"][:2])})
    eng.dur_pairs = saved
    df = pd.DataFrame(rows)
    mol = df[~df["structureless"]]

    def rates(d: pd.DataFrame) -> dict:
        n = len(d)
        return {"n": n, "위험": round((d["verdict"] == "위험").mean(), 3),
                "위험_or_주의": round(d["verdict"].isin(["위험", "주의"]).mean(), 3),
                "pair_flagged": round(d["pair_level"].isin(["높음", "주의"]).mean(), 3)} if n else {"n": 0}

    res = {"source": "식약처 DUR 병용금기 (성분)", "measured": time.strftime("%Y-%m-%d %H:%M"),
           "all": rates(df), "both_structure_mapped": rates(mol),
           "dev": rates(mol[mol["split"] == "dev"]), "test": rates(mol[mol["split"] == "test"]),
           "seconds": round(time.time() - t0)}
    (OUT / "dur_recall.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    df[df["verdict"] != "위험"].to_csv(OUT / "dur_missed.csv", index=False, encoding="utf-8-sig")
    # only the development half may be looked at to improve the rules
    df[(df["verdict"] != "위험") & (df["split"] == "dev")].to_csv(OUT / "dur_missed_dev.csv", index=False,
                                                                 encoding="utf-8-sig")
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
