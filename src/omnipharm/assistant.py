"""What the AI 복약 상담 looks up by itself before answering.

A question often names a medicine or food that is not in the analysis ("박카스 먹어도 돼요?", "술 한 잔은요?"). Those
names are found in the question, resolved like the drug search does, and checked TOGETHER with the medicines of the
analysis by the same engine - so the answer rests on a real interaction check, not on the model's memory.
"""

from __future__ import annotations

import logging
import re

from omnipharm.clinical import Patient
from omnipharm.dosing import effective_limit, fmt_mg
from omnipharm.engine import ResolveError
from omnipharm.knowledge import FOOD_ALIAS

# endings stripped from a word before it is looked up ('박카스랑' -> '박카스', '타이레놀은' -> '타이레놀')
_ENDING = re.compile(r"(이랑|랑|하고|이나|이요|에서|으로|까지|부터|처럼|이란|란|은|는|이|가|을|를|와|과|도|에|로|만|나|요|좀)$")
# general words that must not be pinned to one product ('비타민' is not 비타민B12, '감기약' is not one medicine)
_GENERIC = {"약", "약물", "알약", "영양제", "비타민", "감기약", "진통제", "해열제", "소화제", "수면제", "항생제", "한약", "보약", "음식",
            "같이", "함께", "먹어도", "마셔도", "되나요", "돼요", "괜찮", "괜찮아요", "언제", "몇", "시간", "하루", "지금", "오늘", "어제",
            "정도", "얼마나", "조금", "많이", "한잔", "한", "잔", "병", "알", "개", "번", "후", "전", "같은", "다른", "그럼", "그리고",
            "이거", "저거", "그거", "제가", "저는", "우리", "아이", "엄마", "아빠", "임신", "운전", "운동", "공복", "식후", "식전"}
MAX_NEW = 2
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


def _lookup(eng, w: str) -> str | None:
    """One word -> a medicine / food name the engine knows, or None. Only confident matches: the exact name,
    or a product whose name starts with a word of 3+ letters ('박카스' -> 박카스디액)."""
    if w.replace(" ", "").lower() in FOOD_ALIAS:  # 술, 소주, 자몽, 우유, 담배 ...
        return w
    if len(w) < 2 or w in _GENERIC or w.isdigit():
        return None
    hits = eng.search(w, 6)
    exact = next((h["name"] for h in hits if h["name"].replace(" ", "").lower() == w.lower()), None)
    if exact:
        return exact
    if len(w) >= 3:
        return next((h["name"] for h in hits if h["name"].startswith(w) and len(h["name"]) <= len(w) + 12), None)
    return None


def find_items(eng, question: str) -> list[str]:
    """Names of medicines / foods mentioned in the question (at most MAX_NEW)."""
    out: list[str] = []
    for raw in re.findall(r"[가-힣A-Za-z][가-힣A-Za-z0-9\-]*", question):
        for w in _variants(raw):
            if w in _GENERIC:
                break
            name = _lookup(eng, w)
            if name:
                if name not in out:
                    out.append(name)
                break
        if len(out) >= MAX_NEW:
            break
    return out


def lookups(eng, question: str, base: list[dict], patient: Patient, base_verdict: str | None = None) -> list[dict]:
    """Quick checks of every medicine / food the question names (never raises: the chat must still answer)."""
    out = []
    for name in find_items(eng, question):
        try:
            c = quick_check(eng, base, name, patient, base_verdict)
        except Exception:
            log.exception("chat quick check failed")
            continue
        if c:
            out.append(c)
    return out


def quick_check(eng, base: list[dict], name: str, patient: Patient, base_verdict: str | None = None) -> dict | None:
    """Analyse the analysis' medicines + this one; -> compact facts for the chat (None: unknown or already analysed).
    Only the findings that involve the new item are passed on - the rest is already in the analysis."""
    try:
        ings, info = eng.resolve_full(name)
    except ResolveError as e:  # several products of that name (박카스디액 / 박카스에프액 ...): the first candidate
        if not e.candidates:
            return None
        name = e.candidates[0]["name"]
        try:
            ings, info = eng.resolve_full(name)
        except ResolveError:
            return None
    keys = {g.key for g in ings}
    if not keys or keys <= {g.key for p in base for g in p["ingredients"]}:
        return None  # already in the analysis ('타이레놀' asked about while 타이레놀정500 is analysed)
    products = [*base, {"input": name, "ingredients": ings, "product": info.get("product"),
                        "release": info.get("release"), "strength": info.get("strength")}]
    r = eng.predict(products, patient)
    s = r["summary"]
    names = [eng.ko(g) for g in ings]

    def mine(text: str) -> bool:
        return any(n in text for n in names)

    pairs = [f"{p['a']} + {p['b']}: {p['level']}" + (f" — {p['mechanism'][0]['text']}" if p.get("mechanism") else "")
             + (" (식약처 DUR 병용금기)" if p.get("dur") else "")
             for p in r["pairs"] if (p["a_key"] in keys or p["b_key"] in keys) and p["level"] != "낮음"][:5]
    if not base:
        verdict = f"{s['verdict']} (이 약 하나만 확인 — 아직 분석한 약 조합 없음)"
    elif base_verdict and base_verdict != s["verdict"]:
        verdict = f"{s['verdict']} (추가 전 '{base_verdict}' → 추가 후 '{s['verdict']}')"
    else:
        verdict = f"{s['verdict']} (추가해도 판정 변화 없음)" if base_verdict else s["verdict"]
    shown = ", ".join(names[:5]) + (f" 외 {len(names) - 5}개" if len(names) > 5 else "")
    # how much is in one unit and the labelled daily maximum for this patient: '하루 몇 병?' gets a grounded answer
    st = info.get("strength") or {}
    content = [f"{eng.ko(g)} {fmt_mg(st['mg'][g.key])}" for g in ings if (st.get("mg") or {}).get(g.key)]
    pa = r.get("patient") or {}
    lctx = {"crcl": (pa.get("kidney") or {}).get("clearance"), "age": patient.age,
            "sex": (patient.sex or "").upper()[:1] or None, "weight": patient.weight_kg,
            "hepatic": bool((pa.get("flags") or {}).get("hepatic"))}
    limits = []
    for g in ings:
        lim = effective_limit(g.key, lctx, er=info.get("release") == "ER")
        if lim:
            mx, warn, single, why = lim
            limits.append(f"{eng.ko(g)}: 1일 최대 {fmt_mg(mx)}" + (f"(일반약 자가복용 기준 {fmt_mg(warn)})" if warn else "")
                          + (f", 1회 최대 {fmt_mg(single)}" if single else "") + (f" — {', '.join(why)} 반영" if why else "")
                          + " (다른 약·음료의 같은 성분과 합쳐서)")
    return {"확인한 제품": f"{name} = {shown}", "함께 먹을 때 판정": verdict,
            "1개당 함량": f"{st.get('unit') or '1회 단위'}당 " + ", ".join(content) if content else None,
            "1일최대량(허가기준·이 환자)": limits,
            "이 제품 관련 이유": [x["text"] for x in s["reasons"] if x["sev"] != "low" and mine(x["text"])][:5],
            "이 제품 관련 참고": [x["text"] for x in s["reasons"] if x["sev"] == "low" and mine(x["text"])][:2],
            "할 일": s["advice"][:3], "상호작용": pairs,
            "근거종류": sorted(_sources(r, keys, names) | ({"허가 용량 기준"} if limits else set()))}


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
            src.add("임상 규칙" if m["kind"] == "규칙" else "FDA CYP 지표표" if m["kind"] == "CYP-FDA" else "약리 기전(ChEMBL)")
    if any(d.get("drug") in names for d in r.get("disease_alerts", [])):
        src.add("질환별 금기 기준(Beers·허가사항·DUR)")
    return src
