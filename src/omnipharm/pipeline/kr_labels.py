"""Korean product labels (식약처 의약품 허가사항) of every Korean medicine: 효능·효과, 용법·용량, 사용상의 주의사항.

Source: data.go.kr DrugPrdtPrmsnInfoService08 / getDrugPrdtPrmsnDtlInq08 (the same detail endpoint the strengths stage
reads MATERIAL_NAME from). Raw pages are cached gzip-compressed under data/raw/mfds/detail/ (resumable, one file per
page of 100 products; about 430 pages).

Output: processed/kr_labels.parquet
  item_seq, kind (전문의약품/일반의약품), efficacy, usage, cautions, min_age (일반의약품 only: '15세 미만은 복용하지 말 것'),
  avoid_with (일반의약품: the drugs listed under '이 약을 복용하는 동안 다음의 약을 복용하지 말 것', as written)
"""

from __future__ import annotations

import gzip
import json
import logging
import re
import time

import httpx
import pandas as pd

from omnipharm.config import settings
from omnipharm.pipeline.common import PROC, RAW
from omnipharm.pipeline.dur import doc_text, min_age

log = logging.getLogger("pipeline.kr_labels")
URL = "https://apis.data.go.kr/1471000/DrugPrdtPrmsnInfoService08/getDrugPrdtPrmsnDtlInq08"
CACHE = RAW / "mfds" / "detail"
KEEP = ("ITEM_SEQ", "ITEM_NAME", "ETC_OTC_CODE", "EE_DOC_DATA", "UD_DOC_DATA", "NB_DOC_DATA", "STORAGE_METHOD",
        "VALID_TERM", "ATC_CODE", "CANCEL_NAME")


def _page(c: httpx.Client, key: str, page: int) -> dict:
    f = CACHE / f"page_{page:04d}.json.gz"
    if f.exists():
        return json.loads(gzip.decompress(f.read_bytes()))
    for attempt in range(6):
        try:
            r = c.get(URL, params={"serviceKey": key, "type": "json", "numOfRows": 100, "pageNo": page})
            r.raise_for_status()
            body = r.json()["body"]
            break
        except Exception as e:  # network / quota hiccup (the message never contains the key)
            if attempt == 5:
                raise RuntimeError(f"MFDS detail page {page}: {type(e).__name__}") from None
            time.sleep(5 * (attempt + 1))
    items = body.get("items") or []
    out = {"total": int(body["totalCount"]), "items": [{k: it.get(k) for k in KEEP} for it in items]}
    CACHE.mkdir(parents=True, exist_ok=True)
    f.write_bytes(gzip.compress(json.dumps(out, ensure_ascii=False).encode("utf-8")))
    return out


def download() -> list[dict]:
    key = settings.data_go_kr_api_key
    if not key:
        raise RuntimeError("OMNIPHARM_DATA_GO_KR_API_KEY is not set (.env)")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # request URLs carry the key
    rows: list[dict] = []
    with httpx.Client(timeout=180) as c:
        first = _page(c, key, 1)
        pages = (first["total"] + 99) // 100
        rows += first["items"]
        for p in range(2, pages + 1):
            rows += _page(c, key, p)["items"]
            if p % 25 == 0:
                log.info(f"MFDS labels: page {p}/{pages} ({len(rows):,} products)")
    log.info(f"MFDS labels: {len(rows):,} products")
    return rows


_AVOID_HEAD = re.compile(r"(복용|사용|투여)하는\s*동안\s*다음의?\s*약(물)?을?\s*(복용|사용|투여)하지\s*말")


_NEXT_ARTICLE = re.compile(r"^\s*(\d+\.|[가-하]\.|\d+\)\s*다음|다음과|상의할|[①-⑳])|(말|할)\s*것\.?\s*$")


def avoid_with(cautions: str) -> list[str]:
    """The items under '이 약을 복용하는 동안 다음의 약을 복용하지 말 것' (as written on the label), e.g.
    ['다른 감기약', '해열진통제', '진정제', '레보도파']. Stops at the next article ('3.다음과 같은 사람은 …')."""
    out, inside = [], False
    for ln in cautions.splitlines():
        if _AVOID_HEAD.search(ln):
            inside = True
            rest = ln[_AVOID_HEAD.search(ln).end():]
            rest = rest.split("것", 1)[1] if "것" in rest[:4] else rest
            out += _split_items(rest)
            continue
        if inside:
            if _NEXT_ARTICLE.search(ln):
                break
            out += _split_items(ln)
    return list(dict.fromkeys(out))[:20]


def _split_items(text: str) -> list[str]:
    """'다른 감기약, 해열진통제, 항히스타민제를 함유하는 내복약(비염용 경구제, 멀미약)' -> items; commas inside brackets
    belong to the bracket ('(비염용 경구제, 멀미약)' lists examples of the item before it)."""
    items, depth, cur = [], 0, ""
    for ch in text:
        depth += (ch in "([") - (ch in ")]")
        if ch in ",，·" and depth <= 0:
            items.append(cur)
            cur = ""
        else:
            cur += ch
    items.append(cur)
    out = []
    for x in items:
        x = re.sub(r"^\s*(\(?\d+\)|\d+\.)\s*", "", x)
        x = " ".join(x.split()).strip(" .")
        if 1 < len(x) <= 60:
            out.append(x)
    return out


def build() -> pd.DataFrame:
    recs = []
    for r in download():
        kind = (r.get("ETC_OTC_CODE") or "").strip()
        usage, cautions = doc_text(r.get("UD_DOC_DATA")), doc_text(r.get("NB_DOC_DATA"))
        otc = "일반" in kind
        recs.append({"item_seq": str(r.get("ITEM_SEQ")), "kind": kind, "efficacy": doc_text(r.get("EE_DOC_DATA")),
                     "usage": usage, "cautions": cautions,
                     # prescription labels mention ages in dosing tables and trial descriptions: OTC only
                     "min_age": min_age(usage, cautions) if otc else None,
                     "avoid_with": avoid_with(cautions) if otc else [],
                     "storage": r.get("STORAGE_METHOD") or "", "valid_term": r.get("VALID_TERM") or ""})
    df = pd.DataFrame(recs).drop_duplicates("item_seq")
    df["min_age"] = df["min_age"].astype("Int64")
    df.to_parquet(PROC / "kr_labels.parquet")
    otc = df["kind"].str.contains("일반")
    log.info(f"kr_labels: {len(df):,} products ({int(otc.sum()):,} 일반의약품) | usage text {int((df['usage'] != '').sum()):,} | "
             f"OTC age limit {int(df['min_age'].notna().sum()):,} | OTC 'do not take with' lists "
             f"{int(df['avoid_with'].map(len).gt(0).sum()):,}")
    return df
