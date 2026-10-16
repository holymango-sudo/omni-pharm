"""Clinical sanity audit: ~100 combinations whose answer is well established in standard references
(FDA labelling, Lexicomp / Micromedex severity, Stockley, Beers 2023), run for a healthy adult.

  python scripts/audit_combos.py      -> logs/validation/combo_audit.csv + summary on screen

Categories
  MAJOR     contraindicated / avoid / major           expected 위험
  MODERATE  monitor / adjust                          expected 주의 (위험 also acceptable where noted)
  NONE      routinely co-prescribed, no meaningful    expected 비교적 안전 (주의 allowed where noted)
Besides the verdict, every result is screened for message problems: a 'high' reason under a safe verdict,
alarming AI text about vitamins, label evidence whose excerpt does not name the other drug.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("OMNIPHARM_ONLINE_LOOKUP", "0")

import pandas as pd  # noqa: E402

from omnipharm.clinical import Patient  # noqa: E402
from omnipharm.engine import Engine  # noqa: E402

D, C, S = "위험", "주의", "비교적 안전"
CASES: list[tuple[str, list[str], set[str]]] = [
    # ---------------------------------------------------------------- MAJOR (avoid / contraindicated)
    ("MAJOR", ["warfarin", "amiodarone"], {D}),
    ("MAJOR", ["clarithromycin", "simvastatin"], {D}),
    ("MAJOR", ["itraconazole", "simvastatin"], {D}),
    ("MAJOR", ["ritonavir", "simvastatin"], {D}),
    ("MAJOR", ["gemfibrozil", "simvastatin"], {D}),
    ("MAJOR", ["sildenafil", "nitroglycerin"], {D}),
    ("MAJOR", ["tadalafil", "isosorbide mononitrate"], {D}),
    ("MAJOR", ["phenelzine", "sertraline"], {D}),
    ("MAJOR", ["linezolid", "sertraline"], {D}),
    ("MAJOR", ["selegiline", "meperidine"], {D}),
    ("MAJOR", ["tizanidine", "ciprofloxacin"], {D}),
    ("MAJOR", ["tizanidine", "fluvoxamine"], {D}),
    ("MAJOR", ["ketoconazole", "midazolam"], {D}),
    ("MAJOR", ["itraconazole", "triazolam"], {D}),
    ("MAJOR", ["clarithromycin", "pimozide"], {D}),
    ("MAJOR", ["ketoconazole", "domperidone"], {D}),
    ("MAJOR", ["clarithromycin", "ergotamine"], {D}),
    ("MAJOR", ["sumatriptan", "ergotamine"], {D}),
    ("MAJOR", ["clopidogrel", "omeprazole"], {D}),
    ("MAJOR", ["spironolactone", "potassium chloride"], {D}),
    ("MAJOR", ["oxycodone", "alprazolam"], {D}),
    ("MAJOR", ["methadone", "diazepam"], {D}),
    ("MAJOR", ["allopurinol", "azathioprine"], {D}),
    ("MAJOR", ["methotrexate", "trimethoprim"], {D, C}),
    ("MAJOR", ["sacubitril", "lisinopril"], {D}),
    ("MAJOR", ["isotretinoin", "doxycycline"], {D}),
    ("MAJOR", ["isotretinoin", "minocycline"], {D}),
    ("MAJOR", ["dabigatran", "ketoconazole"], {D}),
    ("MAJOR", ["nirmatrelvir", "rivaroxaban"], {D}),
    ("MAJOR", ["colchicine", "clarithromycin"], {D}),
    ("MAJOR", ["dronedarone", "ketoconazole"], {D}),
    ("MAJOR", ["citalopram", "pimozide"], {D}),
    ("MAJOR", ["fluconazole", "pimozide"], {D}),
    ("MAJOR", ["rifampin", "ticagrelor"], {D, C}),
    ("MAJOR", ["carbamazepine", "rivaroxaban"], {D, C}),
    ("MAJOR", ["tramadol", "phenelzine"], {D}),
    ("MAJOR", ["lithium", "hydrochlorothiazide"], {D, C}),
    ("MAJOR", ["warfarin", "fluconazole"], {D, C}),
    ("MAJOR", ["theophylline", "ciprofloxacin"], {D, C}),
    ("MAJOR", ["digoxin", "amiodarone"], {D, C}),
    ("MAJOR", ["rifampin", "ethinyl estradiol"], {D, C}),
    ("MAJOR", ["apixaban", "ketoconazole"], {D, C}),
    # ---------------------------------------------------------------- MODERATE (monitor / adjust)
    ("MODERATE", ["lisinopril", "spironolactone"], {C, D}),
    ("MODERATE", ["sertraline", "tramadol"], {C, D}),
    ("MODERATE", ["ibuprofen", "lisinopril"], {C}),
    ("MODERATE", ["levothyroxine", "calcium carbonate"], {C}),
    ("MODERATE", ["ciprofloxacin", "magnesium hydroxide"], {C}),
    ("MODERATE", ["warfarin", "acetaminophen"], {C}),
    ("MODERATE", ["amlodipine", "simvastatin"], {C, D}),  # label caps simvastatin at 20 mg (Lexicomp D)
    ("MODERATE", ["atorvastatin", "clarithromycin"], {C, D}),
    ("MODERATE", ["citalopram", "omeprazole"], {C}),
    ("MODERATE", ["metoprolol", "verapamil"], {C, D}),
    ("MODERATE", ["prednisolone", "ibuprofen"], {C}),
    ("MODERATE", ["losartan", "potassium chloride"], {C, D}),
    ("MODERATE", ["digoxin", "furosemide"], {C}),
    ("MODERATE", ["escitalopram", "aspirin"], {C}),
    ("MODERATE", ["metformin", "cimetidine"], {C}),
    ("MODERATE", ["glimepiride", "fluconazole"], {C}),
    ("MODERATE", ["warfarin", "aspirin"], {C, D}),
    ("MODERATE", ["diltiazem", "simvastatin"], {C, D}),
    ("MODERATE", ["alprazolam", "fluvoxamine"], {C, D}),
    ("MODERATE", ["quetiapine", "escitalopram"], {C}),
    ("MODERATE", ["methotrexate", "ibuprofen"], {C, D}),
    ("MODERATE", ["levofloxacin", "prednisolone"], {C}),
    ("MODERATE", ["bupropion", "metoprolol"], {C}),
    ("MODERATE", ["fluoxetine", "metoprolol"], {C}),
    ("MODERATE", ["doxycycline", "ferrous sulfate"], {C}),
    # ---------------------------------------------------------------- NONE (routinely co-prescribed)
    ("NONE", ["acetaminophen", "amoxicillin"], {S}),
    ("NONE", ["cetirizine", "acetaminophen"], {S}),
    ("NONE", ["amlodipine", "atorvastatin"], {S, C}),
    ("NONE", ["metformin", "atorvastatin"], {S}),
    ("NONE", ["losartan", "amlodipine"], {S}),
    ("NONE", ["omeprazole", "amoxicillin"], {S}),
    ("NONE", ["levothyroxine", "atorvastatin"], {S}),
    ("NONE", ["finasteride", "tamsulosin"], {S}),
    ("NONE", ["montelukast", "cetirizine"], {S}),
    ("NONE", ["sitagliptin", "metformin"], {S}),
    ("NONE", ["rosuvastatin", "ezetimibe"], {S}),
    ("NONE", ["famotidine", "acetaminophen"], {S}),
    ("NONE", ["amoxicillin", "ibuprofen"], {S}),
    ("NONE", ["cholecalciferol", "calcium carbonate"], {S}),
    ("NONE", ["melatonin", "acetaminophen"], {S}),
    ("NONE", ["minoxidil", "finasteride"], {S}),
    ("NONE", ["sertraline", "acetaminophen"], {S}),
    ("NONE", ["bisoprolol", "amlodipine"], {S}),
    ("NONE", ["dapagliflozin", "metformin"], {S}),
    ("NONE", ["pantoprazole", "clopidogrel"], {S, C}),
    ("NONE", ["ondansetron", "acetaminophen"], {S}),
    ("NONE", ["albuterol", "budesonide"], {S}),
    ("NONE", ["valsartan", "hydrochlorothiazide"], {S}),
    ("NONE", ["telmisartan", "amlodipine"], {S}),
    ("NONE", ["loperamide", "acetaminophen"], {S}),
    ("NONE", ["fexofenadine", "pseudoephedrine"], {S}),
    ("NONE", ["loratadine", "acetaminophen"], {S}),
    ("NONE", ["esomeprazole", "acetaminophen"], {S}),
    ("NONE", ["levocetirizine", "montelukast"], {S}),
    ("NONE", ["empagliflozin", "sitagliptin"], {S}),
    ("NONE", ["donepezil", "memantine"], {S}),
    ("NONE", ["tamsulosin", "mirabegron"], {S, C}),
    ("NONE", ["alendronate", "cholecalciferol"], {S}),
    ("NONE", ["folic acid", "methotrexate"], {S, C}),
    ("NONE", ["박카스디액", "타이레놀정500밀리그람(아세트아미노펜)"], {S}),
    ("NONE", ["tretinoin", "clindamycin"], {S}),
    ("NONE", ["sildenafil", "tamsulosin"], {S, C}),
    ("NONE", ["oseltamivir", "acetaminophen"], {S}),
    ("NONE", ["azithromycin", "acetaminophen"], {S}),
    ("NONE", ["dextromethorphan", "guaifenesin"], {S}),
    ("NONE", ["insulin glargine", "metformin"], {S, C}),
    ("NONE", ["aspirin", "atorvastatin"], {S}),
]
# Held-out set: written AFTER the rules were tuned on CASES and never used for tuning - honest accuracy estimate.
HOLDOUT: list[tuple[str, list[str], set[str]]] = [
    ("MAJOR", ["fluoxetine", "phenelzine"], {D}),
    ("MAJOR", ["linezolid", "venlafaxine"], {D}),
    ("MAJOR", ["clarithromycin", "lovastatin"], {D}),
    ("MAJOR", ["voriconazole", "simvastatin"], {D}),
    ("MAJOR", ["posaconazole", "midazolam"], {D}),
    ("MAJOR", ["vardenafil", "isosorbide dinitrate"], {D}),
    ("MAJOR", ["ritonavir", "triazolam"], {D}),
    ("MAJOR", ["fluvoxamine", "ramelteon"], {D}),
    ("MAJOR", ["ciprofloxacin", "theophylline"], {D, C}),
    ("MAJOR", ["eplerenone", "ketoconazole"], {D}),
    ("MAJOR", ["ivabradine", "clarithromycin"], {D}),
    ("MAJOR", ["lomitapide", "ketoconazole"], {D}),
    ("MAJOR", ["morphine", "lorazepam"], {D}),
    ("MAJOR", ["hydrocodone", "zolpidem"], {D}),
    ("MAJOR", ["methotrexate", "sulfamethoxazole"], {D, C}),
    ("MAJOR", ["mercaptopurine", "allopurinol"], {D}),
    ("MAJOR", ["warfarin", "metronidazole"], {D, C}),
    ("MAJOR", ["thioridazine", "fluoxetine"], {D}),
    ("MAJOR", ["dofetilide", "hydrochlorothiazide"], {D}),
    ("MAJOR", ["cisapride", "erythromycin"], {D}),
    ("MODERATE", ["enalapril", "potassium chloride"], {C, D}),
    ("MODERATE", ["levothyroxine", "ferrous sulfate"], {C}),
    ("MODERATE", ["tetracycline", "calcium carbonate"], {C}),
    ("MODERATE", ["warfarin", "simvastatin"], {C}),
    ("MODERATE", ["carvedilol", "insulin glargine"], {C, S}),
    ("MODERATE", ["clopidogrel", "aspirin"], {C, D}),
    ("MODERATE", ["naproxen", "sertraline"], {C}),
    ("MODERATE", ["metoclopramide", "sertraline"], {C}),
    ("MODERATE", ["phenytoin", "fluconazole"], {C, D}),
    ("MODERATE", ["lithium", "ibuprofen"], {C, D}),
    ("MODERATE", ["valproate", "lamotrigine"], {C, D}),
    ("MODERATE", ["cyclosporine", "atorvastatin"], {C, D}),
    ("MODERATE", ["spironolactone", "trimethoprim"], {C, D}),
    ("MODERATE", ["glipizide", "clarithromycin"], {C}),
    ("MODERATE", ["ondansetron", "citalopram"], {C}),
    ("NONE", ["amlodipine", "metformin"], {S}),
    ("NONE", ["lisinopril", "atorvastatin"], {S}),
    ("NONE", ["omeprazole", "acetaminophen"], {S}),
    ("NONE", ["simvastatin", "ezetimibe"], {S}),
    ("NONE", ["losartan", "hydrochlorothiazide"], {S}),
    ("NONE", ["metformin", "glipizide"], {S, C}),
    ("NONE", ["cetirizine", "pseudoephedrine"], {S}),
    ("NONE", ["levothyroxine", "metformin"], {S}),
    ("NONE", ["amoxicillin", "clavulanate"], {S}),
    ("NONE", ["fluticasone", "salmeterol"], {S}),
    ("NONE", ["tiotropium", "albuterol"], {S}),
    ("NONE", ["latanoprost", "timolol"], {S}),
    ("NONE", ["allopurinol", "colchicine"], {S}),
    ("NONE", ["furosemide", "spironolactone"], {S, C}),
    ("NONE", ["entecavir", "acetaminophen"], {S}),
    ("NONE", ["rosuvastatin", "aspirin"], {S}),
    ("NONE", ["candesartan", "amlodipine"], {S}),
    ("NONE", ["famotidine", "loratadine"], {S}),
    ("NONE", ["nitrofurantoin", "acetaminophen"], {S}),
    ("NONE", ["vitamin b12", "folic acid"], {S}),
    ("NONE", ["escitalopram", "acetaminophen"], {S}),
    ("NONE", ["duloxetine", "pregabalin"], {S, C}),
    ("NONE", ["linagliptin", "metformin"], {S}),
    ("NONE", ["bisoprolol", "atorvastatin"], {S}),
    ("NONE", ["mirtazapine", "acetaminophen"], {S}),
]
# Food, drink and smoking (labelling / standard references)
FOOD_CASES: list[tuple[str, list[str], set[str]]] = [
    ("MAJOR", ["술", "metronidazole"], {D}),
    ("MAJOR", ["술", "oxycodone"], {D}),
    ("MAJOR", ["술", "zolpidem"], {D}),
    ("MAJOR", ["자몽주스", "simvastatin"], {D}),
    ("MAJOR", ["자몽주스", "felodipine"], {D, C}),
    ("MODERATE", ["술", "타이레놀"], {C}),
    ("MODERATE", ["술", "metformin"], {C}),
    ("MODERATE", ["술", "ibuprofen"], {C}),
    ("MODERATE", ["술", "warfarin"], {C, D}),
    ("MODERATE", ["자몽주스", "atorvastatin"], {C}),
    ("MODERATE", ["우유", "ciprofloxacin"], {C}),
    ("MODERATE", ["우유", "doxycycline"], {C}),
    ("MODERATE", ["우유", "levothyroxine"], {C}),
    ("MODERATE", ["시금치", "warfarin"], {C}),
    ("MODERATE", ["흡연", "clozapine"], {C}),
    ("MODERATE", ["흡연", "ethinyl estradiol"], {C, D}),
    ("NONE", ["술", "amlodipine"], {S}),
    ("NONE", ["우유", "amoxicillin"], {S}),
    ("NONE", ["커피", "acetaminophen"], {S}),
    ("NONE", ["자몽주스", "acetaminophen"], {S}),
    ("NONE", ["시금치", "metformin"], {S}),
    ("NONE", ["흡연", "amoxicillin"], {S}),
]

# Same medicines, different patients: (description, drugs, patient overrides, expected verdicts)
PATIENT_CASES: list[tuple[str, list[str], dict, set[str]]] = [
    ("CKD 4기 + 메트포르민", ["metformin", "amlodipine"], dict(age=70, scr=3.2, weight_kg=60), {D}),
    ("건강 + 메트포르민", ["metformin", "amlodipine"], {}, {S}),
    ("심부전 + NSAID", ["ibuprofen", "furosemide"], dict(age=72, conditions=["heart_failure"]), {D}),
    ("위궤양 + NSAID", ["naproxen", "omeprazole"], dict(conditions=["peptic_ulcer"]), {D}),
    ("임신 + 이소트레티노인", ["isotretinoin", "acetaminophen"], dict(sex="F", conditions=["pregnancy"]), {D}),
    ("임신 + ARB", ["losartan", "acetaminophen"], dict(sex="F", conditions=["pregnancy"]), {D}),
    ("건강 여성 + ARB", ["losartan", "acetaminophen"], dict(sex="F"), {S}),
    ("80세 + 벤조디아제핀", ["lorazepam", "acetaminophen"], dict(age=82, weight_kg=50), {C, D}),
    ("80세 + 1세대 항히스타민", ["diphenhydramine", "acetaminophen"], dict(age=82, weight_kg=50), {C, D}),
    ("천식 + 비선택 베타차단제", ["propranolol", "acetaminophen"], dict(conditions=["asthma"]), {D}),
    ("녹내장 + 항콜린제", ["oxybutynin", "acetaminophen"], dict(age=68, conditions=["glaucoma"]), {C, D}),
    ("전립선비대 + 비충혈제거제", ["pseudoephedrine", "acetaminophen"], dict(age=66, conditions=["bph"]), {C, D}),
    ("파킨슨 + 메토클로프라미드", ["metoclopramide", "levodopa"], dict(age=70, conditions=["parkinson"]), {D}),
    ("중증근무력증 + 퀴놀론", ["levofloxacin", "acetaminophen"], dict(conditions=["myasthenia"]), {D}),
    ("뇌전증 + 트라마돌", ["tramadol", "levetiracetam"], dict(conditions=["epilepsy"]), {C, D}),
    ("간경변 + 아세트아미노펜", ["acetaminophen", "famotidine"],
     dict(ast=120, alt=90, total_bilirubin=3.0, conditions=["hepatic"]), {C, D}),
    ("소아 9세 + 성인 감기약(슈도에페드린)", ["pseudoephedrine", "acetaminophen"], dict(age=9, weight_kg=30, height_cm=133), {C, D, S}),
    ("고령 + 신기능 저하 + 프레가발린", ["pregabalin", "acetaminophen"], dict(age=84, weight_kg=45, scr=1.9), {C, D, S}),
]
ADULT = dict(age=40, sex="M", weight_kg=72, height_cm=175, scr=0.9, ast=22, alt=20, total_bilirubin=0.6)


def message_problems(r: dict) -> list[str]:
    s, out = r["summary"], []
    if s["verdict"] == S and any(x["sev"] == "high" for x in s["reasons"]):
        out.append("안전 판정인데 '높음' 이유가 있음")
    for x in s["reasons"]:
        # an AI warning about a vitamin is a confounding artefact (a documented drug-label interaction such as
        # carbamazepine + niacinamide is legitimate)
        if x["sev"] in ("high", "mid") and "AI" in x["text"] and any(
                v in x["text"] for v in ("니코틴산", "리보플라빈", "피리독신", "비타민", "타우린")):
            out.append("비타민에 AI 경고 문구")
    for p in r["pairs"]:
        for e in p["label_evidence"]:
            if e.get("via_class"):
                continue
            ex = e["excerpt"].lower()
            terms = [t.lower() for t in e.get("terms") or [e["mentions"]]]
            if not any(t.split()[0] in ex for t in terms):
                out.append(f"라벨 발췌에 상대 약 이름 없음: {e['label_of']}→{e['mentions']}")
    return out


def patients(eng: Engine) -> int:
    ok = 0
    for desc, names, pt, expected in PATIENT_CASES:
        prods = [{"input": n, "ingredients": eng.resolve(n)} for n in names]
        r = eng.predict(prods, Patient(**{**ADULT, **pt}))
        v = r["summary"]["verdict"]
        ok += v in expected
        mark = "✓" if v in expected else "✗"
        print(f"{mark} {desc:28s} {' + '.join(names):32s} 기대 {'/'.join(sorted(expected)):12s} 결과 {v}"
              + ("" if v in expected else f"\n     {[x['text'][:110] for x in r['summary']['reasons'][:3]]}"))
    print(f"PATIENT  {ok}/{len(PATIENT_CASES)} 정답")
    return 0 if ok == len(PATIENT_CASES) else 1


def main() -> int:
    """Exit code 0 = every case right and no wording problem (scripts/ci.ps1 full)."""
    holdout = "--holdout" in sys.argv
    if "--patients" in sys.argv:
        return patients(Engine())
    cases = HOLDOUT if holdout else FOOD_CASES if "--foods" in sys.argv else CASES
    eng = Engine()
    rows = []
    for cat, names, expected in cases:
        try:
            prods = [{"input": n, "ingredients": eng.resolve(n)} for n in names]
        except Exception as e:
            rows.append({"category": cat, "drugs": " + ".join(names), "expected": "/".join(sorted(expected)),
                         "verdict": "인식 실패", "ok": False, "problems": str(e)[:80], "reasons": ""})
            continue
        r = eng.predict(prods, Patient(**ADULT))
        v = r["summary"]["verdict"]
        rows.append({"category": cat, "drugs": " + ".join(names), "expected": "/".join(sorted(expected)), "verdict": v,
                     "ok": v in expected, "problems": "; ".join(message_problems(r)),
                     "reasons": " | ".join(f"[{x['sev']}] {x['text']}" for x in r["summary"]["reasons"][:4])})
    df = pd.DataFrame(rows)
    out = ROOT / "logs" / "validation"
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / ("combo_audit_holdout.csv" if holdout else "combo_audit.csv"), index=False, encoding="utf-8-sig")
    for cat in ("MAJOR", "MODERATE", "NONE"):
        d = df[df["category"] == cat]
        print(f"{cat:9s} {int(d['ok'].sum())}/{len(d)} 정답")
    print(f"TOTAL     {int(df['ok'].sum())}/{len(df)} | 문구 문제 {int((df['problems'] != '').sum())}건")
    for _, r in df[~df["ok"] | (df["problems"] != "")].iterrows():
        print(f"\n✗ [{r['category']}] {r['drugs']}: 기대 {r['expected']} / 결과 {r['verdict']}"
              + (f"\n   문제: {r['problems']}" if r["problems"] else "") + f"\n   {r['reasons'][:400]}")
    return 0 if df["ok"].all() and (df["problems"] == "").all() else 1


if __name__ == "__main__":
    raise SystemExit(main())
