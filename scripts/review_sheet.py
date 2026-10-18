"""Pharmacist review sheet: realistic multi-drug cases for blinded expert scoring.

  python scripts/review_sheet.py new 2      -> docs/validation/약사_검수표_2차.xlsx  (a round's case list, #201-400)
  python scripts/review_sheet.py send 2     -> docs/validation/약사_검수표_2차_배포용.xlsx  (the file to give a pharmacist)
  (round 1: 약사_검수표.xlsx #1-200 / `send` -> 약사_검수표_배포용.xlsx; a case list is never overwritten without --force)
  python scripts/score_review.py <filled.xlsx | folder>   -> agreement, Cohen's kappa, sensitivity for '위험'

The case list (번호, 환자, 약물) is the reference for the /review page and the scoring - regenerating it draws new
cases, so do it only before any review has started. The workbook for pharmacists is made from that list and never
contains the program's verdict (not even on a hidden sheet); the scoring recomputes it with the current engine.
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
from omnipharm.review import REVIEW_PATIENTS, ROUND_PATIENTS, ROUND_START, SHEETS  # noqa: E402

OUT = ROOT / "docs" / "validation"
PATIENTS = REVIEW_PATIENTS  # (shared with the review page and the scoring)
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
# round 2 also draws medicines round 1 never showed, so rules written from round 1 are tested on new drugs too
POOL_2 = [*POOL, "nitrofurantoin", "dabigatran", "edoxaban", "ondansetron", "haloperidol", "metoclopramide", "codeine",
                 "propranolol", "enalapril", "valsartan", "methylphenidate", "risperidone", "olanzapine", "phenytoin",
                 "rifampin", "ketoconazole", "erythromycin", "sotalol", "metoprolol", "empagliflozin", "linagliptin",
                 "ramipril", "diclofenac", "loxoprofen", "mirabegron", "solifenacin", "oxybutynin", "hydroxyzine",
                 "diphenhydramine", "fexofenadine", "terbinafine", "valacyclovir", "lansoprazole", "cefuroxime",
                 "sulfamethoxazole", "metronidazole", "nifedipine", "carvedilol", "ezetimibe", "fenofibrate",
                 "pioglitazone", "alendronate", "tamoxifen", "eplerenone", "glipizide", "dutasteride", "acitretin"]
ROUND = {1: {"pool": POOL, "seed": 7}, 2: {"pool": POOL_2, "seed": 29}}


def build(round_no: int = 1, n: int = 200, force: bool = False) -> Path:
    """Draw the case list of a review round. Never over a round that may already be under review (force=True)."""
    f = SHEETS[round_no]
    if f.exists() and not force:
        sys.exit(f"{f.name}이(가) 이미 있습니다 — 검수가 시작된 사례 목록을 덮어쓰지 않습니다(정말 새로 뽑으려면 --force).")
    OUT.mkdir(parents=True, exist_ok=True)
    cfg, patients = ROUND[round_no], ROUND_PATIENTS[round_no]
    rng = random.Random(cfg["seed"])
    eng = Engine()
    used = set()  # combinations of earlier rounds are not drawn again
    for r in range(1, round_no):
        if SHEETS[r].exists():
            used |= {frozenset(str(d).split(" + ")) for d in pd.read_excel(SHEETS[r], sheet_name="사례")["약물"]}
    rows, keys = [], list(patients)
    tries = 0
    while len(rows) < n and tries < n * 10:
        tries += 1
        drugs = rng.sample(cfg["pool"], rng.choice([2, 2, 2, 3, 3, 4]))
        if frozenset(drugs) in used:
            continue
        pname = keys[len(rows) % len(keys)]
        try:
            prods = [{"input": d, "ingredients": eng.resolve(d)} for d in drugs]
        except Exception:
            continue
        used.add(frozenset(drugs))
        s = eng.predict(prods, Patient(**patients[pname]))["summary"]
        rows.append({"번호": ROUND_START[round_no] + len(rows), "환자": pname, "약물": " + ".join(drugs),
                     "약사 판정 (위험/주의/안전)": "", "약사 메모": "",
                     "_프로그램 판정": {"비교적 안전": "안전"}.get(s["verdict"], s["verdict"])})
    df = pd.DataFrame(rows)
    with pd.ExcelWriter(f, engine="openpyxl") as xw:
        pd.DataFrame({"안내": [
            f"OMNI-PHARM {round_no}차 검수 사례 목록(원본) — /review 화면과 채점의 기준입니다. 약사에게는 이 파일이 아니라",
            f"`python scripts/review_sheet.py send {round_no}`로 만든 배포용 파일을 보내세요.",
        ]}).to_excel(xw, sheet_name="안내", index=False)
        df[["번호", "환자", "약물", "약사 판정 (위험/주의/안전)", "약사 메모"]].to_excel(xw, sheet_name="사례", index=False)
        xw.sheets["사례"].column_dimensions["C"].width = 60
        xw.sheets["안내"].column_dimensions["A"].width = 110
    print(f"{len(df)} cases (#{df['번호'].min()}-{df['번호'].max()}) -> {f}")
    print(df["_프로그램 판정"].value_counts().to_string())
    return f


# ------------------------------------------------------------------------------------------- the file for pharmacists
VERDICT_HELP = [
    ("위험", "함께 쓰지 않아야 하거나(병용금기), 이 환자에게 쓰면 안 되는 약이 있음 — 처방 변경·용량 조절이 꼭 필요"),
    ("주의", "함께 쓸 수 있지만 관찰(검사·증상), 용량·복용 간격 조절, 환자 설명이 필요"),
    ("안전", "임상적으로 의미 있는 문제 없음 — 특별한 조치 불필요"),
    ("모름", "자료가 부족해 판단하기 어려움 (채점에서 제외)"),
]
FILL = {"위험": "F8CBAD", "주의": "FFE699", "안전": "C6E0B4", "모름": "D9D9D9"}


def _patient_rows(names) -> list[list]:
    from omnipharm.clinical import assess
    from omnipharm.knowledge import DISEASES

    out = []
    for name in names:
        p = PATIENTS[name]
        a = assess(Patient(**p))
        k = a.get("kidney") or {}
        adult = p["age"] >= 18
        out.append([
            name, p["age"], "남" if p["sex"] == "M" else "여", p.get("height_cm"), p.get("weight_kg"), p.get("scr"),
            k.get("crcl") if adult else "소아(성인 공식 해당 없음)", k.get("egfr"),
            " / ".join(f"{lab} {p[key]}" for lab, key in (("AST", "ast"), ("ALT", "alt"), ("총빌리루빈", "total_bilirubin"))
                       if key in p) or "정상 범위로 가정",
            ", ".join({"pregnancy": "임신 중"}.get(c, DISEASES[c]["ko"]) for c in p.get("conditions", [])) or "없음"])
    return out


def _drug_text(eng: Engine, token: str) -> str:
    """'amlodipine' -> '암로디핀(amlodipine)'; a Korean brand keeps its name: '타이레놀: 아세트아미노펜(acetaminophen)'."""
    try:
        names = ", ".join(eng.ko(g) for g in eng.resolve(token))
    except Exception:
        return token
    return names if token.isascii() else f"{token}: {names}"


def send(round_no: int = 1) -> Path:
    """The workbook a pharmacist fills in: guide (with the reviewer's name), patient profiles, cases - no program result."""
    from openpyxl import Workbook
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.worksheet.datavalidation import DataValidation

    from omnipharm.review import raw_cases

    eng = Engine()
    cases = raw_cases(round_no)
    n, first = len(cases), int(cases["번호"].min())
    tag = f" ({round_no}차)" if round_no > 1 else ""
    bold, head = Font(bold=True), PatternFill("solid", fgColor="DDE7F0")
    wrap = Alignment(wrap_text=True, vertical="top")
    thin = Border(*(Side(style="thin", color="BFBFBF"),) * 4)
    wb = Workbook()

    ws = wb.active
    ws.title = "안내"
    lines = [
        (f"OMNI-PHARM 약 조합 검수표{tag}", ""),
        ("", ""),
        ("검수자 이름", ""),  # B3: the reviewer writes here (score_review.py reads it)
        ("작성일", ""),
        ("진행", f'=COUNTA(사례!D2:D{n + 1})&" / {n}건 입력"'),
        ("", ""),
        ("목적", "약물 상호작용 분석 프로그램(졸업 연구)의 판정이 약사의 판단과 얼마나 맞는지 확인합니다. 프로그램의 판정은 이 "
                 "파일에 없습니다 — 평소 업무처럼 판단해 주세요(참고 자료 사용 가능)."
                 + (f" {round_no}차 검수는 이전 검수와 다른 새 사례·새 환자입니다." if round_no > 1 else "")),
        ("하는 법", f"'사례' 시트의 {n}개 사례마다 '환자' 시트의 환자에게 그 약들을 함께 쓴다고 보고, 조합 전체에서 가장 중요한 문제의 "
                   "수준을 '약사 판정' 칸에서 고릅니다(칸을 누르면 목록이 나옵니다). 약 사이의 상호작용뿐 아니라 이 환자의 질환·임신·"
                   "나이·신기능·간기능에 맞지 않는 약, 같은 성분·같은 계열 중복도 포함합니다."),
        ("가정", "용량은 적혀 있지 않으므로 그 환자에게 흔히 쓰는 통상 용량을 쓴다고 가정합니다. 소아는 체중에 맞는 용량으로 봅니다."),
        ("판정 기준", ""),
        *VERDICT_HELP,
        ("", ""),
        ("메모(선택)", "'가장 중요한 문제'에 판단 이유를 짧게(예: 와파린+아미오다론 INR 상승), '약사 메모'에 그 밖의 의견을 적어 주세요. "
                     "'위험'이나 '주의'로 고른 사례에 적어 주시면 프로그램을 고치는 데 가장 도움이 됩니다."),
        ("시간", f"사례 하나에 30초~1분, 전체 약 2~3시간입니다. 나눠서 하셔도 되고, 시간이 부족하면 앞의 50건({first}~{first + 49}번)만 "
                 "해 주셔도 됩니다."),
        ("돌려주기", "다 적으면 저장해서 파일 그대로 보내 주세요. 파일 이름은 바꾸셔도 됩니다."),
        ("개인정보", "사례는 모두 가상의 환자입니다. 검수자 이름 외의 개인정보는 적지 않습니다."),
    ]
    for r in lines:
        ws.append(list(r))
    ws["A1"].font = Font(bold=True, size=14)
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 14, 110
    for row in ws.iter_rows(min_row=3):
        row[0].font = bold
        row[1].alignment = wrap
    for c in ("B3", "B4"):
        ws[c].fill, ws[c].border = PatternFill("solid", fgColor="FFF2CC"), thin
    start = next(i for i, r in enumerate(lines, 1) if r[0] == "위험")
    for i, (v, _) in enumerate(VERDICT_HELP):
        ws.cell(row=start + i, column=1).fill = PatternFill("solid", fgColor=FILL[v])

    ws = wb.create_sheet("환자")
    ws.append(["환자(사례 시트의 이름)", "나이", "성별", "키(cm)", "체중(kg)", "혈청 크레아티닌(mg/dL)", "CrCl(Cockcroft-Gault, mL/min)",
               "eGFR(CKD-EPI 2021)", "간 검사", "질환·상태"])
    for r in _patient_rows(dict.fromkeys(cases['환자'])):
        ws.append(r)
    for c in ws[1]:
        c.font, c.fill, c.alignment = bold, head, wrap
    for col, w in zip("ABCDEFGHIJ", (34, 6, 6, 8, 9, 12, 16, 12, 34, 22), strict=True):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment, c.border = wrap, thin

    ws = wb.create_sheet("사례")
    ws.append(["번호", "환자", "약물 (함께 쓰는 약)", "약사 판정", "가장 중요한 문제 (선택)", "약사 메모 (선택)"])
    for r in cases.itertuples():
        drugs = [_drug_text(eng, d) for d in str(r.약물).split(" + ")]
        ws.append([int(r.번호), r.환자, "\n".join(drugs), None, None, None])
        ws.row_dimensions[ws.max_row].height = 15 * len(drugs) + 4
    for c in ws[1]:
        c.font, c.fill, c.alignment = bold, head, wrap
    for col, w in zip("ABCDEF", (6, 30, 46, 11, 36, 30), strict=True):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment, c.border = wrap, thin
        row[3].fill = PatternFill("solid", fgColor="FFF2CC")
        row[3].alignment = Alignment(horizontal="center", vertical="top")
    dv = DataValidation(type="list", formula1='"' + ",".join(v for v, _ in VERDICT_HELP) + '"', allow_blank=True,
                        showErrorMessage=True, errorTitle="판정", error="위험 / 주의 / 안전 / 모름 중에서 골라 주세요.",
                        showInputMessage=True, promptTitle="약사 판정", prompt="위험 / 주의 / 안전 / 모름")
    ws.add_data_validation(dv)
    dv.add(f"D2:D{n + 1}")
    for v, color in FILL.items():  # the chosen verdict colours its cell
        ws.conditional_formatting.add(f"D2:D{n + 1}", FormulaRule(formula=[f'$D2="{v}"'],
                                                                   fill=PatternFill("solid", fgColor=color)))
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:F{n + 1}"
    for s in wb.worksheets:  # printable on A4 landscape
        s.page_setup.orientation, s.page_setup.paperSize = "landscape", s.PAPERSIZE_A4
        s.page_setup.fitToWidth, s.page_setup.fitToHeight = 1, 0
        s.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = "1:1"
    wb.active = wb.worksheets.index(wb["안내"])
    f = OUT / ("약사_검수표_배포용.xlsx" if round_no == 1 else f"약사_검수표_{round_no}차_배포용.xlsx")
    wb.save(f)
    print(f"{n} cases -> {f}")
    return f


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--force"]
    if args[:1] == ["send"]:
        send(int(args[1]) if len(args) > 1 else 1)
    else:  # [new [round]]: draw a round's case list
        build(int(args[1]) if args[:1] == ["new"] and len(args) > 1 else 1, force="--force" in sys.argv)
