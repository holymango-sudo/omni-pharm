"""처방전·약봉투 사진 입력 evaluation: the local vision LLM reads the photos of tests/fixtures/rx/eval (real product names,
invented patients) and every line is scored against truth.json.

    python scripts/eval_rx_photo.py        the evaluation set (report these numbers; Ollama must be running)
    python scripts/eval_rx_photo.py dev    the development set (tune the matching on this one only)
    python tests/fixtures/rx/make_eval.py  rebuilds the photo sets

Per medicine line of the document:
  exact          matched to that very product
  same_contents  matched to a product with the same ingredients (another strength / maker: same interactions)
  candidate      not chosen automatically, but offered among the candidates ('확인 필요' -> the user picks)
  missed
Also: precision of the automatic '확인됨' (a wrong 확인됨 is the dangerous error), lines that are not on the document,
dose (amount / times a day / days) of the matched lines, patient identifiers in the model's answer.
Writes logs/rx_eval/<time>.md + .json.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from omnipharm import rx_photo  # noqa: E402
from omnipharm.config import settings  # noqa: E402
from omnipharm.engine import Engine, ResolveError  # noqa: E402
from omnipharm.models import llm  # noqa: E402

SETS = ROOT / "tests" / "fixtures" / "rx"


def keys(eng: Engine, name: str | None) -> frozenset[str]:
    if not name:
        return frozenset()
    try:
        return frozenset(g.key for g in eng.resolve_full(name)[0])
    except ResolveError:
        return frozenset()


def score_doc(eng: Engine, doc: dict, data: dict, items: list[dict]) -> dict:
    truth = doc["medicines"]
    tkeys = [keys(eng, t["name"]) for t in truth]
    used, lines = set(), []
    for t, tk in zip(truth, tkeys, strict=True):
        best = None
        for k, it in enumerate(items):
            if k in used or it["status"] == "none":
                continue
            if it["name"] == t["name"]:
                best = (k, "exact")
                break
            if best is None and tk and keys(eng, it["name"]) == tk:
                best = (k, "same_contents")
        if best is None:  # offered among the candidates of a '확인 필요' line
            for k, it in enumerate(items):
                if k not in used and it["status"] == "check" and any(c["name"] == t["name"] for c in it["candidates"]):
                    best = (k, "candidate")
                    break
        row = {"truth": t["name"], "result": best[1] if best else "missed"}
        if best:
            k = best[0]
            used.add(k)
            it = items[k]
            row |= {"read_as": it["raw"], "status": it["status"], "matched": it["name"]}
            d = it.get("dose") or {}
            row["dose_ok"] = (abs((d.get("amount") or -1) - t["amount"]) < 1e-6 and d.get("unit") == t["unit"]
                              and abs((d.get("per_day") or -1) - t["times"]) < 1e-6 and d.get("days") == t["days"])
        lines.append(row)
    wrong_ok = [it["raw"] for k, it in enumerate(items) if it["status"] == "ok"
                and not any(r.get("matched") == it["name"] and r["result"] in ("exact", "same_contents") for r in lines)]
    extra = [it["raw"] for k, it in enumerate(items) if k not in used]
    text = json.dumps(data, ensure_ascii=False)
    return {"photo": doc["photo"], "layout": doc["layout"], "hard": doc["hard"], "lines": lines,
            "items": [{"raw": it["raw"], "status": it["status"], "name": it["name"], "score": it.get("score"),
                       "second": it["candidates"][1]["name"] if len(it["candidates"]) > 1 else None} for it in items],
            "auto_confirmed": sum(it["status"] == "ok" for it in items), "wrong_confirmed": wrong_ok,
            "extra_lines": extra, "identifier_leak": doc["patient"] in text or doc["rrn"].split("-")[0] in text}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    which = sys.argv[1] if len(sys.argv) > 1 else "eval"
    EVAL = SETS / which
    truth = json.loads((EVAL / "truth.json").read_text(encoding="utf-8"))
    eng = Engine()
    docs, t0 = [], time.time()
    for doc in truth:
        img = rx_photo.prepare_image(base64.b64encode((EVAL / doc["photo"]).read_bytes()).decode())
        t1 = time.time()
        data = asyncio.run(llm.vision_json(rx_photo.PROMPT, rx_photo.SCHEMA, img))
        if data is None:
            print("local vision model not running")
            return 2
        items = rx_photo.match_all(eng, data.get("medicines") or [])
        r = score_doc(eng, doc, data, items) | {"seconds": round(time.time() - t1, 1)}
        docs.append(r)
        res = [x["result"] for x in r["lines"]]
        print(f"{doc['photo']}: {res.count('exact')}+{res.count('same_contents')} 자동, {res.count('candidate')} 후보, "
              f"{res.count('missed')} 놓침 / {len(res)} · 잘못 확인 {len(r['wrong_confirmed'])} · {r['seconds']}s")
    lines = [x for d in docs for x in d["lines"]]
    n = len(lines)
    cnt = {k: sum(x["result"] == k for x in lines) for k in ("exact", "same_contents", "candidate", "missed")}
    auto = sum(d["auto_confirmed"] for d in docs)
    wrong = sum(len(d["wrong_confirmed"]) for d in docs)
    matched = [x for x in lines if x["result"] != "missed"]
    summary = {
        "set": which, "model": settings.llm_model, "documents": len(docs), "lines": n, **cnt,
        "found_rate": round((n - cnt["missed"]) / n, 3), "auto_correct_rate": round((cnt["exact"] + cnt["same_contents"]) / n, 3),
        "auto_confirmed": auto, "wrong_confirmed": wrong,
        "confirmed_precision": round(1 - wrong / auto, 3) if auto else None,
        "extra_lines": sum(len(d["extra_lines"]) for d in docs),
        "dose_correct": round(sum(bool(x.get("dose_ok")) for x in matched) / len(matched), 3) if matched else None,
        "identifier_leaks": sum(d["identifier_leak"] for d in docs),
        "by_condition": {k: round(sum(x["result"] != "missed" for d in docs if d["hard"] == hard for x in d["lines"])
                                  / max(1, sum(len(d["lines"]) for d in docs if d["hard"] == hard)), 3)
                         for k, hard in (("normal_photo", False), ("bad_photo", True))},
        "seconds_per_document": round(sum(d["seconds"] for d in docs) / len(docs), 1),
    }
    out = ROOT / "logs" / "rx_eval"
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M") + f"_{which}"
    (out / f"{stamp}.json").write_text(json.dumps({"summary": summary, "documents": docs}, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    md = [f"# 처방전·약봉투 사진 입력 평가 {time.strftime('%Y-%m-%d %H:%M')}", "", "```", json.dumps(summary, ensure_ascii=False,
                                                                                         indent=1), "```", ""]
    for d in docs:
        md.append(f"## {d['photo']} ({d['layout']}{', 나쁜 사진' if d['hard'] else ''}) — {d['seconds']}s")
        md += [f"- {x['truth']} → {x['result']}" + (f" ('{x['read_as']}' → {x['matched']}, {x['status']}, 용량 "
                                                     f"{'맞음' if x.get('dose_ok') else '다름'})" if x.get("read_as") else "")
               for x in d["lines"]]
        md += [f"- ⚠ 잘못 확인됨: {w}" for w in d["wrong_confirmed"]] + [f"- 문서에 없는 줄: {w}" for w in d["extra_lines"]]
        md.append("")
    (out / f"{stamp}.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"→ {out / (stamp + '.md')} ({time.time() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
