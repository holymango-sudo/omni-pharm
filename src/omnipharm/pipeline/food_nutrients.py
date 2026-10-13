"""Korean food nutrient DB (식약처 식품영양성분DB정보, data.go.kr 15127578 / FoodNtrCpntDbInfo03): 341,645 foods, dishes,
café drinks and processed foods with their nutrients per 100 g / 100 mL.

Only what changes a medicine's effect is kept: caffeine (total caffeine with painkillers / cold medicines, 400 mg a day),
vitamin K (warfarin), potassium (ACE inhibitors / ARBs / potassium-sparing diuretics) and calcium (binds quinolone /
tetracycline antibiotics and thyroid hormone; magnesium is shown only). A food is looked up by its own name
('커피_아메리카노'), a drink's name without the category ('아메리카노', temperature / size marks removed), brand + name
('메가커피 아메리카노') and a raw food's first name ('상추, 잎상추, …, 생것' -> '상추'); several rows under one key (one
per brand / size / variety) are summarised by their median.

Output: processed/food_nutrients.parquet  key, label (a name that normalises to the key), name, maker, group, basis
        ('100g' | '100mL'), caffeine_mg, vitamin_k_ug, potassium_mg, calcium_mg, magnesium_mg, n (rows summarised)
"""

from __future__ import annotations

import logging
import re

import pandas as pd

from omnipharm.pipeline.common import PROC, RAW
from omnipharm.pipeline.mfds_api import fetch_pages

log = logging.getLogger("pipeline.food_nutrients")
URL = "https://apis.data.go.kr/1471000/FoodNtrCpntDbInfo03/getFoodNtrCpntDbInq03"
KEEP = ("FOOD_CD", "FOOD_NM_KR", "DB_GRP_NM", "DB_CLASS_NM", "FOOD_CAT1_NM", "FOOD_REF_NM", "SERVING_SIZE",
        "NUTRI_AMOUNT_SERVING", "AMT_NUM1", "AMT_NUM9", "AMT_NUM12", "AMT_NUM13", "AMT_NUM48", "AMT_NUM100",
        "AMT_NUM111", "MAKER_NM", "ITEM_REPORT_NO", "FOOD_OR_NM")
COLS = {"AMT_NUM100": "caffeine_mg", "AMT_NUM48": "vitamin_k_ug", "AMT_NUM12": "potassium_mg", "AMT_NUM9": "calcium_mg",
        "AMT_NUM111": "magnesium_mg"}
# per 100 g / 100 mL: amounts that matter for a medicine (others are not kept). Potassium 300 mg: banana (~360 mg),
# potato, spinach, avocado - the foods kidney / ACE inhibitor diet sheets list as high-potassium
THRESHOLD = {"caffeine_mg": 5.0, "vitamin_k_ug": 100.0, "potassium_mg": 300.0, "calcium_mg": 200.0,
             "magnesium_mg": 100.0}
KO = {"caffeine_mg": ("카페인", "mg"), "vitamin_k_ug": ("비타민K", "μg"), "potassium_mg": ("칼륨", "mg"),
      "calcium_mg": ("칼슘", "mg"), "magnesium_mg": ("마그네슘", "mg")}
# temperature / size marks of café drinks, and '생것' (a bare food name means the fresh food: '시금치, 생것'; dried or
# cooked forms keep their mark - '녹차, 말린것' is tea leaves, not the drink)
_MARKS = re.compile(r"\(.*?\)|\[.*?\]|\b(핫|아이스|HOT|ICED|tall|grande|venti|small|medium|large|레귤러|라지)\b"
                    r"|\b[SMLR]\b|생것", re.I)
DRINKS = "음료 및 차류"


def norm(s: str) -> str:
    return re.sub(r"[\s_,·\-/]", "", s or "").lower()


def _num(v) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def keys_of(name: str, maker: str | None, drink: bool = False) -> list[tuple[str, str]]:
    """(lookup key, label that normalises back to it): '커피_아메리카노 아이스(ICED)' by 메가커피 ->
    [('커피아메리카노아이스(iced)', '커피_아메리카노 아이스(ICED)'), ('아메리카노', '아메리카노'),
     ('메가커피아메리카노', '메가커피 아메리카노')]. The part after '_' is a name of its own only for drinks
    ('커피_아메리카노'); for a dish it is the variant ('샐러드_감자' is potato salad, not a potato)."""
    out = [(norm(name), name)]
    if "_" in name and not drink:
        return out
    if "," in name and "생것" in name:  # a raw food with its variety: '상추, 잎상추, 뚝섬적축면, 적색, 생것' -> '상추'
        first = name.split(",")[0].strip()
        out.append((norm(first), first))
    short = name.split("_", 1)[1] if "_" in name else name
    base_label = " ".join(_MARKS.sub(" ", short).split()).strip(" ,")
    if len(norm(base_label)) >= 2:
        out.append((norm(base_label), base_label))
        if maker:
            out.append((norm(maker + base_label), f"{maker} {base_label}"))
    return list({k: (k, lab) for k, lab in out}.values())


def build() -> pd.DataFrame:
    rows = fetch_pages(URL, RAW / "mfds_food" / "nutrients", keep=KEEP, label="식품영양성분")
    recs = []
    for r in rows:
        vals = {c: _num(r.get(src)) for src, c in COLS.items()}
        if not any(v is not None and v >= THRESHOLD[c] for c, v in vals.items()):
            continue
        drink = r.get("FOOD_CAT1_NM") == DRINKS
        # some dishes of the restaurant-food group are marked '100mL' (계란 덮밥, 시금치나물): a dish is per 100 g
        basis = ("100mL" if "ml" in (r.get("SERVING_SIZE") or "").lower() and (drink or r.get("DB_GRP_NM") != "음식")
                 else "100g")
        name = r.get("FOOD_NM_KR") or ""
        for k, label in keys_of(name, r.get("MAKER_NM"), drink=drink):
            recs.append({"key": k, "label": label, "name": name, "maker": r.get("MAKER_NM") or "",
                         "group": r.get("DB_GRP_NM") or "", "exact": norm(name) == k,
                         "representative": r.get("DB_CLASS_NM") == "품목대표", "basis": basis, **vals})
    df = pd.DataFrame(recs)
    # one row per key: the raw food ('시금치' is fresh spinach, not a processed product of that name), else a food with
    # exactly that name, else the representative row, else the median of the brands / sizes ('아메리카노': 79 café drinks)
    agg = []
    for k, g in df.groupby("key", sort=False):
        src = next(s for s in (g[g["group"] == "원재료성"], g[g["exact"]], g[g["representative"]], g) if len(s))
        one = src["name"].nunique() == 1
        row = {"key": k, "label": src["label"].iloc[0], "name": src["name"].iloc[0] if one else src["label"].iloc[0],
               "maker": src["maker"].iloc[0] if src["maker"].nunique() == 1 else "",
               "group": src["group"].iloc[0], "basis": src["basis"].mode().iloc[0], "n": len(src)}
        for c in COLS.values():
            v = src[c].dropna()
            row[c] = round(float(v.median()), 2) if len(v) else None
        agg.append(row)
    out = pd.DataFrame(agg)
    out.to_parquet(PROC / "food_nutrients.parquet")
    log.info(f"food nutrients: {len(rows):,} foods read, {len(out):,} lookup names kept | "
             + ", ".join(f"{c} {int((out[c] >= THRESHOLD[c]).sum()):,}" for c in COLS.values()))
    return out
