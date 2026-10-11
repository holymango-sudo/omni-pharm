"""Engine latency: one analysis with 2 / 5 / 10 / 15 drugs (the API maximum), warm cache.

  python scripts/bench_engine.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("OMNIPHARM_ONLINE_LOOKUP", "0")

from omnipharm.clinical import Patient
from omnipharm.engine import Engine

DRUGS = ["warfarin", "amiodarone", "metformin", "atorvastatin", "amlodipine", "lisinopril", "omeprazole",
         "sertraline", "tramadol", "acetaminophen", "ibuprofen", "levothyroxine", "furosemide", "spironolactone",
         "clarithromycin"]


def main() -> None:
    t0 = time.time()
    eng = Engine()
    print(f"engine start {time.time() - t0:.1f}s")
    pt = Patient(age=75, sex="F", weight_kg=55, height_cm=155, scr=1.4, conditions=["heart_failure"])
    for n in (2, 5, 10, 15):
        prods = [{"input": d, "ingredients": eng.resolve(d)} for d in DRUGS[:n]]
        eng.predict(prods, pt)  # warm
        ts = []
        for _ in range(3):
            t = time.time()
            eng.predict(prods, pt)
            ts.append(time.time() - t)
        print(f"{n:2d} drugs ({n * (n - 1) // 2:3d} pairs): {min(ts) * 1000:7.0f} ms (best of 3)")


if __name__ == "__main__":
    main()
