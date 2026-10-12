"""Dose and duration checks (adults).

Inputs per product: amount per dose, doses per day, days, regular vs. as-needed. With the product's
ingredient strengths this gives mg/day per ingredient, SUMMED across products (two cold medicines +
paracetamol). Checked against:
  * maximum daily dose, lowered for kidney function (Cockcroft-Gault CrCl), liver impairment, age, sex, weight
  * dose-dependent interactions (e.g. simvastatin > 20 mg/day with amlodipine)
  * dosing-frequency errors (methotrexate is weekly) and long-term-use limits (NSAIDs, decongestants, ...)

Values follow FDA/MFDS labelling and widely used references (Beers 2023, KDIGO). They are screening
limits for adults; paediatric dosing is out of scope. Local protocols take precedence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

SRC_LABEL = "허가사항(라벨) 1일 최대용량"


class Dose(BaseModel):
    amount: float = Field(..., gt=0, le=100000, description="amount per dose")
    unit: str = Field("unit", description="'unit' (tablet/capsule/sachet...), 'mL', 'g' or 'mg'")
    per_day: float = Field(1, gt=0, le=24, description="doses per day (max per day when as-needed)")
    days: int | None = Field(None, ge=1, le=3650, description="planned duration in days")
    prn: bool = Field(False, description="as needed (occasional) instead of regular")
    weekly: bool = Field(False, description="dose is per WEEK (e.g. methotrexate) instead of per day")


@dataclass
class Limit:
    max_mg: float
    warn_mg: float | None = None  # e.g. OTC ceiling: above it needs medical supervision
    single_max: float | None = None
    adjust: list[tuple[str, float, str]] = field(default_factory=list)  # (condition, max, reason)


# condition syntax: crcl<30, age>=75, hepatic, female, weight<50
DOSE_LIMITS: dict[str, Limit] = {
    # analgesics / NSAIDs
    "acetaminophen": Limit(4000, warn_mg=3000, single_max=1000, adjust=[
        ("hepatic", 2000, "간기능 저하"), ("age>=75", 3000, "75세 이상"), ("weight<50", 3000, "체중 50kg 미만")]),
    "ibuprofen": Limit(3200, warn_mg=1200, single_max=800, adjust=[("crcl<30", 1200, "신기능 고도 저하")]),
    "dexibuprofen": Limit(1200, single_max=400),
    "naproxen": Limit(1500, warn_mg=660),
    "aspirin": Limit(4000, single_max=1000),
    "loxoprofen": Limit(180, single_max=60),
    "celecoxib": Limit(400, adjust=[("hepatic", 200, "간기능 저하(중등도): 50% 감량")]),
    "diclofenac": Limit(150),
    "meloxicam": Limit(15, adjust=[("crcl<30", 7.5, "신기능 고도 저하")]),
    "ketorolac": Limit(40, single_max=10),
    "tramadol": Limit(400, single_max=100, adjust=[("age>=75", 300, "75세 이상"), ("crcl<30", 200, "신기능 고도 저하")]),
    # cold / allergy
    "dextromethorphan": Limit(120),
    "pseudoephedrine": Limit(240),
    "phenylephrine": Limit(60),
    "chlorpheniramine": Limit(24),
    "diphenhydramine": Limit(300),
    "guaifenesin": Limit(2400),
    "caffeine": Limit(1200, warn_mg=400),
    "cetirizine": Limit(10, adjust=[("crcl<31", 5, "신기능 저하"), ("hepatic", 5, "간기능 저하")]),
    "levocetirizine": Limit(5, adjust=[("crcl<80", 2.5, "신기능 경도 저하"), ("crcl<50", 1.25, "신기능 중등도 저하")]),
    "loratadine": Limit(10, adjust=[("hepatic", 5, "간기능 저하(격일 10mg)"), ("crcl<30", 5, "신기능 고도 저하")]),
    "fexofenadine": Limit(180, adjust=[("crcl<50", 60, "신기능 저하")]),
    "famotidine": Limit(80, adjust=[("crcl<60", 40, "신기능 저하"), ("crcl<30", 20, "신기능 고도 저하")]),
    # CNS
    "zolpidem": Limit(10, adjust=[("female", 5, "여성(아침 졸림 위험)"), ("age>=65", 5, "65세 이상"),
                                 ("hepatic", 5, "간기능 저하")]),
    "alprazolam": Limit(10, warn_mg=4, adjust=[("age>=65", 2, "65세 이상"), ("hepatic", 2, "간기능 저하")]),
    "lorazepam": Limit(10),
    "diazepam": Limit(40),
    "citalopram": Limit(40, adjust=[("age>60", 20, "60세 초과 (QT 연장)"), ("hepatic", 20, "간기능 저하")]),
    "escitalopram": Limit(20, adjust=[("age>=65", 10, "65세 이상"), ("hepatic", 10, "간기능 저하")]),
    "sertraline": Limit(200),
    "fluoxetine": Limit(80),
    "paroxetine": Limit(50, adjust=[("age>=65", 40, "65세 이상"), ("crcl<30", 40, "신기능 고도 저하")]),
    "duloxetine": Limit(120),
    "venlafaxine": Limit(375),
    "bupropion": Limit(450, single_max=150),
    "mirtazapine": Limit(45),
    "quetiapine": Limit(800),
    "gabapentin": Limit(3600, adjust=[("crcl<60", 1400, "신기능 저하(CrCl 30-59)"), ("crcl<30", 700, "CrCl 15-29"),
                                      ("crcl<15", 300, "CrCl <15")]),
    "pregabalin": Limit(600, adjust=[("crcl<60", 300, "신기능 저하(CrCl 30-60)"), ("crcl<30", 150, "CrCl 15-30"),
                                     ("crcl<15", 75, "CrCl <15")]),
    # cardiovascular / metabolic
    "digoxin": Limit(0.25, adjust=[("age>=75", 0.125, "75세 이상 (Beers)"), ("crcl<30", 0.125, "신기능 고도 저하")]),
    "amlodipine": Limit(10),
    "losartan": Limit(100),
    "valsartan": Limit(320),
    "lisinopril": Limit(80),
    "enalapril": Limit(40),
    "bisoprolol": Limit(20),
    "atorvastatin": Limit(80),
    "rosuvastatin": Limit(40, warn_mg=20, adjust=[("crcl<30", 10, "신기능 고도 저하")]),
    "simvastatin": Limit(40),
    "pravastatin": Limit(80),
    "metformin": Limit(2550, adjust=[("crcl<45", 1000, "신기능 저하(eGFR 30-45)")]),
    "glimepiride": Limit(8),
    "sitagliptin": Limit(100, adjust=[("crcl<45", 50, "신기능 저하"), ("crcl<30", 25, "신기능 고도 저하")]),
    "allopurinol": Limit(800),
    "colchicine": Limit(1.2, adjust=[("crcl<30", 0.3, "신기능 고도 저하")]),
    "rivaroxaban": Limit(20, adjust=[("crcl<50", 15, "신기능 저하(CrCl 15-50)")]),
    "edoxaban": Limit(60, adjust=[("crcl<50", 30, "신기능 저하(CrCl 15-50)"), ("weight<=60", 30, "체중 60kg 이하")]),
    "apixaban": Limit(10),  # 2.5 mg bid rule (age/weight/creatinine) handled in check_doses
    # anti-infectives
    "ciprofloxacin": Limit(1500, adjust=[("crcl<50", 1000, "신기능 저하"), ("crcl<30", 500, "신기능 고도 저하")]),
    "clarithromycin": Limit(1000, adjust=[("crcl<30", 500, "신기능 고도 저하")]),
    "nirmatrelvir": Limit(600, adjust=[("crcl<60", 300, "신기능 저하(eGFR 30-60)")]),
    # misc
    "montelukast": Limit(10),
    "sildenafil": Limit(100),
    "tadalafil": Limit(20),
    "metoclopramide": Limit(40, adjust=[("crcl<60", 20, "신기능 저하"), ("age>=65", 20, "65세 이상")]),
}

# (drug, partner drugs, max mg/day of `drug` when combined, reason) - FDA labelling
DOSE_INTERACTIONS: list[tuple[str, frozenset[str], float, str]] = [
    ("simvastatin", frozenset({"amlodipine", "ranolazine", "amiodarone", "lomitapide"}), 20, "근육 손상(횡문근융해) 위험"),
    ("simvastatin", frozenset({"diltiazem", "verapamil", "dronedarone"}), 10, "근육 손상(횡문근융해) 위험"),
    ("lovastatin", frozenset({"amiodarone"}), 40, "근육 손상 위험"),
    ("lovastatin", frozenset({"diltiazem", "verapamil"}), 20, "근육 손상 위험"),
    ("atorvastatin", frozenset({"clarithromycin", "itraconazole"}), 20, "근육 손상 위험"),
    ("rosuvastatin", frozenset({"cyclosporine"}), 5, "근육 손상 위험"),
    ("rosuvastatin", frozenset({"gemfibrozil"}), 10, "근육 손상 위험"),
    ("rosuvastatin", frozenset({"clopidogrel"}), 20, "로수바스타틴 혈중농도 상승"),
    ("sildenafil", frozenset({"ritonavir", "ketoconazole", "itraconazole", "clarithromycin"}), 25, "저혈압·실신 위험"),
    ("tadalafil", frozenset({"ritonavir", "ketoconazole", "itraconazole", "clarithromycin"}), 10, "저혈압 위험"),
    ("colchicine", frozenset({"clarithromycin", "ketoconazole", "itraconazole", "ritonavir"}), 0.6, "콜히친 중독 위험"),
]

WEEKLY_ONLY = {"methotrexate": "메토트렉세이트는 보통 '주 1회' 복용합니다. 매일 복용하면 치명적인 골수·간 독성이 생길 수 있습니다"}

# regular use longer than N days -> (severity, reason)
DURATION_RULES: list[tuple[str, int, str, str]] = [
    ("nsaid", 10, "주의", "소염진통제를 10일 넘게 계속 먹으면 위궤양·출혈, 신장 손상, 심혈관 위험이 커집니다"),
    ("ketorolac", 5, "금기", "케토롤락은 5일을 넘겨 쓰면 안 됩니다(허가사항 박스경고)"),
    ("acetaminophen", 10, "주의", "해열진통제를 10일 넘게 매일 먹는 경우 의사와 상의가 필요합니다"),
    ("decongestant", 7, "주의", "코막힘약을 7일 넘게 쓰면 혈압 상승·불면이 생기기 쉽습니다"),
    ("antitussive", 7, "주의", "기침약을 7일 넘게 쓸 정도면 원인 확인이 필요합니다"),
    ("sedative", 28, "주의", "수면·진정제를 4주 넘게 쓰면 의존성·낙상 위험이 커집니다"),
    ("opioid", 7, "주의", "마약성 진통제를 1주 넘게 쓰면 의존성 위험이 커집니다"),
    ("ppi", 56, "주의", "위산억제제(PPI)를 8주 넘게 쓰면 골절·장염·비타민 B12 결핍 위험이 있습니다"),
    ("corticosteroid", 21, "주의", "스테로이드를 3주 넘게 쓰면 갑자기 끊으면 안 됩니다(부신 기능 억제)"),
    ("metoclopramide", 84, "금기", "메토클로프라미드는 12주를 넘겨 쓰면 지연성 운동장애 위험이 있습니다(박스경고)"),
]
PPI = frozenset({"omeprazole", "esomeprazole", "pantoprazole", "lansoprazole", "rabeprazole", "dexlansoprazole"})


def _holds(cond: str, ctx: dict) -> bool:
    if cond == "hepatic":
        return bool(ctx.get("hepatic"))
    if cond == "female":
        return ctx.get("sex") == "F"
    m = re.match(r"(crcl|age|weight)(<=|>=|<|>)([\d.]+)", cond)
    if not m:
        return False
    v = ctx.get(m.group(1))
    if v is None:
        return False
    x, op = float(m.group(3)), m.group(2)
    return {"<": v < x, "<=": v <= x, ">": v > x, ">=": v >= x}[op]


# Filled by the engine from the MFDS DUR list (용량주의 = maximum daily dose, 투여기간주의 = maximum duration) for
# ingredients the curated tables above do not cover.  name -> Limit / (days, ingredient Korean name)
EXTRA_LIMITS: dict[str, Limit] = {}
EXTRA_DURATION: dict[str, int] = {}


# Extended-release products whose labelled maximum (or single dose) differs from the ordinary tablet (FDA / MFDS labels)
ER_LIMITS: dict[str, Limit] = {
    "metformin": Limit(2000, adjust=[("crcl<45", 1000, "신기능 저하(eGFR 30-45)")]),
    "tramadol": Limit(300, single_max=300, adjust=[("crcl<30", 0.001, "신기능 고도 저하 — 서방형 사용 안 함")]),
    "zolpidem": Limit(12.5, adjust=[("female", 6.25, "여성(아침 졸림 위험)"), ("age>=65", 6.25, "65세 이상"),
                                   ("hepatic", 6.25, "간기능 저하")]),
    "venlafaxine": Limit(225),
    "bupropion": Limit(450, single_max=450),
    "paroxetine": Limit(62.5, adjust=[("age>=65", 50, "65세 이상")]),
    "glipizide": Limit(20),
    "methylphenidate": Limit(72),
    "doxazosin": Limit(8),
    "pregabalin": Limit(660, adjust=[("crcl<60", 330, "신기능 저하(CrCl 30-60)"), ("crcl<30", 0.001, "CrCl <30 — 서방형 사용 안 함")]),
    "gabapentin": Limit(1800, adjust=[("crcl<60", 0.001, "CrCl <60 — 서방형 사용 안 함")]),
    "acetaminophen": Limit(4000, warn_mg=3900, single_max=1300, adjust=[
        ("hepatic", 2000, "간기능 저하"), ("age>=75", 3000, "75세 이상"), ("weight<50", 3000, "체중 50kg 미만")]),
    "nifedipine": Limit(120),
}
# extended-release products are taken once or twice a day (8-hourly acetaminophen ER: 3 times)
ER_PER_DAY = {"acetaminophen": 3}


def effective_limit(key: str, ctx: dict, er: bool = False) -> tuple[float, float | None, float | None, list[str]] | None:
    name = key.removeprefix("ing:").removeprefix("lab:").removeprefix("bio:")
    lim = (ER_LIMITS.get(name) if er else None) or DOSE_LIMITS.get(name) or EXTRA_LIMITS.get(name)
    if lim is None:
        return None
    mx, why = lim.max_mg, []
    for cond, m, reason in lim.adjust:
        if _holds(cond, ctx) and m < mx:
            mx = m
            why.append(reason)
    warn = lim.warn_mg if lim.warn_mg and lim.warn_mg < mx else None
    return mx, warn, lim.single_max, why


def fmt_mg(x: float) -> str:
    return f"{x:,.0f}mg" if x >= 10 else f"{x:.3g}mg"


def limit_text(display: str, lim: tuple) -> str:
    """One line for the AI 상담: the self-medication (OTC) limit first, the prescription maximum second."""
    mx, warn, single, why = lim
    parts = ([f"스스로 사 먹을 때(일반약 기준) 하루 {fmt_mg(warn)}까지", f"의사 지시가 있을 때 하루 최대 {fmt_mg(mx)}"]
             if warn else [f"하루 최대 {fmt_mg(mx)}"])
    if single:  # with an OTC ceiling the single maximum is the prescription one ('1회 800mg' is not self-medication)
        parts.append(f"{'의사 지시가 있을 때 ' if warn else ''}1회 최대 {fmt_mg(single)}")
    return f"{display}: {' · '.join(parts)}" + (f" — {', '.join(why)} 반영" if why else "")


def check_doses(items: list[dict], ctx: dict, groups_of, display_of) -> dict:
    """items: [{"input": product name, "keys": [ingredient keys], "dose": Dose | None,
                "strength": {"unit", "per", "mg": {key: mg}} | None}]
    ctx: {"crcl", "age", "sex", "weight", "hepatic"}; groups_of(key) -> set of class/group ids;
    display_of(key) -> display name.
    -> {"ingredients": [...daily totals vs limits...], "alerts": [{"sev": "금기"|"주의", "text"}], "missing": [...]}"""
    totals: dict[str, dict] = {}
    alerts: list[dict] = []
    missing: list[str] = []
    for it in items:
        d: Dose | None = it.get("dose")
        if d is None:
            continue
        st = it.get("strength") or {}
        for k in it["keys"]:
            per_dose = None
            if d.unit == "mg":
                if len(it["keys"]) == 1:
                    per_dose = d.amount
            elif st.get("mg", {}).get(k) is not None:
                per = st.get("per", "unit")
                if (d.unit == "unit" and per == "unit") or (d.unit == per):
                    per_dose = st["mg"][k] * d.amount
            if per_dose is None:
                missing.append(it["input"])
                continue
            per_day = d.per_day / 7 if d.weekly else d.per_day
            er = it.get("release") == "ER"
            t = totals.setdefault(k, {"key": k, "display": display_of(k), "daily_mg": 0.0, "single_max_dose": 0.0,
                                      "single_ir": 0.0, "single_er": 0.0, "er": False, "ir": False,
                                      "sources": [], "days": 0, "regular": False})
            t["daily_mg"] += per_dose * per_day
            t["single_max_dose"] = max(t["single_max_dose"], per_dose)
            t["single_er" if er else "single_ir"] = max(t["single_er" if er else "single_ir"], per_dose)
            t["er" if er else "ir"] = True
            t["sources"].append({"product": it["input"], "per_dose_mg": round(per_dose, 3),
                                 "per_day": d.per_day, "weekly": d.weekly, "release": it.get("release") or "IR"})
            name0 = k.removeprefix("ing:").removeprefix("lab:").removeprefix("bio:")
            cap = ER_PER_DAY.get(name0, 2)
            if er and not d.weekly and d.per_day > cap and not any(a["text"].startswith(it["input"]) for a in alerts):
                alerts.append({"sev": "주의", "text": f"{it['input']}: 서방형인데 하루 {d.per_day:g}회 — 서방형은 보통 하루 "
                               f"{cap}회 이하로 복용합니다(약이 겹쳐 한꺼번에 많이 노출될 수 있음)"})
            t["days"] = max(t["days"], d.days or 0)
            t["regular"] = t["regular"] or not d.prn
            # weekly-only drugs taken daily
            name = k.removeprefix("ing:")
            if name in WEEKLY_ONLY and not d.weekly:
                alerts.append({"sev": "금기", "text": f"{display_of(k)}: {WEEKLY_ONLY[name]} — 현재 '매일' 복용으로 입력됨"})

    out = []
    for k, t in totals.items():
        name = k.removeprefix("ing:").removeprefix("lab:").removeprefix("bio:")
        lim_ir = effective_limit(k, ctx) if t["ir"] else None
        lim_er = effective_limit(k, ctx, er=True) if t["er"] else None
        er_own = t["er"] and name in ER_LIMITS
        # ordinary + extended-release of the same ingredient: the stricter daily maximum applies
        lim = min([x for x in (lim_ir, lim_er) if x], key=lambda x: x[0], default=None)
        row = {**t, "daily_mg": round(t["daily_mg"], 3), "max_mg": None, "status": "기준 없음", "why": [],
               "source": ("서방형 허가사항 1일 최대용량" if er_own and lim is lim_er else
                          "식약처 DUR 용량주의" if name not in DOSE_LIMITS and name in EXTRA_LIMITS else SRC_LABEL)}
        if lim:
            mx, warn, _, why = lim
            row.update(max_mg=mx, warn_mg=warn, single_max=(lim_er or lim_ir)[2], why=why)
            many = len(t["sources"]) > 1
            src = f" ({' + '.join(s['product'] for s in t['sources'])} 합계)" if many else ""
            if mx < 0.01:  # 'do not use the extended-release form' (e.g. pregabalin ER with CrCl < 30)
                row["status"] = "초과"
                alerts.append({"sev": "금기", "text": f"{t['display']} 서방형 — {', '.join(why)}: 서방형은 쓰지 않고 일반형으로 "
                               "용량을 조절합니다"})
            elif t["daily_mg"] > mx * 1.001:
                row["status"] = "초과"
                alerts.append({"sev": "금기", "text": f"{t['display']}{' 서방형' if lim is lim_er and er_own else ''} 하루 "
                               f"{fmt_mg(t['daily_mg'])}{src} — 1일 최대 {fmt_mg(mx)}"
                               f"{' (' + ', '.join(why) + ')' if why else ''}를 넘습니다"})
            elif warn and t["daily_mg"] > warn * 1.001:
                row["status"] = "주의"
                alerts.append({"sev": "주의", "text": f"{t['display']} 하루 {fmt_mg(t['daily_mg'])}{src} — 일반약 기준"
                               f" {fmt_mg(warn)}을 넘어 의사·약사의 지시가 있을 때만 가능한 용량입니다"})
            else:
                row["status"] = "적정"
            for kind, lm, dose1 in (("", lim_ir, t["single_ir"]), (" 서방형", lim_er, t["single_er"])):
                single = lm[2] if lm else None
                if single and dose1 > single * 1.001:
                    alerts.append({"sev": "주의", "text": f"{t['display']}{kind} 1회 {fmt_mg(dose1)} — "
                                   f"1회 최대 {fmt_mg(single)}를 넘습니다"})
                    if row["status"] == "적정":
                        row["status"] = "주의"
        out.append(row)

    # dose-dependent interactions
    present = {k.removeprefix("ing:") for k in totals} | set(ctx.get("all_keys", []))
    for drug, partners, mx, why in DOSE_INTERACTIONS:
        t = totals.get(drug)
        hit = sorted(partners & present)
        if t and hit and t["daily_mg"] > mx * 1.001:
            alerts.append({"sev": "금기", "text": f"{t['display']} 하루 {fmt_mg(t['daily_mg'])} + {', '.join(hit)} — "
                           f"함께 쓸 때는 하루 {fmt_mg(mx)} 이하로 줄여야 합니다({why})"})
    # apixaban 2.5 mg bid when >= 2 of: age >= 80, weight <= 60 kg, creatinine >= 1.5 mg/dL
    t = totals.get("apixaban")
    if t:
        n = sum([(ctx.get("age") or 0) >= 80, (ctx.get("weight") or 999) <= 60, (ctx.get("scr") or 0) >= 1.5])
        if n >= 2 and t["daily_mg"] > 5.001:
            alerts.append({"sev": "금기", "text": f"아픽사반 하루 {fmt_mg(t['daily_mg'])} — 80세 이상·체중 60kg 이하·"
                           "크레아티닌 1.5 이상 중 2가지 이상이면 1회 2.5mg 하루 2회(5mg)로 줄여야 합니다"})
    # interaction that depends on regular, higher-dose use
    a, w = totals.get("acetaminophen"), "warfarin" in present
    if a and w and a["regular"] and a["daily_mg"] >= 2000 and a["days"] >= 3:
        alerts.append({"sev": "주의", "text": f"아세트아미노펜을 하루 {fmt_mg(a['daily_mg'])}씩 {a['days']}일 규칙적으로 먹으면 "
                       "와파린 효과(INR)가 올라갈 수 있습니다 — INR 검사를 받으세요"})
    # long-term use
    for k, t in totals.items():
        if not t["regular"] or not t["days"]:
            continue
        groups = groups_of(k) | ({"ppi"} if k in PPI else set())
        for grp, limit_days, sev, why in DURATION_RULES:
            if grp in groups and t["days"] > limit_days:
                alerts.append({"sev": sev, "text": f"{t['display']} {t['days']}일 복용 — {why}"})
                break
        else:
            dmax = EXTRA_DURATION.get(k.removeprefix("ing:").removeprefix("lab:").removeprefix("bio:"))
            if dmax and t["days"] > dmax:
                alerts.append({"sev": "주의", "text": f"{t['display']} {t['days']}일 복용 — 식약처 DUR 투여기간주의: "
                               f"한 번에 최대 {dmax}일까지 권장됩니다"})
    return {"ingredients": sorted(out, key=lambda r: {"초과": 0, "주의": 1, "적정": 2}.get(r["status"], 3)),
            "alerts": alerts, "missing": sorted(set(missing))}
