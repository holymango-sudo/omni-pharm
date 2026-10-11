"""'증상으로 약 찾기': over-the-counter options for common symptoms, screened for THIS patient.

The symptom -> ingredient table follows standard self-care references (first choice / alternative). Every candidate is
then checked with the same evidence the verdict uses - MFDS DUR, label interactions and clinical rules against the
medicines already entered, same ingredient / same class duplication, the patient's diseases, age and pregnancy - and
shown as 적합 / 주의 / 피하세요 with the reason. Suggestions are self-care options to discuss with a pharmacist, not a
prescription; each symptom also lists when to see a doctor instead.
"""

from __future__ import annotations

import re

from omnipharm.clinical import Patient, assess
from omnipharm.knowledge import DRUG_CLASSES, drug_class, drug_groups

# symptom id -> (Korean name, group, icon, see-a-doctor-when, [(ingredient key, 1=first choice / 2=alternative, what it does)])
SYMPTOMS: dict[str, dict] = {
    "fever": {"ko": "발열·몸살", "group": "감기", "icon": "🤒", "doctor": "39도 이상, 3일 넘는 열, 숨이 차거나 의식이 흐려지면 진료",
              "options": [("acetaminophen", 1, "열을 내리고 통증을 줄입니다 — 위 부담이 적어 가장 무난"),
                          ("ibuprofen", 2, "열·통증·염증을 줄입니다 — 식후 복용, 위장 장애 주의"),
                          ("ing:dexibuprofen", 2, "이부프로펜의 활성 성분 — 적은 용량으로 같은 효과")]},
    "sore_throat": {"ko": "목 아픔", "group": "감기", "icon": "😣", "doctor": "침을 삼키기 힘들 만큼 붓거나 고열·발진이 있으면 진료",
                    "options": [("ibuprofen", 1, "목의 염증과 통증을 함께 줄입니다"),
                                ("acetaminophen", 1, "통증과 열을 줄입니다")]},
    "runny_nose": {"ko": "콧물·재채기", "group": "감기", "icon": "🤧", "doctor": "누런 콧물·얼굴 통증이 10일 넘으면 진료(축농증)",
                   "options": [("cetirizine", 1, "콧물·재채기를 막는 2세대 항히스타민 — 졸음 적음"),
                               ("fexofenadine", 1, "졸음이 가장 적은 항히스타민"),
                               ("loratadine", 1, "하루 1번, 졸음 거의 없음"),
                               ("chlorpheniramine", 2, "효과가 빠르지만 졸음·입마름 — 운전 전·고령자 피함")]},
    "stuffy_nose": {"ko": "코막힘", "group": "감기", "icon": "👃", "doctor": "1주 넘게 계속되면 진료",
                    "options": [("pseudoephedrine", 1, "코 점막 혈관을 좁혀 코막힘을 풉니다 — 혈압·불면 주의"),
                                ("oxymetazoline", 2, "코 스프레이 — 3일 이내만(오래 쓰면 더 막힘)")]},
    "cough": {"ko": "마른기침", "group": "감기", "icon": "😷", "doctor": "3주 넘는 기침, 피 섞인 가래, 숨참이 있으면 진료",
              "options": [("dextromethorphan", 1, "뇌의 기침 중추를 진정시킵니다 — 가래 없는 기침에"),
                          ("ing:levodropropizine", 2, "기관지 신경에 작용 — 졸음이 적음")]},
    "phlegm": {"ko": "가래", "group": "감기", "icon": "🫁", "doctor": "누렇거나 피 섞인 가래, 열이 계속되면 진료",
               "options": [("acetylcysteine", 1, "가래를 묽게 해 잘 나오게 합니다"),
                           ("ambroxol", 1, "가래 배출을 돕습니다"),
                           ("guaifenesin", 2, "가래를 묽게 합니다")]},
    "headache": {"ko": "두통", "group": "통증", "icon": "🤕", "doctor": "갑자기 생긴 극심한 두통, 마비·말 어눌함이 있으면 즉시 응급실",
                 "options": [("acetaminophen", 1, "두통에 무난한 진통제"), ("ibuprofen", 1, "염증성·긴장성 두통에 효과적"),
                             ("naproxen", 2, "효과가 오래 갑니다(하루 2번)")]},
    "period": {"ko": "생리통", "group": "통증", "icon": "🩸", "doctor": "진통제로 조절되지 않거나 점점 심해지면 진료",
               "options": [("ibuprofen", 1, "통증 물질(프로스타글란딘)을 줄여 생리통에 특히 효과적"),
                           ("naproxen", 1, "오래 가는 소염진통제"), ("acetaminophen", 2, "위가 약할 때")]},
    "muscle": {"ko": "근육통·관절통", "group": "통증", "icon": "💪", "doctor": "붓고 열나는 관절, 다친 뒤 심한 통증은 진료",
               "options": [("ibuprofen", 1, "염증과 통증을 줄입니다"), ("naproxen", 2, "오래 가는 소염진통제"),
                           ("acetaminophen", 2, "염증은 없고 통증만 있을 때")]},
    "toothache": {"ko": "치통", "group": "통증", "icon": "🦷", "doctor": "진통제는 임시 — 치과 진료 필요",
                  "options": [("ibuprofen", 1, "잇몸·치아 염증 통증에 효과적"), ("acetaminophen", 1, "통증을 줄입니다")]},
    "heartburn": {"ko": "속쓰림·위산", "group": "소화", "icon": "🔥", "doctor": "검은 변, 피 토함, 2주 넘는 속쓰림은 진료",
                  "options": [("famotidine", 1, "위산 분비를 줄입니다 — 몇 시간 지속"),
                              ("calcium carbonate", 1, "제산제 — 위산을 바로 중화(짧게 작용)"),
                              ("magnesium hydroxide", 2, "제산제 — 설사가 날 수 있음")]},
    "indigestion": {"ko": "더부룩함·가스", "group": "소화", "icon": "🍽", "doctor": "체중 감소·구토·삼키기 힘듦이 있으면 진료",
                    "options": [("simethicone", 1, "장내 가스를 없앱니다"), ("pancreatin", 2, "소화 효소를 보충합니다")]},
    "diarrhea": {"ko": "설사", "group": "소화", "icon": "💧", "doctor": "피 섞인 설사, 고열, 2일 넘는 설사, 탈수는 진료",
                 "options": [("loperamide", 1, "장 운동을 늦춥니다 — 열·혈변이 있으면 쓰지 마세요")]},
    "constipation": {"ko": "변비", "group": "소화", "icon": "🚽", "doctor": "갑자기 생긴 변비, 혈변, 심한 복통은 진료",
                     "options": [("magnesium oxide", 1, "장에 수분을 끌어들여 변을 부드럽게 — 콩팥이 약하면 주의"),
                                 ("lactulose", 2, "변을 부드럽게 합니다"), ("bisacodyl", 2, "장을 자극 — 단기간만")]},
    "allergy": {"ko": "두드러기·가려움", "group": "기타", "icon": "🌸", "doctor": "입술·혀가 붓거나 숨이 차면 즉시 응급실",
                "options": [("cetirizine", 1, "가려움·두드러기를 줄입니다"), ("fexofenadine", 1, "졸음이 가장 적음"),
                            ("loratadine", 1, "하루 1번")]},
    "insomnia": {"ko": "가끔 잠이 안 옴", "group": "기타", "icon": "🌙", "doctor": "2주 넘는 불면은 원인 진료",
                 "options": [("doxylamine", 1, "수면을 유도하는 항히스타민 — 가끔만, 다음날 졸림 주의"),
                             ("diphenhydramine", 2, "수면 유도 — 고령자는 피함")]},
    "motion": {"ko": "멀미", "group": "기타", "icon": "🚌", "doctor": "",
               "options": [("dimenhydrinate", 1, "출발 30분~1시간 전에 복용"), ("meclizine", 2, "효과가 오래 감")]},
}
# combination cold medicines often contain several of the above at once: mention it with the cold symptoms
COLD_NOTE = "종합감기약은 여러 성분이 섞여 있어 다른 약과 같은 성분이 겹치기 쉽습니다 — 필요한 증상의 성분만 고르는 것이 안전합니다."
SEVERE_CLASSES = {"nsaid", "opioid", "sedative", "anticoagulant", "pde5"}
SEDATING = {"chlorpheniramine", "doxylamine", "diphenhydramine", "dimenhydrinate", "meclizine"}


def catalog() -> list[dict]:
    return [{"id": k, "ko": v["ko"], "group": v["group"], "icon": v["icon"]} for k, v in SYMPTOMS.items()]


NAME_KO = {"calcium carbonate": "탄산칼슘", "magnesium hydroxide": "수산화마그네슘", "magnesium oxide": "산화마그네슘",
           "lactulose": "락툴로오스", "ing:dexibuprofen": "덱시부프로펜", "ing:levodropropizine": "레보드로프로피진"}
_NOT_ORAL = re.compile(r"주사|주$|주\(|주\d|앰플|바이알|수액|프리필드")
_CHILD_FORM = re.compile(r"시럽|현탁|어린이|키즈|츄어블|과립|산\d|산$|산\(")


def _products(eng, key: str, limit: int = 3, child: bool = False) -> list[dict]:
    """Korean marketed products with this ingredient: single-ingredient first (else simple 2-3 ingredient products),
    no injections, OTC first; for children the syrups / chewables."""
    P = eng.products
    base = P[(P["country"] == "KR") & P["active"] & ~P["name"].str.contains(_NOT_ORAL)]
    rows = base[base["ingredients"].map(lambda v: list(v) == [key])]
    combo = False
    if not len(rows):  # e.g. dextromethorphan is sold only in combinations
        rows = base[base["ingredients"].map(lambda v: key in v and len(v) <= 3)]
        combo = True
    if not len(rows):
        return []
    rows = rows.assign(_n=rows["name"].str.len(), _c=rows["name"].str.contains(_CHILD_FORM) == child)
    rows = rows.sort_values(["otc", "_c", "_n"], ascending=[False, False, True])
    out, seen = [], set()
    for r in rows.itertuples():
        name = r.name.split("(")[0].strip()
        if name in seen:
            continue
        seen.add(name)
        out.append({"name": name, "full": r.name, "otc": bool(r.otc), "combo": combo})
        if len(out) >= limit:
            break
    return out


def recommend(eng, symptom_ids: list[str], current: list[dict], patient: Patient) -> dict:
    """current = [{"input", "ingredients": [Ingredient]}] already entered."""
    pa = assess(patient)
    U = {g.key: g for p in current for g in p["ingredients"]}
    owner = {g.key: p["input"] for p in current for g in p["ingredients"]}
    classes = {drug_class(k): k for k in U if drug_class(k)}
    age = patient.age
    pregnant = "pregnancy" in pa["diseases"]
    kid = (pa.get("kidney") or {}).get("clearance")
    out = []
    cache: dict[str, dict] = {}
    for sid in symptom_ids:
        s = SYMPTOMS.get(sid)
        if not s:
            continue
        opts = []
        for key, rank, does in s["options"]:
            if key not in cache:
                cache[key] = _screen(eng, key, U, owner, classes, pa, patient, age, pregnant, kid)
            c = cache[key]
            if c is None:
                continue
            opts.append({"key": key, "rank": rank, "does": does, **c})
        order = {"ok": 0, "caution": 1, "avoid": 2}
        opts.sort(key=lambda o: (order[o["status"]], o["rank"]))
        out.append({"id": sid, "ko": s["ko"], "icon": s["icon"], "doctor": s["doctor"], "options": opts})
    notes = []
    if any(SYMPTOMS.get(s, {}).get("group") == "감기" for s in symptom_ids):
        notes.append(COLD_NOTE)
    if age is not None and age < 12:
        notes.append("어린이는 나이·체중에 맞는 어린이용 제품과 용량을 써야 합니다 — 약사와 상의하세요.")
    if pregnant:
        notes.append("임신 중에는 일반약도 먼저 의사·약사와 상의하세요(해열·진통은 보통 아세트아미노펜).")
    return {"symptoms": out, "notes": notes,
            "disclaimer": "증상 완화를 위한 일반의약품 후보입니다. 진단·처방이 아니며, 복용 전 약사와 상의하세요."}


def _screen(eng, key, U, owner, classes, pa, patient, age, pregnant, kid) -> dict | None:
    if not (key in eng.name_idx or key in eng.ext_ing):
        return None
    g = eng._ext_ingredient(key)
    reasons, status = [], "ok"

    def worse(st: str, why: str):
        nonlocal status
        reasons.append({"sev": st, "text": why})
        if st == "avoid" or (st == "caution" and status == "ok"):
            status = st

    if key in U:
        worse("avoid", f"이미 넣은 '{owner[key]}'에 같은 성분이 있습니다 — 함께 먹으면 과다 복용")
    cid = drug_class(key)
    if cid and cid in classes and classes[cid] != key:
        sev = "avoid" if cid in SEVERE_CLASSES else "caution"
        worse(sev, f"'{owner[classes[cid]]}'와 같은 계열({DRUG_CLASSES[cid][0]})이라 효과·부작용이 겹칩니다")
    for c in U.values():
        if c.key == key:
            continue
        lvl, why = eng.pair_level(g, c)
        if lvl == "높음":
            worse("avoid", f"'{owner[c.key]}'와 함께 먹으면 위험: {why}")
        elif lvl == "주의":
            worse("caution", f"'{owner[c.key]}'와 상호작용: {why}")
    for d in eng._drug_disease([g], pa, patient):
        worse("avoid" if d["severity"] == "금기" else "caution", f"{d['disease']}: {d['reason']}".rstrip(" —"))
    groups = drug_groups(key)
    # self-care is stricter than a prescriber's 'caution': these are simply not good choices to pick up yourself
    if pregnant and "nsaid" in groups:
        worse("avoid", "임신 중에는 소염진통제를 스스로 고르지 않습니다(특히 20주 이후 금기) — 아세트아미노펜을 상의하세요")
    if age is not None and age >= 65 and key in SEDATING:
        worse("avoid", "65세 이상은 졸음·혼돈·낙상 위험이 커 피하는 약입니다(Beers 기준)")
    if kid is not None and kid < 30 and key in ("magnesium oxide", "magnesium hydroxide"):
        worse("avoid", "콩팥 기능이 크게 떨어져 마그네슘이 쌓일 수 있습니다")
    if age is not None and age < 12 and key in ("pseudoephedrine", "dextromethorphan", "doxylamine", "diphenhydramine",
                                                "chlorpheniramine", "loperamide", "naproxen"):
        worse("caution", "어린이는 사용 연령·용량 제한이 있습니다 — 어린이용 제품으로 약사와 상의")
    seen, uniq = set(), []
    for r in reasons:
        if r["text"] not in seen:
            seen.add(r["text"])
            uniq.append(r)
    return {"name": NAME_KO.get(key) or eng.ko(g).split("(")[0], "status": status, "reasons": uniq,
            "products": _products(eng, key, child=age is not None and age < 12)}
