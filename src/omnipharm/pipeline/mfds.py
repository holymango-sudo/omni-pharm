"""Korean marketed products (식약처 의약품 제품 허가정보, data.go.kr) -> ingredient lists.

Downloads the full permit list once (~43k products, both OTC 일반의약품 and Rx 전문의약품) so product
names like 콜대원코프에스시럽 or 알러샷연질캡슐 resolve offline. Each product's main ingredients
(English, '/'-separated) are mapped to:
  * a drug in drug_master (salt stripping, synonyms, or identical structure via PubChem InChIKey), or
  * a Korean-market ingredient with a PubChem structure but no training data (e.g. methylephedrine,
    pentoxyverine) -> scored by the GNN as an unseen molecule, or
  * unresolved (herbal extracts, biologics, vitamins mixtures) -> reported, never guessed.
Requires OMNIPHARM_DATA_GO_KR_API_KEY (공공데이터포털 '식품의약품안전처_의약품 제품 허가정보' 활용신청).
"""

from __future__ import annotations

import json
import logging
import re
import time

import httpx
import pandas as pd

from omnipharm.config import settings
from omnipharm.pipeline.common import PROC, RAW
from omnipharm.pipeline.ingredients import Mapper, resolve_list

log = logging.getLogger("pipeline.mfds")
URL = "https://apis.data.go.kr/1471000/DrugPrdtPrmsnInfoService08/getDrugPrdtPrmsnInq08"
PAGE = 500
OUT_RAW = RAW / "mfds"

# dosage-form words at the end of Korean product names (longest first)
FORMS = sorted("""
연질캡슐 경질캡슐 서방캡슐 장용캡슐 캡슐 필름코팅정 서방정 장용정 츄어블정 구강붕해정 발포정 저작정 정제 정
시럽 건조시럽 현탁액 내복액 액 과립 세립 산 주사액 주사 주 크림 연고 겔 로션 패치 플라스타 첩부제 스프레이
점안액 점비액 좌제 트로키 캅셀 카플렛
""".split(), key=len, reverse=True)


def base_name(name: str) -> str:
    """'알러샷연질캡슐(세티리진염산염)' -> '알러샷', '타이레놀정500밀리그람' -> '타이레놀'."""
    s = re.sub(r"\(.*?\)|\[.*?\]", "", name or "")
    s = re.sub(r"[\d.,]+\s*(밀리그람|밀리그램|mg|그램|g|마이크로그램|㎎|%|ml|밀리리터|아이유|IU)?", "", s, flags=re.I)
    s = s.replace(" ", "")
    changed = True
    while changed and len(s) > 1:
        changed = False
        for f in FORMS:
            if s.endswith(f) and len(s) > len(f):
                s, changed = s[: -len(f)], True
                break
    return s


def _download() -> list[dict]:
    key = settings.data_go_kr_api_key
    if not key:
        raise RuntimeError("OMNIPHARM_DATA_GO_KR_API_KEY is not set (.env)")
    OUT_RAW.mkdir(parents=True, exist_ok=True)
    items: list[dict] = []
    page, total = 1, None
    with httpx.Client(timeout=120) as c:
        while total is None or (page - 1) * PAGE < total:
            f = OUT_RAW / f"products_{page:04d}.json"
            if f.exists():
                body = json.loads(f.read_text(encoding="utf-8"))
            else:
                for attempt in range(5):
                    try:
                        r = c.get(URL, params={"serviceKey": key, "type": "json", "numOfRows": PAGE, "pageNo": page})
                        body = r.json()["body"]
                        break
                    except Exception as e:  # network / quota hiccup -> retry
                        log.warning(f"page {page} attempt {attempt + 1}: {e}")
                        time.sleep(3 * (attempt + 1))
                else:
                    raise RuntimeError(f"MFDS download failed at page {page}")
                f.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
            total = int(body["totalCount"])
            items += body.get("items") or []
            if page % 20 == 0:
                log.info(f"MFDS {len(items):,}/{total:,}")
            page += 1
    return items


def build() -> pd.DataFrame:
    items = _download()
    df = pd.DataFrame(items)
    df = df[df["ITEM_INGR_NAME"].fillna("").str.len() > 0].copy()
    log.info(f"{len(df):,} products with ingredient lists")

    mapper = Mapper()
    mapping = mapper.map_all(p for s in df["ITEM_INGR_NAME"] for p in s.split("/"))
    res = df["ITEM_INGR_NAME"].map(lambda s: resolve_list(s.split("/"), mapping))
    out = pd.DataFrame({
        "item_seq": df["ITEM_SEQ"].astype(str), "name": df["ITEM_NAME"], "base": df["ITEM_NAME"].map(base_name),
        "eng_name": df["ITEM_ENG_NAME"].fillna(""), "company": df["ENTP_NAME"].fillna(""),
        "kind": df["SPCLTY_PBLC"].fillna(""), "status": df["CANCEL_NAME"].fillna(""),
        "category": df["PRDUCT_TYPE"].fillna(""), "ingredients_raw": df["ITEM_INGR_NAME"],
        "ingredients": res.map(lambda t: t[0]), "unresolved": res.map(lambda t: t[1]),
    }).reset_index(drop=True)
    out.to_parquet(PROC / "kr_products.parquet")
    full = (out["unresolved"].str.len() == 0) & (out["ingredients"].str.len() > 0)
    log.info(f"kr_products: {len(out):,} products, fully analysable {full.mean():.0%}, "
             f"partially {((out['ingredients'].str.len() > 0) & ~full).mean():.0%}; "
             f"{len(mapper.ext)} untrained molecules (ext_ingredients) so far")
    return out
