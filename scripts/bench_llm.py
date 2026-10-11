"""Benchmark local Ollama models for the OMNI-PHARM report on THIS machine (GPU fit, speed, faithfulness).

  python scripts/bench_llm.py qwen3:8b gemma4:12b ...

For each model and each clinical case: VRAM share, load time, time to first token, tokens/s, and automatic
quality checks on the report: Korean-only output, the 4 required sections, the overall verdict stated, and
numbers that do not occur in the fact sheet (likely invented). Reports are written to logs/llm_bench/ for
reading, the summary to logs/llm_bench/summary.json.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from omnipharm.clinical import Patient  # noqa: E402
from omnipharm.config import settings  # noqa: E402
from omnipharm.dosing import Dose  # noqa: E402
from omnipharm.engine import Engine  # noqa: E402
from omnipharm.models.llm import SECTIONS, SYSTEM, fact_sheet  # noqa: E402

OUT = ROOT / "logs" / "llm_bench"
CASES = {
    "elderly_ckd_warfarin": (["warfarin", "amiodarone", "타이레놀"],
                             dict(age=81, sex="F", weight_kg=48, height_cm=152, scr=1.8, conditions=["arrhythmia"]), None),
    "statin_macrolide": (["clarithromycin", "simvastatin"], dict(age=58, sex="M", weight_kg=80, height_cm=172), None),
    "glp1_sulfonylurea": (["위고비", "아마릴", "metformin"], dict(age=52, sex="F", weight_kg=96, height_cm=160,
                                                             conditions=["diabetes"]), None),
    "otc_overdose": (["타이레놀정500밀리그람(아세트아미노펜)", "콜대원코프에스시럽"],
                     dict(age=35, sex="M", weight_kg=70, height_cm=175),
                     [{"amount": 2, "per_day": 4, "days": 3}, {"amount": 1, "per_day": 3, "days": 3}]),
}
HEADERS = ["### 1.", "### 2.", "### 3.", "### 4."]
VERDICT_WORD = {"높음": ("위험", "높음"), "주의": ("주의",), "낮음": ("안전", "낮음", "낮")}


def facts_for(eng: Engine) -> dict:
    out = {}
    for name, (drugs, pt, doses) in CASES.items():
        prods = []
        for i, d in enumerate(drugs):
            ings, info = eng.resolve_full(d)
            prods.append({"input": d, "ingredients": ings, "strength": info.get("strength"),
                          "dose": Dose(**doses[i]) if doses else None})
        p = Patient(**pt)
        out[name] = fact_sheet(eng.predict(prods, p), p.model_dump())
    return out


def numbers(s: str) -> set[str]:
    out = set()
    for m in re.findall(r"\d+(?:[.,]\d+)*", s):
        m = m.replace(",", "")  # 4,975 -> 4975
        out.add(m[:-2] if m.endswith(".0") else m)  # 58.0 -> 58
    return out


def run(model: str, facts: dict[str, dict]) -> dict:
    url = settings.ollama_url
    with httpx.Client(timeout=600) as c:
        c.post(f"{url}/api/generate", json={"model": model, "keep_alive": 0})  # unload anything else
        for m in c.get(f"{url}/api/ps").json().get("models", []):
            c.post(f"{url}/api/generate", json={"model": m["name"], "keep_alive": 0})
        t0 = time.time()
        c.post(f"{url}/api/generate", json={"model": model, "prompt": "", "keep_alive": "10m"})
        load_s = time.time() - t0
        ps = next((m for m in c.get(f"{url}/api/ps").json().get("models", []) if m["name"] == model), {})
        gpu_share = ps.get("size_vram", 0) / max(ps.get("size", 1), 1)
        res = {"model": model, "load_s": round(load_s, 1), "vram_gb": round(ps.get("size_vram", 0) / 1e9, 1),
               "gpu_share": round(gpu_share, 2), "cases": {}}
        for case, f in facts.items():
            prompt = f"[분석 근거]\n```json\n{json.dumps(f, ensure_ascii=False, indent=1)}\n```\n\n{SECTIONS}"
            body = {"model": model, "stream": True, "keep_alive": "10m", "think": False,
                    "options": {"temperature": 0.2, "num_ctx": 6144, "num_predict": 900},
                    "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]}
            if c.post(f"{url}/api/chat", json={**body, "stream": False,
                                                "options": {"num_predict": 1}}).status_code == 400:
                body.pop("think")  # model without a thinking switch
            text, ttft, final = "", None, {}
            t0 = time.time()
            with c.stream("POST", f"{url}/api/chat", json=body) as r:
                for line in r.iter_lines():
                    if not line:
                        continue
                    msg = json.loads(line)
                    tok = msg.get("message", {}).get("content", "")
                    if tok and ttft is None:
                        ttft = time.time() - t0
                    text += tok
                    if msg.get("done"):
                        final = msg
            tps = final.get("eval_count", 0) / max(final.get("eval_duration", 1) / 1e9, 1e-9)
            letters = re.findall(r"[A-Za-z가-힣一-鿿぀-ヿ]", text)
            hangul = sum(1 for ch in letters if "가" <= ch <= "힣")
            cjk = sum(1 for ch in letters if "一" <= ch <= "鿿" or "぀" <= ch <= "ヿ")
            src_nums = numbers(json.dumps(f, ensure_ascii=False))
            invented = sorted(n for n in numbers(text) - src_nums if n not in {"1", "2", "3", "4"} and len(n) > 1)
            want = VERDICT_WORD.get(f["종합등급"], ())
            res["cases"][case] = {
                "ttft_s": round(ttft or 0, 2), "tok_per_s": round(tps, 1), "total_s": round(time.time() - t0, 1),
                "tokens": final.get("eval_count", 0), "sections": sum(h in text for h in HEADERS),
                "hangul_ratio": round(hangul / max(len(letters), 1), 2), "cjk_chars": cjk,
                "verdict_stated": any(w in text[:400] for w in want), "invented_numbers": invented[:10],
            }
            (OUT / f"{model.replace(':', '_')}__{case}.md").write_text(text, encoding="utf-8")
            print(model, case, res["cases"][case], flush=True)
        c.post(f"{url}/api/generate", json={"model": model, "keep_alive": 0})
    cs = list(res["cases"].values())
    res["avg_tok_per_s"] = round(sum(x["tok_per_s"] for x in cs) / len(cs), 1)
    res["avg_ttft_s"] = round(sum(x["ttft_s"] for x in cs) / len(cs), 2)
    res["invented_total"] = sum(len(x["invented_numbers"]) for x in cs)
    res["sections_ok"] = sum(x["sections"] == 4 for x in cs)
    res["foreign_script_chars"] = sum(x["cjk_chars"] for x in cs)
    return res


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    eng = Engine()  # keeps its GPU memory, exactly like the running server
    facts = facts_for(eng)
    (OUT / "facts.json").write_text(json.dumps(facts, ensure_ascii=False, indent=1), encoding="utf-8")
    summ_f = OUT / "summary.json"
    summary = json.loads(summ_f.read_text(encoding="utf-8")) if summ_f.exists() else {}
    for m in sys.argv[1:]:
        summary[m] = run(m, facts)
        summ_f.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    for m, r in summary.items():
        print(f"{m:16s} VRAM {r['vram_gb']:5.1f}GB GPU {r['gpu_share']:.0%} | {r['avg_tok_per_s']:6.1f} tok/s | "
              f"TTFT {r['avg_ttft_s']:.1f}s | sections {r['sections_ok']}/4 | invented {r['invented_total']} | "
              f"foreign chars {r['foreign_script_chars']}")


if __name__ == "__main__":
    main()
