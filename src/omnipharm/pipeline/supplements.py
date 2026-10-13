"""건강기능식품: 45,902 Korean health functional foods (식약처 건강기능식품정보, data.go.kr 15056760 / HtfsInfoService03).

The API has no ingredient list. The functional ingredient is read from what the product declares about itself - the
bracketed type in its cautions ('[홍삼제품]', '[비타민K]'), the items of its standard ('진세노사이드 …의 합', 'EPA와 DHA의
합'), its name and its main function - with the rules in ING_RULES. Its cautions name the medicines to be careful with
('의약품(당뇨치료제, 혈액항응고제) 복용 시 섭취에 주의'); those classes become '식약처 표시사항' evidence ('주의').

Output: processed/kr_hff_products.parquet (same columns as kr_products + usage, efficacy, cautions, avoid_with)
"""

from __future__ import annotations

import logging
import re

import pandas as pd

from omnipharm.knowledge import lab_name
from omnipharm.pipeline.common import PROC, RAW
from omnipharm.pipeline.ingredients import Mapper
from omnipharm.pipeline.mfds_api import fetch_pages

log = logging.getLogger("pipeline.supplements")
URL = "https://apis.data.go.kr/1471000/HtfsInfoService03/getHtfsItem01"

# declared functional ingredient -> ingredient name (English, as KR_INGREDIENTS / the label index use them)
ING_RULES: list[tuple[re.Pattern, str]] = [(re.compile(p), n) for p, n in [
    (r"홍삼|인삼|진세노사이드", "ginseng"), (r"EPA|DHA|오메가\s*-?\s*3|크릴|어유", "omega-3 fatty acids"),
    (r"프로바이오틱스|유산균|비피더스|락토바실", "probiotics"), (r"은행잎|플라보놀\s*배당체|징코", "ginkgo biloba leaf extract"),
    (r"코엔자임\s*Q\s*10|코큐텐|유비데카레논", "ubidecarenone"), (r"녹차|카테킨|EGCG", "green tea extract"),
    (r"밀크씨슬|실리마린", "silymarin"), (r"가르시니아|Hydroxycitric|HCA", "garcinia cambogia"),
    (r"쏘팔메토|로르산", "saw palmetto"), (r"마리골드|루테인|지아잔틴", "lutein"), (r"프로폴리스", "propolis"),
    (r"크랜베리", "cranberry"), (r"홍국|모나콜린", "red yeast rice"), (r"강황|커큐민", "curcumin"),
    (r"(?<!흑)마늘", "garlic"), (r"감초", "glycyrrhiza"), (r"바나바|코로솔산", "banaba"), (r"크롬", "chromium"),
    (r"비타민\s*K\s*2|메나퀴논|MK-?7", "menaquinone"), (r"비타민\s*K(?!\s*2)|필로퀴논", "phytonadione"),
    (r"비타민\s*D|콜레칼시페롤|\+\s*D3|칼마디", "cholecalciferol"),
    (r"비타민\s*C|아스코르브", "ascorbic acid"), (r"비타민\s*E|토코페롤", "tocopherol"),
    (r"비타민\s*B\s*12|시아노코발라민", "cyanocobalamin"), (r"엽산", "folic acid"), (r"비오틴", "biotin"),
    (r"칼슘|칼마", "calcium carbonate"), (r"마그네슘|칼마", "magnesium oxide"), (r"아연", "zinc"), (r"셀렌|셀레늄", "selenium"),
    (r"(?<![가-힣])철(?![가-힣])|철분", "ferrous sulfate"), (r"글루코사민", "glucosamine"),
    (r"테아닌", "theanine"), (r"포스파티딜세린", "phosphatidylserine"), (r"세인트\s*존스\s*워트|히페리쿰", "hypericum perforatum"),
    (r"비타민\s*B\s*1(?!\d)|티아민", "thiamine"), (r"비타민\s*B\s*6|피리독신", "pyridoxine"), (r"차전자피", "psyllium"),
    (r"알로에", "aloe"), (r"공액리놀레산|CLA", "conjugated linoleic acid"), (r"감마리놀렌산|달맞이꽃", "gamma-linolenic acid"),
    (r"키토산", "chitosan"), (r"이소플라본", "soy isoflavones"), (r"엠에스엠|MSM", "methylsulfonylmethane"),
    (r"크레아틴", "creatine"), (r"난소화성\s*말토덱스트린|식이섬유", "dietary fiber"), (r"레시틴", "lecithin"),
    (r"옥타코사놀", "octacosanol"), (r"히알루론산", "hyaluronic acid"), (r"콜라겐", "collagen"),
]]
_BRACKET = re.compile(r"\[([^\]]{2,25})\]")
_STD_ITEM = re.compile(r"(?:^|\n)\s*(?:\d+\.|[①-⑳])\s*([^:：\n]{2,40})[:：]")
# a list of medicines on ONE line: '의약품(당뇨치료제, 혈액항응고제) 복용 시', '항응고제 복용 시 섭취에 주의'
_DRUG_LIST = re.compile(r"의약품\s*\(([^)\n]{2,100})\)|([가-힣 ,·및]{2,60}(?:제|약))\s*(?:를|을)?\s*복용\s*(?:중|하는|시)")


def declared_ingredients(r: dict) -> list[str]:
    """Functional ingredients this product declares (names as in ING_RULES), most specific first."""
    text = " ".join([r.get("PRDUCT") or "", " ".join(_BRACKET.findall(r.get("INTAKE_HINT1") or "")),
                     " ".join(_STD_ITEM.findall(r.get("BASE_STANDARD") or ""))])
    out = list(dict.fromkeys(n for rx, n in ING_RULES if rx.search(text)))
    if "menaquinone" in out:  # '[비타민K]' is the category heading of a K2 product, not a second (K1) ingredient
        out = [n for n in out if n != "phytonadione"]
    return out


def medicines_to_watch(cautions: str) -> list[str]:
    """'의약품(당뇨치료제, 혈액항응고제) 복용 시 섭취에 주의' -> ['당뇨치료제', '혈액항응고제']."""
    out = []
    for a, b in _DRUG_LIST.findall(cautions or ""):
        for x in re.split(r"[,·/]|\s및\s|\s또는\s", a or b):
            x = re.sub(r"\s*등$", "", " ".join(x.split())).strip(" .")
            if 2 <= len(x) <= 20 and re.search(r"(제|약)$", x):  # a medicine class, not '임신' / '간 질환이 있거나'
                out.append(x)
    return list(dict.fromkeys(out))[:10]


def build() -> pd.DataFrame:
    rows = fetch_pages(URL, RAW / "mfds_food" / "hff", label="건강기능식품")
    recs = []
    for r in rows:
        name = " ".join((r.get("PRDUCT") or "").split())
        if not name:
            continue
        ings = declared_ingredients(r)
        cautions = r.get("INTAKE_HINT1") or ""
        recs.append({"item_seq": str(r.get("STTEMNT_NO")), "name": name, "base": re.sub(r"\(.*?\)|\s", "", name),
                     "eng_name": "", "company": r.get("ENTRPS") or "", "kind": "건강기능식품", "status": "정상",
                     "category": "건강기능식품", "ingredients_raw": ", ".join(ings), "ingredients": ings,
                     # a product without a recognised functional ingredient is still found by its name
                     "unresolved": [] if ings else [(_BRACKET.findall(cautions) or ["건강기능식품"])[0]],
                     "usage": r.get("SRV_USE") or "", "efficacy": r.get("MAIN_FNCTN") or "", "cautions": cautions,
                     "avoid_with": medicines_to_watch(cautions)})
    df = pd.DataFrame(recs).drop_duplicates("item_seq")
    mapper = Mapper()
    plain = {i for v in df["ingredients"] for i in v}
    pm = mapper.map_all(plain) if plain else {}
    df["ingredients"] = [list(dict.fromkeys(pm.get(i) or f"lab:{lab_name(i) or i}" for i in v)) for v in df["ingredients"]]
    df.to_parquet(PROC / "kr_hff_products.parquet")
    log.info(f"건강기능식품: {len(df):,} products | functional ingredient recognised {int((df['ingredients'].map(len) > 0).mean() * 100)}% "
             f"| medicines named in cautions {int(df['avoid_with'].map(len).gt(0).sum()):,}")
    return df
