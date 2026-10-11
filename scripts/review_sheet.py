"""Pharmacist review sheet: realistic multi-drug cases with the program's verdict, for blinded expert scoring.

  python scripts/review_sheet.py            -> docs/validation/약사_검수표.xlsx  (200 cases)
  (pharmacist fills column '약사 판정' with 위험 / 주의 / 안전, optionally '약사 메모')
  python scripts/score_review.py <filled.xlsx>   -> agreement, Cohen's kappa, sensitivity for '위험'

The program's verdict is on a separate, hidden-by-default sheet so the reviewer can judge blind first.
"""

from __future__ import annotations

import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("OMNIPHARM_ONLINE_LOOKUP", "0")

import pandas as pd  # noqa: E402

from omnipharm.clinical import Patient  # noqa: E402
from omnipharm.engine import Engine  # noqa: E402

OUT = ROOT / "docs" / "validation"
PATIENTS = {
    "건강한 성인 (45세 남, 72kg)": dict(age=45, sex="M", weight_kg=72, height_cm=174, scr=0.9),
    "고령·신장저하 (82세 여, 48kg, Cr 1.6)": dict(age=82, sex="F", weight_kg=48, height_cm=150, scr=1.6),
    "임신 중 (31세 여)": dict(age=31, sex="F", weight_kg=60, height_cm=162, scr=0.7, conditions=["pregnancy"]),
    "간경변 (63세 남)": dict(age=63, sex="M", weight_kg=68, height_cm=170, scr=1.0, ast=110, alt=80,
                         total_bilirubin=2.8, conditions=["hepatic"]),
    "심부전·당뇨 (70세 남)": dict(age=70, sex="M", weight_kg=80, height_cm=168, scr=1.3,
                            conditions=["heart_failure", "diabetes"]),
    "소아 (9세, 30kg)": dict(age=9, sex="M", weight_kg=30, height_cm=133, scr=0.5),
}
# commonly used medicines in Korean practice (chronic, acute, OTC, lifestyle)
POOL = ["amlodipine", "losartan", "telmisartan", "atorvastatin", "rosuvastatin", "metformin", "glimepiride",
        "sitagliptin", "dapagliflozin", "aspirin", "clopidogrel", "warfarin", "apixaban", "rivaroxaban", "digoxin",
        "amiodarone", "bisoprolol", "furosemide", "spironolactone", "hydrochlorothiazide", "levothyroxine",
        "omeprazole", "esomeprazole", "famotidine", "acetaminophen", "ibuprofen", "naproxen", "celecoxib",
        "tramadol", "oxycodone", "pregabalin", "gabapentin", "sertraline", "escitalopram", "fluoxetine",
        "duloxetine", "trazodone", "mirtazapine", "quetiapine", "alprazolam", "zolpidem", "lorazepam",
        "donepezil", "levetiracetam", "valproate", "carbamazepine", "clarithromycin", "azithromycin",
        "levofloxacin", "ciprofloxacin", "amoxicillin", "doxycycline", "fluconazole", "itraconazole",
        "prednisolone", "methotrexate", "allopurinol", "colchicine", "febuxostat", "tamsulosin", "finasteride",
        "sildenafil", "tadalafil", "isotretinoin", "pseudoephedrine", "chlorpheniramine", "dextromethorphan",
        "cetirizine", "montelukast", "theophylline", "simvastatin", "verapamil", "diltiazem", "lithium",
        "potassium chloride", "calcium carbonate", "ginkgo biloba leaf extract", "위고비", "보톡스", "타이레놀"]


def main(n: int = 200, seed: int = 7) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    eng = Engine()
    rows, keys = [], list(PATIENTS)
    tries = 0
    while len(rows) < n and tries < n * 5:
        tries += 1
        k = rng.choice([2, 2, 2, 3, 3, 4])
        drugs = rng.sample(POOL, k)
        pname = keys[len(rows) % len(keys)]
        try:
            prods = [{"input": d, "ingredients": eng.resolve(d)} for d in drugs]
        except Exception:
            continue
        r = eng.predict(prods, Patient(**PATIENTS[pname]))
        s = r["summary"]
        rows.append({"번호": len(rows) + 1, "환자": pname, "약물": " + ".join(drugs),
                     "약사 판정 (위험/주의/안전)": "", "약사 메모": "",
                     "_프로그램 판정": {"비교적 안전": "안전"}.get(s["verdict"], s["verdict"]),
                     "_주요 근거": " / ".join(x["text"] for x in s["reasons"][:3])})
    df = pd.DataFrame(rows)
    f = OUT / "약사_검수표.xlsx"
    with pd.ExcelWriter(f, engine="openpyxl") as xw:
        pd.DataFrame({"안내": [
            "OMNI-PHARM 약사 검수표 — 각 사례를 보고 '약사 판정' 열에 위험 / 주의 / 안전 중 하나를 적어 주세요.",
            "위험 = 병용·투여를 피하거나 용량 변경이 반드시 필요 / 주의 = 모니터링·간격 조절 등 / 안전 = 의미 있는 문제 없음",
            "공정한 평가를 위해 먼저 '사례' 시트만 보고 판정한 뒤, 필요하면 '프로그램 결과' 시트를 보세요.",
            "채점: python scripts/score_review.py 약사_검수표.xlsx",
        ]}).to_excel(xw, sheet_name="안내", index=False)
        df[["번호", "환자", "약물", "약사 판정 (위험/주의/안전)", "약사 메모"]].to_excel(xw, sheet_name="사례", index=False)
        df[["번호", "_프로그램 판정", "_주요 근거"]].rename(columns=lambda c: c.lstrip("_")).to_excel(
            xw, sheet_name="프로그램 결과", index=False)
        from openpyxl.worksheet.datavalidation import DataValidation

        ws = xw.sheets["사례"]
        dv = DataValidation(type="list", formula1='"위험,주의,안전"', allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f"D2:D{len(df) + 1}")
        for col, w in zip("ABCDE", (6, 34, 60, 22, 40), strict=True):
            ws.column_dimensions[col].width = w
        xw.sheets["프로그램 결과"].column_dimensions["C"].width = 120
        xw.sheets["프로그램 결과"].sheet_state = "hidden"
        xw.sheets["안내"].column_dimensions["A"].width = 110
    print(f"{len(df)} cases -> {f}")
    print(df["_프로그램 판정"].value_counts().to_string())


if __name__ == "__main__":
    main()
