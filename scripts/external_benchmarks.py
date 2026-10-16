"""External benchmarks that the developers did not write (v2.18) -> docs/validation/external_validation.md

  (a) MFDS DUR contraindicated pairs, DUR layer OFF, TEST half (scripts/validate_external.py: never used for tuning)
  (b) AGS Beers Criteria, Table 5 'drug-drug interactions to avoid in older adults' (tests/fixtures/external/
      beers_ddi.json - entered from the publication, to be checked against the original)
  (c) negative controls: medicine pairs whose FDA label says there is NO clinically meaningful interaction, found in
      real FAERS medicine lists (scripts/alert_burden.py) - how often does the program still warn about the pair?

    python scripts/external_benchmarks.py      (run validate_external.py first for (a))
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("OMNIPHARM_ONLINE_LOOKUP", "0")

from omnipharm.clinical import Patient  # noqa: E402
from omnipharm.engine import Engine, _own  # noqa: E402
from omnipharm.pipeline.stats import wilson  # noqa: E402

_spec = importlib.util.spec_from_file_location("alert_burden", ROOT / "scripts" / "alert_burden.py")
ab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ab)
OUT = ROOT / "docs" / "validation" / "external_validation.md"


def pct(k: int, n: int) -> str:
    lo, hi = wilson(k, n)
    return f"{k}/{n} = {k / n:.0%} (95% CI {lo:.0%}–{hi:.0%})" if n else "-"


def beers(eng: Engine) -> tuple[list[str], dict]:
    spec = json.loads((ROOT / "tests" / "fixtures" / "external" / "beers_ddi.json").read_text(encoding="utf-8"))
    rows, warn, danger = [], 0, 0
    for c in spec["cases"]:
        prods = [{"input": d, "ingredients": eng.resolve(d)} for d in c["drugs"]]
        r = eng.predict(prods, Patient(**spec["patient"]))
        v = r["summary"]["verdict"]
        warn += v in ("위험", "주의")
        danger += v == "위험"
        why = next((x["text"] for x in r["summary"]["reasons"] if x["sev"] in ("high", "mid")), "")
        rows.append(f"| {' + '.join(c['drugs'])} | {c['class']} | {c['risk']} | {v} | {why[:90]} |")
    n = len(spec["cases"])
    return ([f"## (b) AGS Beers 기준 — 노인에게 피해야 할 약물 상호작용 ({n}개 조합, 75세 여성)", "",
             f"출처: {spec['source']}", "", f"**주의 이상 {pct(warn, n)}, 위험 {pct(danger, n)}**", "",
             "> " + spec["note"], "", "| 조합 | 분류 | 위험 | 판정 | 첫 번째 이유 |", "|---|---|---|---|---|", *rows],
            {"n": n, "warn": warn, "danger": danger})


def negative_controls(eng: Engine, n_regimens: int = 3000) -> tuple[list[str], dict]:
    """Pairs whose label evidence says 'no clinically meaningful interaction' and nothing else."""
    seen, flagged, examples = {}, 0, []
    for ings, pt in ab.regimens(eng, n_regimens, seed=9):
        r = eng.predict([{"input": eng.ko(g), "ingredients": [g]} for g in ings], Patient(**pt))
        for p in r["pairs"]:
            key = frozenset((p["a_key"], p["b_key"]))
            if key in seen or not p["label_evidence"]:
                continue
            ctx = [e.get("context") for e in p["label_evidence"]]
            if "no_interaction" in ctx and not _own(p["label_evidence"]) and not p.get("dur"):
                seen[key] = p["level"]
                if p["level"] != "낮음":
                    flagged += 1
                    kinds = sorted({m["kind"] for m in p.get("mechanism", [])})
                    examples.append(f"- {p['a']} + {p['b']}: {p['level']} (근거: {', '.join(kinds) or '없음'})")
    n = len(seen)
    return ([f"## (c) 음성 대조군 — 허가 문서에 '임상적으로 의미 있는 상호작용 없음'인 쌍 ({n}쌍)", "",
             f"실제 FAERS 처방 조합 {n_regimens:,}건에서 찾은, 허가 문서가 상호작용이 없다고 적은 약 쌍(다른 문서 근거 없음).",
             f"**거짓 경고(주의 이상) {pct(flagged, n)}** — 남은 경고는 규칙·약리 기전 근거에서 온 것입니다.", "",
             *examples[:20]], {"n": n, "false_alerts": flagged})


def main() -> None:
    t0 = time.time()
    eng = Engine()
    lines = ["# 외부 기준 검증 (개발 측이 만들지 않은 기준)", "", f"측정: {time.strftime('%Y-%m-%d %H:%M')}", ""]
    dur = json.loads((ROOT / "logs" / "validation" / "dur_recall.json").read_text(encoding="utf-8"))
    t = dur.get("test", {})
    lines += ["## (a) 식약처 DUR 병용금기 — DUR 끈 상태, 시험(test) 절반", "",
              "DUR 쌍을 중심 약물 단위로 개발/시험 절반으로 나누고, 규칙 보완은 개발 절반의 놓친 쌍만 보고 했습니다. 시험 절반은 "
              "보완 전후 성적만 잽니다(scripts/validate_external.py).", "",
              f"**시험 절반 {t.get('n')}쌍: 위험 {t.get('위험', 0):.1%}, 위험 또는 주의 {t.get('위험_or_주의', 0):.1%}** "
              "(보완 전 51.5% / 74.2%). 정상 운영(DUR 켬)에서는 100%.", ""]
    b_lines, b = beers(eng)
    n_lines, neg = negative_controls(eng)
    lines += [*b_lines, "", *n_lines, "",
              "## 한계", "",
              "- (b)는 개발 측이 논문 표를 옮겨 적은 것이라 원문 대조가 필요합니다. 대표 약 1~2개씩만 넣었습니다.",
              "- 진짜 외부 검증은 약사 검수(docs/validation/약사검수_안내.md)와 상용 상호작용 DB 대조이며, 아직 하지 않았습니다.",
              f"- 소요 {time.time() - t0:.0f}초."]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"beers": b, "negative": neg, "dur_test": t}, ensure_ascii=False))


if __name__ == "__main__":
    main()
