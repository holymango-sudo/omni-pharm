"""What the AI 복약 상담 looks up by itself before answering.

A question often names a medicine or food that is not in the analysis ("박카스 먹어도 돼요?", "술 한 잔은요?"). Those
names are found in the question, resolved like the drug search does, and checked TOGETHER with the medicines of the
analysis by the same engine - so the answer rests on a real interaction check, not on the model's memory.
"""

from __future__ import annotations

import logging
import re

from omnipharm.clinical import Patient
from omnipharm.dosing import effective_limit, fmt_mg, limit_text
from omnipharm.engine import ResolveError
from omnipharm.knowledge import FOOD_ALIAS, mech_source
from omnipharm.models.llm import (
    HARM_REDUCTION_LINE,
    LIMIT_KEY,
    NUTRIENT_KEY,
    USAGE_KEY,
    USAGE_KEY_LIMITED,
    nutrient_text,
)
from omnipharm.pipeline.food_nutrients import norm as food_norm

# endings stripped from a word before it is looked up ('박카스랑' -> '박카스', '타이레놀은' -> '타이레놀')
_ENDING = re.compile(r"(이랑|랑|하고|이나|이요|에서|으로|까지|부터|처럼|이란|란|은|는|이|가|을|를|와|과|도|에|로|만|나|요|좀)$")
# general words that must not be pinned to one product ('비타민' is not 비타민B12, '감기약' is not one medicine)
_GENERIC = {"약", "약물", "알약", "영양제", "비타민", "감기약", "진통제", "해열제", "소화제", "수면제", "항생제", "한약", "보약", "음식",
            "같이", "함께", "먹어도", "마셔도", "되나요", "돼요", "괜찮", "괜찮아요", "언제", "몇", "시간", "하루", "지금", "오늘", "어제",
            "정도", "얼마나", "조금", "많이", "한잔", "한", "잔", "병", "알", "개", "번", "후", "전", "같은", "다른", "그럼", "그리고",
            "이거", "저거", "그거", "제가", "저는", "우리", "아이", "엄마", "아빠", "임신", "운전", "운동", "공복", "식후", "식전",
            # everyday 2-letter words that begin product names (2-letter brands such as 펜잘 are looked up)
            "감기", "두통", "기침", "콧물", "가래", "몸살", "생리", "소화", "변비", "설사", "치통", "근육", "허리", "무릎", "피부",
            "머리", "혈압", "혈당", "당뇨", "심장", "신장", "콩팥", "위장", "장염", "비염", "수유", "아기", "노인", "남편", "아내",
            "친구", "어린", "열이", "잠이", "속이", "배가", "눈이", "목이", "기운", "증상", "효과", "부작", "얼마", "무슨", "어떤"}
# a verb / adjective in the question ('먹으면', '마셔도', '괜찮을까요'): never a product, whatever names start with it
_VERB = re.compile(r"(으면|하면|는데|어요|아요|해요|나요|까요|어도|아도|니까|지만|으니|면서|습니다|세요|던데|는지|을까)$")
_NOT_MEDICINE = {"건강기능식품", "음식·영양성분"}
MAX_NEW = 2
_REPORT_STAT = re.compile(r"약 자체에 따르는 위험|\(AI 참고\)")  # FAERS associations shown as information only
log = logging.getLogger(__name__)


def _variants(word: str) -> list[str]:
    """'타이레놀이랑' -> ['타이레놀이랑', '타이레놀']: the word itself first, then with Korean endings removed."""
    out = [word]
    for _ in range(2):
        w = _ENDING.sub("", out[-1])
        if not w or w == out[-1]:
            break
        out.append(w)
    return out


def _lookup(eng, w: str, prefix: bool = True) -> str | None:
    """One word -> a medicine / food name the engine knows, or None. Only confident matches: the exact name,
    or a product whose name starts with a word of 3+ letters ('박카스' -> 박카스디액)."""
    if w.replace(" ", "").lower() in FOOD_ALIAS:  # 술, 소주, 자몽, 우유, 담배 ...
        return w
    if len(w) < 2 or w in _GENERIC or w.isdigit():
        return None
    hits = eng.search(w, 6)
    exact = next((h["name"] for h in hits if h["name"].replace(" ", "").lower() == w.lower()), None)
    if exact or not prefix:
        return exact
    # health foods / foods have marketing names that begin with everyday words ('먹으면티나', '하루에…'): their names
    # count only from 4 letters ('고려홍삼정'); medicines from 3, and a 2-letter brand ('펜잘' -> 펜잘큐정) only when the
    # product name is little more than the brand
    if any(h["name"].startswith(w) and len(h["name"]) <= len(w) + (12 if len(w) >= 3 else 6)
           and len(w) >= (4 if h.get("type") in _NOT_MEDICINE else 2) for h in hits):
        return w  # resolved later like the drug bar does ('게보린' -> 게보린정, not the first search hit)
    return None


def find_items(eng, question: str) -> list[str]:
    """Names of medicines / foods mentioned in the question (at most MAX_NEW)."""
    out: list[str] = []
    words = re.findall(r"[가-힣A-Za-z][가-힣A-Za-z0-9\-]*", question)
    used: set[int] = set()
    # a food / drink of several words ('스타벅스 카페 아메리카노'): the nutrient DB knows it as one name
    for n in (3, 2):
        for i in range(len(words) - n + 1):
            if used & set(range(i, i + n)):
                continue
            for last in _variants(words[i + n - 1]):
                name = " ".join([*words[i:i + n - 1], last])
                if food_norm(name) in getattr(eng, "food_nutr", {}):
                    out.append(name)
                    used |= set(range(i, i + n))
                    break
    for j, raw in enumerate(words):
        if len(out) >= MAX_NEW:
            break
        if j in used:
            continue
        variants = _variants(raw)
        if any(w in _GENERIC for w in variants) or _VERB.search(raw):  # '하루에' is '하루', not '하루에…' (a product)
            continue
        # an exact name in any form first ('오메가3는' -> 오메가3, not a product that begins with '오메가3는'), then a
        # product that begins with it
        name = next((n for w in variants if (n := _lookup(eng, w, prefix=False))), None) or \
            next((n for w in variants if (n := _lookup(eng, w))), None)
        if name and name not in out:
            out.append(name)
    return out[:MAX_NEW]


def lookups(eng, question: str, base: list[dict], patient: Patient, base_verdict: str | None = None) -> list[dict]:
    """Quick checks of every medicine / food the question names (never raises: the chat must still answer)."""
    out, also = [], []
    for name in find_items(eng, question):
        try:  # '박카스랑 타이레놀 같이?': the second item is checked together with the first one too
            c = quick_check(eng, base, name, patient, base_verdict, also)
        except Exception:
            log.exception("chat quick check failed")
            continue
        if c:
            also.append(c.pop("_product"))
            out.append(c)
    return out


def quick_check(eng, base: list[dict], name: str, patient: Patient, base_verdict: str | None = None,
                also: list[dict] | None = None) -> dict | None:
    """Analyse the analysis' medicines (+ items named earlier in the same question) + this one; -> compact facts for
    the chat (None: unknown or already analysed). Only findings that involve the new item are passed on - the rest
    is already in the analysis. '_product' is the resolved product (lookups() passes it on to the next item)."""
    also = also or []
    try:
        ings, info = eng.resolve_full(name)
    except ResolveError as e:  # several products of that name (박카스디액 / 박카스에프액 ...): the first candidate
        if not e.candidates:
            return None
        cands = [c["name"] for c in e.candidates]
        # the one the search box lists first (active, OTC, shortest name: 박카스 -> 박카스디액)
        name = next((h["name"] for h in eng.search(name, 15) if h["name"] in cands), cands[0])
        try:
            ings, info = eng.resolve_full(name)
        except ResolveError:
            return None
    name = info.get("product") or name
    keys = {g.key for g in ings}
    if not keys or keys <= {g.key for p in base for g in p["ingredients"]}:
        return None  # already in the analysis ('타이레놀' asked about while 타이레놀정500 is analysed)
    new = {"input": name, "ingredients": ings, "product": info.get("product"), "release": info.get("release"),
           "strength": info.get("strength"), "label_ko": info.get("label_ko"), "controlled": info.get("controlled")}
    r = eng.predict([*base, *also, new], patient)
    s = r["summary"]
    names = [eng.ko(g) for g in ings]

    def mine(text: str) -> bool:  # findings about this item: its ingredients, or the product itself (label age limit)
        return any(n in text for n in [*names, name])

    pairs = [f"{p['a']} + {p['b']}: {p['level']}" + (f" — {p['mechanism'][0]['text']}" if p.get("mechanism") else "")
             + (" (식약처 DUR 병용금기)" if p.get("dur") else "")
             for p in r["pairs"] if (p["a_key"] in keys or p["b_key"] in keys) and p["level"] != "낮음"][:5]
    together = " + ".join(p["input"] for p in also)
    if not base:
        verdict = (f"{s['verdict']} ({together} + {name} 함께 확인)" if also
                   else f"{s['verdict']} (이 약 하나만 확인 — 아직 분석한 약 조합 없음)")
    elif base_verdict and base_verdict != s["verdict"]:
        verdict = f"{s['verdict']} (추가 전 '{base_verdict}' → 추가 후 '{s['verdict']}')"
    else:
        verdict = f"{s['verdict']} (추가해도 판정 변화 없음)" if base_verdict else s["verdict"]
    if base and also:
        verdict += f" — 질문의 {together}도 함께 넣어 확인"
    shown = ", ".join(names[:5]) + (f" 외 {len(names) - 5}개" if len(names) > 5 else "")
    ctl = info.get("controlled")
    # how much is in one unit and the labelled daily maximum for this patient: '하루 몇 병?' gets a grounded answer
    # (never for a controlled substance: harm reduction names the dangerous combinations, not amounts)
    st = {} if ctl else info.get("strength") or {}
    content = [f"{eng.ko(g)} {fmt_mg(st['mg'][g.key])}" for g in ings if (st.get("mg") or {}).get(g.key)]
    # a brand typed without its strength ('타이레놀 10알?'): the marketed products of that name and their content,
    # so a count of tablets rests on real products, not on an invented '500 mg'
    brands = brand_contents(eng, name, ings) if not content and not ctl else []
    pa = r.get("patient") or {}
    lctx = {"crcl": (pa.get("kidney") or {}).get("clearance"), "age": patient.age,
            "sex": (patient.sex or "").upper()[:1] or None, "weight": patient.weight_kg,
            "hepatic": bool((pa.get("flags") or {}).get("hepatic"))}
    # a pregnancy alert or a contraindication for this person's disease: no maximum dose (it reads as permission) -
    # the answer is 'ask the doctor / pharmacist first'
    own_alert = any(d.get("drug") in names and (d.get("severity") == "금기" or d.get("key") == "pregnancy"
                                                 or re.search(r"임신|임부", str(d.get("disease"))))
                    for d in r.get("disease_alerts", []))
    limits = []
    for g in [] if ctl or own_alert else ings:
        lim = effective_limit(g.key, lctx, er=info.get("release") == "ER")
        if lim:
            # no content per unit (의약외품 박카스): the mg limit cannot be turned into bottles - the label usage answers
            limits.append(limit_text(eng.ko(g), lim) + " (다른 약·음료의 같은 성분과 합쳐서"
                          + (")" if content else
                             " — 개수는 '같은 이름 시판 제품의 1개당 함량'의 제품별로 계산)" if brands else
                             " — 1개당 함량 자료가 없어 개수로 바꿀 수 없음: 개수는 제품 설명서 용법)"))
    lab = info.get("label_ko") or {}
    src = _sources(r, keys, [*names, name]) | ({"허가 용량 기준"} if limits else set())
    if lab:
        src.add(mech_source("식약처표시") if lab.get("kind") == "건강기능식품" else mech_source("식약처허가"))
    if info.get("nutrients"):
        src.add("식약처 식품영양성분DB")
    if not src:  # checked, and no documented problem: that is the source of a '괜찮다' answer
        src.add("상호작용 검사(문서·규칙 근거 없음)")
    usage = (lab.get("usage") or "")[:300] or None
    # v2.18: each ingredient's key FDA label warning (boxed warning, else the start of the warnings), so the usual
    # explanations ('acetaminophen and the liver') rest on a document, not on the model's memory
    labels = getattr(eng, "labels", None)
    key_warn = []
    for g in [] if ctl or labels is None else ings[:3]:
        if g.key in labels.index:
            row = labels.loc[g.key]
            w = " ".join(str(row.get("boxed_warning") or row.get("warnings") or "").split())[:320]
            if w:
                key_warn.append(f"{eng.ko(g)}: {w}")
    if key_warn:
        src.add("FDA 허가 라벨")
    # this patient's daily limit (kidney / liver / age / self-medication) comes first: a label's general maximum
    # ('1일 최대 4그램(8정)') is higher than what an elderly patient on warfarin should take on their own
    return {"확인한 제품": f"{name} = {shown}", "함께 먹을 때 판정": verdict,
            "1개당 함량": f"{st.get('unit') or '1회 단위'}당 " + ", ".join(content) if content else None,
            "같은 이름 시판 제품의 1개당 함량(식약처)": brands or None,
            LIMIT_KEY: limits,
            # Korean label (박카스 '1일 1회 1병', 일반의약품 용법): answers '하루 몇 병?' when the mg limit cannot
            (USAGE_KEY_LIMITED if limits and content else USAGE_KEY): usage,
            "제품 설명서 주의사항(요약)": label_cautions(lab.get("cautions") or "") or None,
            "허가 문서 핵심 경고(FDA 원문)": key_warn or None,
            NUTRIENT_KEY: nutrient_text(info["nutrients"]) if info.get("nutrients") else None,
            "이 제품 관련 이유": [x["text"] for x in s["reasons"] if x["sev"] != "low" and mine(x["text"])][:5],
            # not the FAERS 'drug's own risk' associations: they are report statistics, not known side effects
            "이 제품 관련 참고": [x["text"] for x in s["reasons"] if x["sev"] == "low" and mine(x["text"])
                            and not _REPORT_STAT.search(x["text"])][:2],
            "할 일": s["advice"][:3], "상호작용": pairs,
            "규제 물질": (f"{ctl['ko']} — 한국: {ctl['law']}" + (f" / 해외: {ctl['abroad']}" if ctl.get("abroad") else "")
                       + f" / 위험 신호: {ctl['signs']} / 상담: {HARM_REDUCTION_LINE} / 용량·사용법은 안내하지 않음")
                      if ctl else None,
            "근거종류": sorted(src),
            "_product": new}


def brand_contents(eng, name: str, ings: list, limit: int = 3) -> list[str]:
    """'타이레놀' -> ['타이레놀정500밀리그람: 아세트아미노펜 500mg', ...]: active Korean products whose name starts with
    the typed word and their per-unit content of these ingredients (product_strengths)."""
    P = getattr(eng, "products", None)
    if P is None or not len(P) or len(name) < 2:
        return []
    keys = {g.key for g in ings}
    rows = P[(P["country"] == "KR") & P["active"] & P["name"].str.startswith(name)]
    out = []
    for r in rows.sort_values("name", key=lambda s: s.str.len()).head(12).itertuples():
        st = getattr(eng, "strengths", {}).get(("KR", str(r.item_seq))) or {}
        mg = {k: v for k, v in (st.get("mg") or {}).items() if k in keys}
        if mg:
            out.append(f"{r.name}: " + ", ".join(f"{eng.ko(next(g for g in ings if g.key == k))} {fmt_mg(v)}"
                                                 for k, v in mg.items()) + f" (1{st.get('unit') or '개'}당)")
        if len(out) >= limit:
            break
    return out


def conversation_names(eng, base: list[dict], found: list[dict]) -> list[str]:
    """Medicines / foods of this conversation: what the person entered, their ingredients, what the question checked
    (chat_checks.unsupported_claims looks for clinical claims about these)."""
    out = set()
    for p in base:
        out |= {p["input"], *(eng.ko(g).split("(")[0] for g in p["ingredients"])}
    for f in found:
        prod, _, ings = f["확인한 제품"].partition(" = ")
        out |= {prod.split(" · ")[0].split(" (")[0].strip(), *(x.split("(")[0].strip() for x in ings.split(", "))}
    return [n for n in out if len(n) >= 2]


def label_cautions(text: str, limit: int = 420) -> str:
    """The first articles of a Korean label's cautions (who must not take it, who should ask first), shortened."""
    out, n = [], 0
    for ln in text.splitlines():
        if n + len(ln) > limit:
            break
        out.append(ln)
        n += len(ln)
    return "\n".join(out)


def _sources(r: dict, keys: set[str], names: list[str]) -> set[str]:
    """Kinds of evidence behind the findings that involve the new item (the answer may cite only these)."""
    src = set()
    for p in r["pairs"]:
        if p["level"] == "낮음" or not (p["a_key"] in keys or p["b_key"] in keys):
            continue
        if p.get("dur"):
            src.add("식약처 DUR")
        if any(e.get("context") != "co_therapy" for e in p.get("label_evidence", [])):
            src.add("FDA 허가 라벨")
        for m in p.get("mechanism", []):
            src.add(mech_source(m["kind"]))
    for d in r.get("disease_alerts", []):
        if d.get("drug") in names:
            src.add("식약처 허가사항(제품 설명서)" if d.get("key") == "age_label"
                    else "질환별 금기 기준(Beers·허가사항·DUR)")
    return src
