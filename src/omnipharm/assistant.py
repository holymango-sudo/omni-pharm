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
from omnipharm.knowledge import FOOD_ALIAS
from omnipharm.models.llm import HARM_REDUCTION_LINE

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
    if len(w) >= 3 and any(h["name"].startswith(w) and len(h["name"]) <= len(w) + 12 for h in hits):
        return w  # resolved later like the drug bar does ('게보린' -> 게보린정, not the first search hit)
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
           "strength": info.get("strength"), "label_ko": info.get("label_ko")}
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
    pa = r.get("patient") or {}
    lctx = {"crcl": (pa.get("kidney") or {}).get("clearance"), "age": patient.age,
            "sex": (patient.sex or "").upper()[:1] or None, "weight": patient.weight_kg,
            "hepatic": bool((pa.get("flags") or {}).get("hepatic"))}
    limits = []
    for g in [] if ctl else ings:
        lim = effective_limit(g.key, lctx, er=info.get("release") == "ER")
        if lim:
            limits.append(limit_text(eng.ko(g), lim) + " (다른 약·음료의 같은 성분과 합쳐서)")
    lab = info.get("label_ko") or {}
    src = _sources(r, keys, [*names, name]) | ({"허가 용량 기준"} if limits else set())
    if lab:
        src.add("식약처 허가사항(제품 설명서)")
    if not src:  # checked, and no documented problem: that is the source of a '괜찮다' answer
        src.add("상호작용 검사(문서·규칙 근거 없음)")
    return {"확인한 제품": f"{name} = {shown}", "함께 먹을 때 판정": verdict,
            "1개당 함량": f"{st.get('unit') or '1회 단위'}당 " + ", ".join(content) if content else None,
            # Korean label of quasi-drugs (박카스 ...): the official '1일 1회 1병' answers '하루 몇 병?'
            "제품 설명서 용법(식약처)": (lab.get("usage") or "")[:300] or None,
            "제품 설명서 주의사항(요약)": label_cautions(lab.get("cautions") or "") or None,
            "1일최대량(허가기준·이 환자)": limits,
            "이 제품 관련 이유": [x["text"] for x in s["reasons"] if x["sev"] != "low" and mine(x["text"])][:5],
            "이 제품 관련 참고": [x["text"] for x in s["reasons"] if x["sev"] == "low" and mine(x["text"])][:2],
            "할 일": s["advice"][:3], "상호작용": pairs,
            "규제 물질": (f"{ctl['ko']} — 한국: {ctl['law']}" + (f" / 해외: {ctl['abroad']}" if ctl.get("abroad") else "")
                       + f" / 위험 신호: {ctl['signs']} / 상담: {HARM_REDUCTION_LINE} / 용량·사용법은 안내하지 않음")
                      if ctl else None,
            "근거종류": sorted(src),
            "_product": new}


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
            src.add("임상 규칙" if m["kind"] == "규칙" else "FDA CYP 지표표" if m["kind"] == "CYP-FDA" else "약리 기전(ChEMBL)")
    for d in r.get("disease_alerts", []):
        if d.get("drug") in names:
            src.add("식약처 허가사항(제품 설명서)" if d.get("key") == "age_label"
                    else "질환별 금기 기준(Beers·허가사항·DUR)")
    return src
