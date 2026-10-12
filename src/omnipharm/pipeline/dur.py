"""MFDS DUR (의약품안전사용서비스) ingredient rules + MFDS quasi-drug (의약외품) products.

DUR is the official Korean prescribing / dispensing safety list that every Korean pharmacy and hospital system
checks. Ingredient-level operations of DURIrdntInfoService03 (data.go.kr 15056780):
  병용금기 (contraindicated combinations), 특정연령대금기 (age), 임부금기 (pregnancy, grade 1/2), 용량주의
  (maximum daily dose), 투여기간주의 (maximum duration), 노인주의 (elderly), 효능군중복 (same-efficacy group).
Quasi-drugs: QdrgPrdtPrmsnInfoService03 (data.go.kr 15095679) - toothpaste, sanitizers, drinks such as 박카스.

Outputs (data/processed):
  dur.json                 {"pairs", "age", "pregnancy", "dose", "duration", "elderly", "efficacy", "meta"}
  kr_quasi_products.parquet   same columns as kr_products.parquet, kind "의약외품"
Raw API pages are cached in data/raw/mfds_dur/ (re-run = no network).
"""

from __future__ import annotations

import html
import json
import logging
import re
import time

import httpx
import pandas as pd

from omnipharm.config import settings
from omnipharm.knowledge import KR_INGREDIENTS, lab_name
from omnipharm.pipeline.common import PROC, RAW
from omnipharm.pipeline.ingredients import Mapper

log = logging.getLogger("pipeline.dur")
BASE = "https://apis.data.go.kr/1471000"
CACHE = RAW / "mfds_dur"
OPS = {
    "pairs": "getUsjntTabooInfoList02", "age": "getSpcifyAgrdeTabooInfoList02", "pregnancy": "getPwnmTabooInfoList02",
    "dose": "getCpctyAtentInfoList02", "duration": "getMdctnPdAtentInfoList02", "elderly": "getOdsnAtentInfoList02",
    "efficacy": "getEfcyDplctInfoList02",
}
ORAL = re.compile(r"정|캡슐|시럽|산제|과립|액|현탁|츄어블|트로키|환|경구")


def fetch_all(svc: str, op: str, rows: int = 100) -> list[dict]:
    """Every row of a data.go.kr MFDS operation (cached as one JSON file per operation)."""
    f = CACHE / f"{op}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    key = settings.data_go_kr_api_key
    if not key:
        raise RuntimeError("OMNIPHARM_DATA_GO_KR_API_KEY is not set (.env)")
    out: list[dict] = []
    page, total = 1, None
    with httpx.Client(timeout=60) as c:
        while total is None or len(out) < total:
            for attempt in range(5):
                try:
                    r = c.get(f"{BASE}/{svc}/{op}", params={"serviceKey": key, "type": "json", "numOfRows": rows,
                                                            "pageNo": page})
                    r.raise_for_status()
                    body = r.json()["body"]
                    break
                except Exception as e:
                    if attempt == 4:
                        raise RuntimeError(f"{op} page {page}: {type(e).__name__}") from None
                    time.sleep(2 + attempt * 3)
            total = int(body.get("totalCount") or 0)
            items = body.get("items") or []
            if isinstance(items, dict):
                items = items.get("item") or []
            items = [i.get("item", i) if isinstance(i, dict) else i for i in items]
            if not items:
                break
            out += items
            page += 1
    CACHE.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    log.info(f"{op}: {len(out):,} rows")
    return out


def _mg(text: str | None) -> float | None:
    """'리다미딘염산염 20밀리그램' / '2,000밀리그램' / '4그램' / '500마이크로그램' -> mg."""
    m = re.search(r"([\d,]+(?:\.\d+)?)\s*(밀리그램|마이크로그램|그램|mg|mcg|㎍|g)", (text or "").replace(" ", ""))
    if not m:
        return None
    v = float(m.group(1).replace(",", ""))
    return v * {"그램": 1000, "g": 1000, "마이크로그램": 0.001, "㎍": 0.001, "mcg": 0.001}.get(m.group(2), 1.0)


def _days(text: str | None) -> int | None:
    m = re.search(r"(\d+)\s*일", text or "")
    return int(m.group(1)) if m else None


def build_dur() -> dict:
    raw = {k: fetch_all("DURIrdntInfoService03", op) for k, op in OPS.items()}
    mapper = Mapper()
    names = {r.get(f) for rows in raw.values() for r in rows for f in ("INGR_ENG_NAME", "MIXTURE_INGR_ENG_NAME")}
    mapping = mapper.map_all(n for n in names if n)

    def key(eng: str | None) -> str | None:
        if not eng:
            return None
        k = mapping.get(eng)
        if k:
            return k
        n = lab_name(eng)
        return f"lab:{n}" if n else None

    def ok(r: dict) -> bool:
        return r.get("DEL_YN", "정상") == "정상"

    pairs = {}
    for r in filter(ok, raw["pairs"]):
        a, b = key(r.get("INGR_ENG_NAME")), key(r.get("MIXTURE_INGR_ENG_NAME"))
        if a and b and a != b:
            pairs[tuple(sorted((a, b)))] = {"a": a, "b": b, "a_ko": r.get("INGR_KOR_NAME"),
                                            "b_ko": r.get("MIXTURE_INGR_KOR_NAME"),
                                            "content": (r.get("PROHBT_CONTENT") or "").strip(),
                                            "date": r.get("NOTIFICATION_DATE")}
    age = [{"key": key(r["INGR_ENG_NAME"]), "ko": r.get("INGR_NAME"), "age_base": r.get("AGE_BASE"),
            "form": r.get("FORM_NAME"), "content": (r.get("PROHBT_CONTENT") or "").strip()}
           for r in filter(ok, raw["age"]) if key(r.get("INGR_ENG_NAME"))]
    preg = [{"key": key(r["INGR_ENG_NAME"]), "ko": r.get("INGR_NAME"), "grade": r.get("GRADE"),
             "content": (r.get("PROHBT_CONTENT") or "").strip(), "route": r.get("REMARK"), "form": r.get("FORM_NAME")}
            for r in filter(ok, raw["pregnancy"]) if key(r.get("INGR_ENG_NAME"))]
    dose: dict[str, dict] = {}
    for r in filter(ok, raw["dose"]):
        k, mg = key(r.get("INGR_ENG_NAME")), _mg(r.get("MAX_QTY"))
        if not k or mg is None or r.get("MIX_TYPE") != "단일" or not ORAL.search(r.get("FORM_NAME") or ""):
            continue  # oral single-ingredient maximum daily dose only
        if k not in dose or mg > dose[k]["max_mg"]:  # several oral forms: the most permissive applies to all
            dose[k] = {"key": k, "ko": r.get("INGR_NAME"), "max_mg": mg, "raw": r.get("MAX_QTY"),
                       "form": r.get("FORM_NAME")}
    duration: dict[str, dict] = {}
    for r in filter(ok, raw["duration"]):
        k, d = key(r.get("INGR_ENG_NAME")), _days(r.get("MAX_DOSAGE_TERM"))
        if k and d and (k not in duration or d > duration[k]["days"]):
            duration[k] = {"key": k, "ko": r.get("INGR_NAME"), "days": d, "form": r.get("FORM_NAME")}
    elderly = [{"key": key(r["INGR_ENG_NAME"]), "ko": r.get("INGR_NAME"),
                "content": " ".join((r.get("PROHBT_CONTENT") or "").split())}
               for r in filter(ok, raw["elderly"]) if key(r.get("INGR_ENG_NAME"))]
    eff = [{"key": key(r["INGR_ENG_NAME"]), "ko": r.get("INGR_NAME"), "group": r.get("EFFECT_CODE"),
            "series": r.get("SERS_NAME")}
           for r in filter(ok, raw["efficacy"]) if key(r.get("INGR_ENG_NAME"))]
    out = {"pairs": list(pairs.values()), "age": age, "pregnancy": preg, "dose": list(dose.values()),
           "duration": list(duration.values()), "elderly": elderly, "efficacy": eff,
           "meta": {"source": "식품의약품안전처 의약품안전사용서비스(DUR) 성분정보", "built": time.strftime("%Y-%m-%d"),
                    "counts": {k: len(v) for k, v in raw.items()}}}
    (PROC / "dur.json").write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
    mapped = sum(not p["a"].startswith("lab:") and not p["b"].startswith("lab:") for p in out["pairs"])
    log.info("DUR: " + ", ".join(f"{k} {len(v):,}" for k, v in out.items() if k != "meta")
             + f" | combination pairs with both ingredients structure-mapped: {mapped:,}")
    return out


def doc_text(xml: str | None) -> str:
    """MFDS XML document (EE/UD/NB_DOC_DATA) -> plain text, one line per article title / paragraph."""
    if not xml:
        return ""
    s = re.sub(r"<ARTICLE title=\"([^\"]*)\"", lambda m: "\n" + m.group(1) + "\n<", xml)
    s = re.sub(r"<!\[CDATA\[(.*?)\]\]>", lambda m: m.group(1) + "\n", s, flags=re.S)
    s = html.unescape(html.unescape(re.sub(r"<[^>]*>", " ", s))).replace("\xa0", " ")
    return "\n".join(ln for ln in (" ".join(x.split()) for x in s.splitlines()) if ln)


def min_age(usage: str, cautions: str) -> int | None:
    """'15세 미만은 복용하지 않는다' -> 15 (the product is labelled not for younger people)."""
    age = re.compile(r"(\d{1,2})\s*세\s*(미만|이하)")
    ban = re.compile(r"(복용|사용|투여|먹)(하)?지\s*(않|말|마)|금지|금한다")
    # not bans: '6세 이하는 보호자 지도 하에', '2세 이상 ~ 12세 미만: …', '소아에게 사용할 때는 어른 손에 …', '지시 없이는'
    soft = re.compile(r"주의|감독|지도|보호자|삼키|독성|경우|이상|할 때|때는|어른|직접|지시")
    ages, ban_list = [], False
    for ln in (usage + "\n" + cautions).splitlines():
        if "다음" in ln and re.search(r"것\.?\s*$", ln) and not age.search(ln):  # article title
            ban_list = bool(re.search(r"(복용|사용|투여)하지\s*말", ln))  # '…은 이 제품을 복용하지 말 것'
            continue
        if not age.search(ln) and (len(ln) > 40 or re.search(r"사항|이상반응|경고|보관", ln)):
            ban_list = False  # the '…하지 말 것' list has ended
            continue
        for sent in re.split(r"(?<=[.。])\s+", ln):  # judge each sentence ('15세 이상 성인: 1병. 15세 미만은 …')
            for m in age.finditer(sent):
                if soft.search(sent):
                    continue
                # '15세 미만은 복용하지 않는다' / a short item under '다음 사람은 복용하지 말 것' ('2) 15세 미만')
                if ban.search(sent[m.end():]) or (ban_list and len(ln) <= 40):
                    ages.append(int(m[1]) + (m[2] == "이하"))
    return max(ages) if ages else None


def build_quasi() -> pd.DataFrame:
    rows = fetch_all("QdrgPrdtPrmsnInfoService03", "getQdrgPrdtPrmsnInfoInq03")
    kr = PROC / "kr_ingredient_names.json"
    ko2key = json.loads(kr.read_text(encoding="utf-8")) if kr.exists() else {}
    from omnipharm.pipeline.strengths import KO_SALT

    def ko_key(s: str) -> str | None:
        s = re.sub(r"\(.*?\)", "", s).replace(" ", "")
        for cand in (s, KO_SALT.sub("", s)):
            if cand in ko2key:
                return ko2key[cand]
            if cand in KR_INGREDIENTS:
                return KR_INGREDIENTS[cand]
        return None

    recs = []
    for r in rows:
        name = re.sub(r"^\s*1\.\s*", "", (r.get("ITEM_NAME") or "").split(", 2.")[0]).strip()
        parts = [p.strip() for p in re.split(r",(?![^()]*\))", r.get("MAIN_INGR") or "") if p.strip()]
        ings, un = [], []
        for p in parts:
            k = ko_key(p)
            (ings if k else un).append(k or p)
        # mapped English names from KR_INGREDIENTS are plain names: resolve them like other English names
        usage, cautions = doc_text(r.get("UD_DOC_DATA")), doc_text(r.get("NB_DOC_DATA"))
        recs.append({"item_seq": str(r.get("ITEM_SEQ")), "name": name, "base": re.sub(r"\(.*?\)|\s", "", name),
                     "eng_name": "", "company": r.get("ENTP_NAME") or "", "kind": "의약외품",
                     "status": "정상" if (r.get("CANCEL_CODE_NAME") or "정상") == "정상" else "취소",
                     "category": r.get("CLASS_NO_NAME") or "", "ingredients_raw": r.get("MAIN_INGR") or "",
                     "ingredients": list(dict.fromkeys(ings)), "unresolved": un,
                     # the Korean label: 용법용량 / 효능 / 사용상의 주의사항 (the per-bottle amount is not in this API)
                     "usage": usage, "efficacy": doc_text(r.get("EE_DOC_DATA")), "cautions": cautions,
                     "min_age": min_age(usage, cautions)})
    df = pd.DataFrame(recs)
    df["min_age"] = df["min_age"].astype("Int64")
    mapper = Mapper()
    plain = {i for v in df["ingredients"] for i in v if ":" not in i}
    pm = mapper.map_all(plain) if plain else {}
    df["ingredients"] = [list(dict.fromkeys(i if ":" in i else (pm.get(i) or f"lab:{lab_name(i) or i}") for i in v))
                         for v in df["ingredients"]]
    df.to_parquet(PROC / "kr_quasi_products.parquet")
    log.info(f"quasi-drugs: {len(df):,} products, {int((df['status'] == '정상').sum()):,} active, "
             f"with an analysable ingredient {int((df['ingredients'].map(len) > 0).mean() * 100)}%")
    return df


def build() -> None:
    build_dur()
    build_quasi()
