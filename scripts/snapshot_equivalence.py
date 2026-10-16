"""Behaviour snapshot for refactoring: analyse a fixed set of combinations and record what every part of the result
was, so a refactor (rules moved to JSON, engine.py split into modules) can be proven to change nothing.

    python scripts/snapshot_equivalence.py save [N=500]   -> logs/validation/snapshot_before.json
    python scripts/snapshot_equivalence.py compare        -> differences against that file (exit 1 if any)

The set: N random combinations (the random-sweep pool x the review patients) + N real FAERS medicine lists
(the alert-burden sample). Per analysis it stores the summary itself (verdict, headline, reasons, advice, action) and
a hash of every top-level part of the result, so a difference names the part that changed.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("OMNIPHARM_ONLINE_LOOKUP", "0")
# CPU only: GPU float sums are not reproducible between processes (the 6th digit of an AI probability changes)
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"  # ("" would delete the variable on Windows)

from omnipharm.clinical import Patient  # noqa: E402
from omnipharm.engine import Engine  # noqa: E402

FILE = ROOT / "logs" / "validation" / "snapshot_before.json"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _norm(x):
    """JSON-stable form: floats rounded (summation order may differ in the last bits), sets sorted."""
    if isinstance(x, float):
        return round(x, 6)
    if isinstance(x, dict):
        return {str(k): _norm(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_norm(v) for v in x]
    if isinstance(x, (set, frozenset)):
        return sorted(_norm(v) for v in x)
    if hasattr(x, "item"):  # numpy scalar
        return _norm(x.item())
    if hasattr(x, "__dict__"):  # dataclasses (Ingredient, Dose ...)
        return _norm(vars(x))
    return x


def _hash(x) -> str:
    return hashlib.md5(json.dumps(_norm(x), ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:12]


def cases(eng: Engine, n: int) -> list[tuple[str, list, dict]]:
    sw, ab = _load("sweep_random"), _load("alert_burden")
    rng = random.Random(23)
    pool, pts = sw.rs.POOL + sw.EXTRA, list(sw.rs.PATIENTS.values())
    out = []
    for i in range(n):
        names = rng.sample(pool, rng.choice([2, 2, 3, 3, 4, 5]))
        prods = []
        for x in names:
            try:
                prods.append({"input": x, "ingredients": eng.resolve(x)})
            except Exception:  # ambiguous product names: skipped (the UI asks the user)
                pass
        if len({g.key for p in prods for g in p["ingredients"]}) >= 2:
            out.append((" + ".join(names), prods, pts[i % len(pts)]))
    for ings, pt in ab.regimens(eng, n, seed=17):
        out.append((" + ".join(g.key for g in ings), [{"input": eng.ko(g), "ingredients": [g]} for g in ings], pt))
    return out


def run(eng: Engine, n: int) -> list[dict]:
    rows = []
    for label, prods, pt in cases(eng, n):
        r = eng.predict(prods, Patient(**pt))
        s = r["summary"]
        rows.append({"case": label, "patient": pt, "verdict": s["verdict"], "headline": s["headline"],
                     "reasons": [x["text"] for x in s["reasons"]], "advice": s["advice"],
                     "action": (s.get("action") or {}).get("level", ""),
                     "parts": {k: _hash(v) for k, v in sorted(r.items())}})
    return rows


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "compare"
    t0 = time.time()
    eng = Engine()
    if mode == "save":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 500
        rows = run(eng, n)
        FILE.parent.mkdir(parents=True, exist_ok=True)
        FILE.write_text(json.dumps({"n": n, "rows": rows}, ensure_ascii=False, indent=0), encoding="utf-8")
        print(f"saved {len(rows)} analyses in {time.time() - t0:.0f}s -> {FILE}")
        return 0
    old = json.loads(FILE.read_text(encoding="utf-8"))
    new = run(eng, old["n"])
    if len(new) != len(old["rows"]):
        print(f"case count differs: {len(old['rows'])} -> {len(new)}")
        return 1
    diffs = []
    for a, b in zip(old["rows"], new):
        if a["case"] != b["case"]:
            diffs.append((a["case"], "different case"))
            continue
        changed = [k for k in ("verdict", "headline", "reasons", "advice", "action") if a[k] != b[k]]
        changed += [f"part:{k}" for k in sorted(set(a["parts"]) | set(b["parts"]))
                    if a["parts"].get(k) != b["parts"].get(k)]
        if changed:
            diffs.append((a["case"], ", ".join(changed)))
    print(f"{len(new)} analyses compared in {time.time() - t0:.0f}s: {len(diffs)} differ")
    for c, what in diffs[:40]:
        print(f"  {c[:70]} -> {what}")
    return 1 if diffs else 0


if __name__ == "__main__":
    sys.exit(main())
