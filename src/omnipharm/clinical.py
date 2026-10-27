"""Patient state: body size + lab values -> organ function, drug-handling capacity, model features.

Lab values and height are not in FAERS, so the learned patient model cannot see them directly. They are
bridged in transparent steps:
  1. standard formulas: BMI, BSA (Mosteller), CKD-EPI 2021 eGFR, Cockcroft-Gault creatinine clearance
     (the value drug labels use for renal dose adjustment - it depends on weight, and uses adjusted body
     weight in obesity), ULN multiples for liver tests
  2. impairment switches on the comorbidity flags the model was trained on (renal / hepatic / cardiac ...),
     weight and age are model inputs directly
  3. a 0-100 "drug-handling capacity" summary for people who are not clinicians (reference only)
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from omnipharm.knowledge import COMORBIDITIES, DISEASES, disease_ids

AST_ULN, ALT_ULN, BILI_ULN = 40.0, 40.0, 1.2
ADULT_AGE = 18  # CKD-EPI and Cockcroft-Gault are validated in adults only; children: bedside Schwartz
_SEX = {"f": "F", "female": "F", "woman": "F", "여": "F", "여성": "F", "여자": "F",
        "m": "M", "male": "M", "man": "M", "남": "M", "남성": "M", "남자": "M"}


class Patient(BaseModel):
    age: float | None = Field(default=None, ge=0, le=120)
    sex: str | None = Field(default=None, description="'F' or 'M'")
    weight_kg: float | None = Field(default=None, gt=0, le=400)
    height_cm: float | None = Field(default=None, ge=40, le=250)
    scr: float | None = Field(default=None, gt=0, le=30, description="serum creatinine mg/dL")
    egfr: float | None = Field(default=None, ge=0, le=200, description="mL/min/1.73m2 (overrides scr)")
    ast: float | None = Field(default=None, ge=0, le=20000)
    alt: float | None = Field(default=None, ge=0, le=20000)
    total_bilirubin: float | None = Field(default=None, ge=0, le=60)
    # Child-Pugh (labels give hepatic dose steps by its class): albumin, INR, ascites, encephalopathy - or the class
    albumin: float | None = Field(default=None, gt=0, le=7, description="serum albumin g/dL")
    inr: float | None = Field(default=None, gt=0, le=15)
    ascites: Literal["none", "mild", "severe"] | None = Field(default=None, description="severe = moderate-severe")
    encephalopathy: Literal["none", "mild", "severe"] | None = Field(default=None, description="mild = grade 1-2")
    child_pugh: Literal["A", "B", "C"] | None = Field(default=None, description="Child-Pugh class when already known")
    conditions: list[str] = Field(default_factory=list, description=f"disease ids: {list(DISEASES)}")
    diagnosis: str | None = Field(default=None, description="free-text diagnosis (Korean/English)")
    symptoms: str | None = None

    @field_validator("sex", mode="before")
    @classmethod
    def _sex(cls, v):
        """'F' / 'M' whatever the caller wrote ('female', '여성', 'f'): the formulas below compare against 'F' only, so
        'female' used to lose the Cockcroft-Gault 0.85 factor. Anything else = not given."""
        return None if v is None else _SEX.get(str(v).strip().lower())

    @field_validator("child_pugh", mode="before")
    @classmethod
    def _cp(cls, v):
        return None if v in (None, "") else str(v).strip().upper()

    @field_validator("ascites", "encephalopathy", mode="before")
    @classmethod
    def _grade(cls, v):
        return None if v in (None, "") else str(v).strip().lower()


_GRADE = {None: 1, "none": 1, "mild": 2, "severe": 3}


def child_pugh(p: Patient, liver_disease: bool) -> dict | None:
    """Child-Pugh class of a person with chronic liver disease (5-6 A, 7-9 B, 10-15 C). Without a known liver disease
    the score is not computed: a low albumin (nephrotic syndrome) or a high INR (warfarin) alone would read as 'C'.
    Ascites / encephalopathy not given count as absent and are named, so the real class can only be higher. A class
    given by the user wins over a lower computed one (never over a higher one)."""
    if not (liver_disease or p.child_pugh):
        return None
    out = None
    if None not in (p.total_bilirubin, p.albumin, p.inr):
        b, al, inr = p.total_bilirubin, p.albumin, p.inr
        pts = {"빌리루빈": 1 if b < 2 else 2 if b <= 3 else 3, "알부민": 1 if al > 3.5 else 2 if al >= 2.8 else 3,
               "INR": 1 if inr < 1.7 else 2 if inr <= 2.3 else 3, "복수": _GRADE[p.ascites],
               "간성뇌증": _GRADE[p.encephalopathy]}
        score = sum(pts.values())
        out = {"class": "A" if score <= 6 else "B" if score <= 9 else "C", "score": score, "points": pts,
               "assumed": [k for k, v in (("복수", p.ascites), ("간성뇌증", p.encephalopathy)) if v is None],
               "source": "계산"}
    if p.child_pugh and (out is None or p.child_pugh > out["class"]):
        out = {"class": p.child_pugh, "score": None, "points": None, "assumed": [], "source": "입력값"}
    return out


def schwartz_bedside(scr: float, height_cm: float) -> float:
    """Bedside Schwartz (2009) for children 1-17 y: eGFR = 0.413 x height (cm) / creatinine (mg/dL), mL/min/1.73m2."""
    return 0.413 * height_cm / scr


def ckd_epi_2021(scr: float, age: float, sex: str | None) -> float:
    """Race-free CKD-EPI 2021 creatinine equation (mL/min/1.73m2)."""
    female = (sex or "").upper().startswith("F")
    k, a = (0.7, -0.241) if female else (0.9, -0.302)
    r = scr / k
    return 142 * min(r, 1) ** a * max(r, 1) ** -1.200 * 0.9938 ** age * (1.012 if female else 1.0)


def kdigo_stage(egfr: float) -> tuple[str, str]:
    for lo, g, desc in ((90, "G1", "정상"), (60, "G2", "경도 감소"), (45, "G3a", "경도~중등도 감소"),
                        (30, "G3b", "중등도~고도 감소"), (15, "G4", "고도 감소")):
        if egfr >= lo:
            return g, desc
    return "G5", "신부전"


def ideal_body_weight(height_cm: float, sex: str | None) -> float:
    """Devine formula (kg), adults; not positive below about 100 cm (handled by the caller)."""
    base = 45.5 if (sex or "").upper().startswith("F") else 50.0
    return base + 0.9055 * (height_cm - 152.4)


def body(p: Patient) -> dict | None:
    if not p.weight_kg:
        return None
    out: dict = {"weight": p.weight_kg}
    if p.height_cm:
        h = p.height_cm / 100
        bmi = p.weight_kg / h ** 2
        ibw = ideal_body_weight(p.height_cm, p.sex)
        cat = ("저체중" if bmi < 18.5 else "정상" if bmi < 23 else "과체중" if bmi < 25 else
               "비만" if bmi < 30 else "고도비만")  # Asian-Pacific BMI cut-offs (KSSO)
        out.update(height=p.height_cm, bmi=round(bmi, 1), bmi_cat=cat, bsa=round(math.sqrt(p.height_cm * p.weight_kg / 3600), 2),
                   ibw=round(ibw, 1) if ibw > 0 else None)
        # dosing weight for Cockcroft-Gault: actual if below ideal, adjusted (IBW + 0.4 excess) if obese.
        # Devine is an adult formula: in children (and at heights where it gives no positive weight) the actual
        # weight is used - IBW of a 90 cm toddler is negative and gave a negative creatinine clearance.
        if (p.age is not None and p.age < ADULT_AGE) or ibw <= 0 or p.weight_kg < ibw:
            out["dosing_weight"], out["dosing_weight_kind"] = p.weight_kg, "실제 체중"
        elif bmi >= 30:
            out["dosing_weight"], out["dosing_weight_kind"] = round(ibw + 0.4 * (p.weight_kg - ibw), 1), "보정 체중(비만)"
        else:
            out["dosing_weight"], out["dosing_weight_kind"] = round(ibw, 1), "이상 체중"
    else:
        out["dosing_weight"], out["dosing_weight_kind"] = p.weight_kg, "실제 체중(키 미입력)"
    return out


def assess(p: Patient) -> dict:
    """-> {"body", "kidney", "liver", "capacity", "diseases", "flags", "alerts"}"""
    alerts: list[str] = []
    b = body(p)

    # ---- kidney: eGFR (per 1.73 m2) + what matters for drug elimination: absolute clearance in mL/min
    child = p.age is not None and p.age < ADULT_AGE
    egfr, src = p.egfr, "입력값" if p.egfr is not None else None
    if egfr is None and p.scr is not None and p.age is not None:
        if not child:
            egfr, src = ckd_epi_2021(p.scr, p.age, p.sex), "CKD-EPI 2021"
        elif p.height_cm:
            egfr, src = schwartz_bedside(p.scr, p.height_cm), "Schwartz(소아)"
        else:
            alerts.append("18세 미만은 키를 함께 입력해야 신기능을 추정할 수 있습니다(소아용 Schwartz 식).")
    kidney = None
    if egfr is not None:
        crcl = None
        if p.scr and p.age is not None and b and not child:
            crcl = (140 - p.age) * b["dosing_weight"] / (72 * p.scr) * (0.85 if p.sex == "F" else 1)
        # children: drug doses are scaled per 1.73 m2 / per kg, so the normalised eGFR itself is the measure
        egfr_abs = egfr * b["bsa"] / 1.73 if b and "bsa" in b and not child else None
        clearance = crcl if crcl is not None else egfr_abs if egfr_abs is not None else egfr
        g, desc = kdigo_stage(egfr)  # CKD staging is defined on eGFR; doses follow the clearance below
        kidney = {"egfr": round(egfr, 1), "egfr_abs": round(egfr_abs, 1) if egfr_abs else None,
                  "crcl": round(crcl, 1) if crcl is not None else None, "clearance": round(clearance, 1),
                  # egfr_abs = eGFR scaled back from 1.73 m2 to this person's body surface (mL/min, not per 1.73 m2)
                  "clearance_kind": "크레아티닌 청소율(Cockcroft-Gault)" if crcl is not None else
                  "본인 체표면적 기준 eGFR" if egfr_abs else "eGFR(소아, Schwartz)" if src == "Schwartz(소아)" else "eGFR",
                  "pct_normal": round(min(clearance / 100 * 100, 150)), "stage": g, "desc": desc,
                  "impaired": clearance < 60, "source": src}
        if clearance < 30:
            alerts.append(f"콩팥이 약을 걸러내는 능력이 정상의 약 {kidney['pct_normal']}%입니다 — 신배설 약물은 감량하거나 피해야 합니다.")
        elif clearance < 60:
            alerts.append(f"콩팥 기능이 정상의 약 {kidney['pct_normal']}%입니다 — 신배설 약물의 용량 조절이 필요할 수 있습니다.")

    # ---- liver
    liver = None
    ratios = {k: v / u for k, v, u in (("AST", p.ast, AST_ULN), ("ALT", p.alt, ALT_ULN),
                                        ("빌리루빈", p.total_bilirubin, BILI_ULN)) if v is not None}
    if ratios:
        trans = max([ratios.get("AST", 0), ratios.get("ALT", 0)])
        bili = ratios.get("빌리루빈", 0)
        if trans >= 3 and bili >= 2:
            sev, desc = 3, "심한 간 손상 의심 (Hy's law 범위)"
        elif trans >= 5 or bili >= 3:
            sev, desc = 2, "중등도 이상 간기능 저하"
        elif trans >= 3 or bili >= 1.5:
            sev, desc = 1, "경도 간기능 저하"
        else:
            sev, desc = 0, "정상 범위"
        liver = {"severity": sev, "desc": desc, "impaired": sev >= 1,
                 "uln_multiples": {k: round(v, 2) for k, v in ratios.items()}}
        if sev >= 1:
            alerts.append(f"간 수치: {desc} — 간에서 분해되는 약이 몸에 더 오래 남고 간 독성 위험이 커집니다.")

    dis = disease_ids(p.conditions, p.diagnosis)
    liver_disease = bool(dis & {"hepatic", "cirrhosis"})  # ticked / named by the user (not only a lab value)
    # Child-Pugh: what drug labels grade hepatic dosing by (A mild, B moderate, C severe)
    cp = child_pugh(p, liver_disease)
    if cp:
        cp_sev = {"A": 1, "B": 2, "C": 3}[cp["class"]]
        cp_desc = {"A": "경도", "B": "중등도", "C": "고도"}[cp["class"]]
        liver = liver or {"severity": 0, "desc": "정상 범위", "impaired": False, "uln_multiples": {}}
        liver["child_pugh"] = cp
        if cp_sev >= liver["severity"]:
            liver["severity"] = cp_sev
            liver["desc"] = f"Child-Pugh {cp['class']}" + (f"({cp['score']}점)" if cp["score"] else "") + \
                f" — {cp_desc} 간기능 저하"
        liver["impaired"] = True
        note = (f" ({'·'.join(cp['assumed'])}{'를' if cp['assumed'][-1] == '복수' else '을'} 입력하지 않아 '없음'으로 "
                "계산 — 실제 등급은 더 높을 수 있음)" if cp["assumed"] else "")
        alerts.append(f"간기능 Child-Pugh {cp['class']}{note}: 간에서 분해되는 약은 허가사항의 간장애 용량을 따르고, "
                      + ("일부 약은 권장되지 않습니다." if cp["class"] != "A" else "필요하면 감량합니다."))
    elif p.albumin is not None or p.inr is not None:
        alerts.append("알부민·INR은 간경변 등 만성 간질환이 있을 때만 Child-Pugh 등급으로 계산합니다 — "
                      "'간질환'을 선택하면 반영됩니다.")

    # ---- diseases -> model flags
    if kidney and kidney["impaired"]:
        dis.add("renal")
    if kidney and kidney["clearance"] < 30:  # labels' 'severe renal impairment' threshold
        dis.add("renal_severe")
    if liver and liver["impaired"]:
        dis.add("hepatic")
    # 'active liver disease' of the statin / metformin / NSAID ... contraindications: a known liver disease with
    # bilirubin >= 2 mg/dL (Child-Pugh 2+ points) or transaminases >= 3x ULN, or values that are severe on their own
    if liver and (liver["severity"] >= 2 or (liver_disease and (
            (p.total_bilirubin or 0) >= 2.0 or max(liver["uln_multiples"].get(k, 0) for k in ("AST", "ALT")) >= 3))):
        dis.add("cirrhosis")  # (Child-Pugh B / C is severity >= 2: the labels' 'moderate-severe hepatic impairment')
    if "cirrhosis" in dis:  # (ticked as 간경변 / dialysis): the milder group's rules apply too
        dis.add("hepatic")
    if "renal_severe" in dis:
        dis.add("renal")
    flags = dict.fromkeys(COMORBIDITIES, False)
    for d in dis:
        f = DISEASES[d]["flag"]
        if f in flags:
            flags[f] = True

    # ---- drug-handling capacity (plain-language summary, reference only)
    score, why = 100.0, []
    if p.age is not None and p.age > 65:
        score -= min(30, (p.age - 65) * 1.2)
        why.append(f"{p.age:.0f}세 고령")
    if kidney and kidney["clearance"] < 90:
        score -= min(40, (90 - kidney["clearance"]) * 0.55)
        why.append(f"콩팥 기능 정상의 {kidney['pct_normal']}%")
    if liver and liver["severity"]:
        score -= (0, 15, 30, 45)[liver["severity"]]
        why.append(liver["desc"])
    if b and b.get("bmi"):
        if b["bmi"] < 18.5:
            score -= 10
            why.append("저체중(약물이 상대적으로 과량이 되기 쉬움)")
        elif b["bmi"] >= 30:
            score -= 5
            why.append("고도비만")
    n_dis = len(dis - {"renal", "hepatic"})
    if n_dis:
        score -= min(15, 3 * n_dis)
        why.append(f"기저질환 {n_dis}개")
    score = max(0.0, min(100.0, score))
    level = "좋음" if score >= 75 else "보통" if score >= 50 else "저하" if score >= 25 else "매우 저하"
    capacity = {"score": round(score), "level": level, "factors": why,
                "text": {"좋음": "약을 분해·배출하는 능력이 충분합니다.",
                         "보통": "약을 처리하는 능력이 다소 떨어져 있어 용량·조합에 주의가 필요합니다.",
                         "저하": "약이 몸에 쌓이기 쉬운 상태입니다. 용량 감량과 조합 점검이 필요합니다.",
                         "매우 저하": "약에 매우 민감한 상태입니다. 모든 약을 전문가와 확인하세요."}[level]}
    if p.age is not None and p.age >= 75:
        alerts.append("75세 이상: 졸음·어지러움을 일으키는 약은 낙상·섬망 위험이 큽니다 (Beers 기준).")
    return {"egfr": kidney["egfr"] if kidney else None, "body": b, "kidney": kidney, "liver": liver,
            "capacity": capacity, "diseases": sorted(dis), "flags": flags, "alerts": alerts}
