"""Ingredient strength per dosing unit for marketed products (needed to turn "1 tablet x 3/day" into mg/day).

Output processed/product_strengths.parquet: one row per product
    country, item_seq, unit (dosing unit shown to the user: 정/캡슐/포/mL/錠/tablet ...),
    per ("unit" = amount per one unit, "mL" = amount per millilitre), strengths (JSON {ingredient key: mg})
Sources: KR 식약처 허가 상세(MATERIAL_NAME: '총량 : 1포20밀리리터|성분명 : 아세트아미노펜|분량 : 325|단위 : 밀리그램'),
US NDC active_ingredients strength ('160 mg/5mL'), CA DPD ingred.txt, FR BDPM composition, JP 規格 ('60mg1錠').
Products whose amounts cannot be parsed unambiguously are left out - the engine then asks for mg directly.
"""

from __future__ import annotations

import collections
import io
import json
import logging
import re
import time
import unicodedata
import zipfile

import httpx
import pandas as pd

from omnipharm.config import settings
from omnipharm.knowledge import KR_INGREDIENTS, lab_name
from omnipharm.pipeline.common import PROC, RAW
from omnipharm.pipeline.ingredients import Mapper

log = logging.getLogger("pipeline.strengths")
KR_URL = "https://apis.data.go.kr/1471000/DrugPrdtPrmsnInfoService08/getDrugPrdtPrmsnDtlInq08"
UNIT_MG = {"밀리그램": 1.0, "밀리그람": 1.0, "mg": 1.0, "그램": 1000.0, "g": 1000.0, "마이크로그램": 0.001,
           "μg": 0.001, "mcg": 0.001, "µg": 0.001, "ug": 0.001}


def _mg(amount: str | float, unit: str) -> float | None:
    try:
        a = float(str(amount).replace(",", ".").strip())
    except ValueError:
        return None
    f = UNIT_MG.get(str(unit).strip().lower())
    return a * f if f and a > 0 else None


# --------------------------------------------------------------------------- Korea
def _kr_download() -> list[dict]:
    """Detail endpoint, paged; only ITEM_SEQ + MATERIAL_NAME are kept (the label documents are large)."""
    out_f = RAW / "mfds" / "materials.json"
    if out_f.exists():
        return json.loads(out_f.read_text(encoding="utf-8"))
    key = settings.data_go_kr_api_key
    if not key:
        log.warning("no data.go.kr key: Korean strengths skipped")
        return []
    rows, page, total = [], 1, None
    with httpx.Client(timeout=180) as c:
        while total is None or (page - 1) * 100 < total:
            for attempt in range(5):
                try:
                    body = c.get(KR_URL, params={"serviceKey": key, "type": "json", "numOfRows": 100,
                                                 "pageNo": page}).json()["body"]
                    break
                except Exception as e:  # network / quota hiccup
                    log.warning(f"page {page}: {e}")
                    time.sleep(3 * (attempt + 1))
            else:
                raise RuntimeError(f"MFDS detail download failed at page {page}")
            total = int(body["totalCount"])
            rows += [{"item_seq": str(it.get("ITEM_SEQ")), "material": it.get("MATERIAL_NAME") or ""}
                     for it in body.get("items") or []]
            if page % 50 == 0:
                log.info(f"MFDS detail {len(rows):,}/{total:,}")
            page += 1
    out_f.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return rows


KR_UNITS = r"정|캡슐|연질캡슐|포|병|앰플|바이알|관|매|개|좌제|g|그램|밀리리터|mL|ml"
KO_SALT = re.compile(r"(염산염|이염산염|시트르산염|말레산염|브롬화수소산염|황산염|타르타르산염|푸마르산염|메실산염|베실산염|"
                     r"숙신산염|인산염|아세트산염|나트륨|칼륨|칼슘|마그네슘|무수물|수화물|일수화물|이수화물|삼수화물|수화물)+$")


def _kr_basis(total: str) -> tuple[str, str, float] | None:
    """'1포20밀리리터' -> ('포', 'unit', 1); '1정615.04밀리그램' -> ('정', 'unit', 1); '100밀리리터' -> ('mL', 'mL', 100)."""
    t = total.replace(" ", "")
    m = re.match(rf"^(\d+(?:\.\d+)?)({KR_UNITS})", t)
    if not m:
        return None
    n, u = float(m.group(1)), m.group(2)
    if u in ("밀리리터", "mL", "ml"):
        return "mL", "mL", n
    if u in ("g", "그램"):
        return "g", "g", n
    return u, "unit", n


GRANULE = re.compile(r"과립|코팅|직타|\bDC\b|90%|95%")


def _kr_weight(total: str) -> float | None:
    """'1정615.04밀리그램' / '1정 (약 787.95 밀리그램) 중-속방층' -> mg weight of ONE unit (None if not stated)."""
    t = total.replace(" ", "")
    m = re.match(rf"^(\d+(?:\.\d+)?)({KR_UNITS})\(?약?(\d+(?:\.\d+)?)(밀리그램|mg|그램|g)", t)
    if not m or m.group(2) in ("밀리리터", "mL", "ml", "g", "그램"):
        return None
    w = float(m.group(3)) * (1000 if m.group(4) in ("그램", "g") else 1)
    return w / float(m.group(1))


def _kr(products: pd.DataFrame, mapper: Mapper) -> list[dict]:
    mats = _kr_download()
    if not mats:
        return []
    parsed: dict[str, list[tuple[str, float, str]]] = {}
    for r in mats:
        items = []
        for seg in r["material"].split(";"):
            f = dict(p.split(" : ", 1) for p in seg.split("|") if " : " in p)
            name, amt, unit, total = (f.get(k, "").strip() for k in ("성분명", "분량", "단위", "총량"))
            mg = _mg(amt, unit)
            if name and mg:
                items.append((name, mg, total))
        if items:
            parsed[r["item_seq"]] = items
    # Korean ingredient name -> ingredient key, learned from single-ingredient products
    eng = products.set_index("item_seq")["ingredients_raw"].to_dict()
    mapping = mapper.map_all({s.strip() for v in eng.values() for s in str(v).split("/")})
    ko2key: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for seq, items in parsed.items():
        en = [s.strip() for s in str(eng.get(seq, "")).split("/") if s.strip()]
        if len(items) == 1 and len(en) == 1 and mapping.get(en[0]):
            ko2key[items[0][0]][mapping[en[0]]] += 1
    k2 = {ko: c.most_common(1)[0][0] for ko, c in ko2key.items()}
    known_keys = mapper.known | set(mapper.ext)
    # Korean ingredient dictionary for search and display (stage output, read by the engine):
    # base Korean name (salt words stripped) -> key; ingredients without a structure become "lab:<english>"
    ko_dict: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for seq, items in parsed.items():
        en = [s.strip() for s in str(eng.get(seq, "")).split("/") if s.strip()]
        if len(items) == 1 and len(en) == 1:
            key = mapping.get(en[0]) or (f"lab:{lab_name(en[0])}" if lab_name(en[0]) else None)
            base = KO_SALT.sub("", re.sub(r"^(dl|d|l)-", "", items[0][0].replace(" ", "")))
            if key and len(base) >= 2:
                ko_dict[base][key] += 1
    (PROC / "kr_ingredient_names.json").write_text(json.dumps(
        {ko: c.most_common(1)[0][0] for ko, c in ko_dict.items() if c.most_common(1)[0][1] >= 0.6 * sum(c.values())},
        ensure_ascii=False, indent=0), encoding="utf-8")

    def ko_key(ko: str) -> str | None:
        """Learned name, else Korean salt/hydrate words stripped + curated Korean INN table."""
        if ko in k2:
            return k2[ko]
        base = re.sub(r"^(dl|d|l)-", "", ko)
        base = KO_SALT.sub("", base)
        if base in k2:
            return k2[base]
        en = KR_INGREDIENTS.get(base)
        if en:
            key = mapper.by_name(en)
            if key and key in known_keys:
                return key
        return None

    out = []
    names = products.set_index("item_seq")["name"].to_dict()
    for seq, items in parsed.items():
        basis = _kr_basis(items[0][2])
        if not basis:
            continue
        unit, per, n = basis
        s: dict[str, float] = {}
        top: dict[str, float] = {}
        left = []
        for ko, mg, _ in items:
            key = ko_key(ko)
            if key:
                s[key] = s.get(key, 0) + mg / n  # amount per ONE unit / per mL / per g (bilayer tablets: layers add up)
                top[key] = max(top.get(key, 0), mg / n)
            else:
                left.append(mg / n)
        # ... unless the layers cannot add up: '1정(약 787.95밀리그램) 중-속방층 650 ; 서방층 325' lists the total and one
        # layer (650 + 325 = 975 mg would not fit in a 788 mg tablet)
        weight = _kr_weight(items[0][2])
        if weight and per == "unit" and sum(s.values()) > weight * 1.001:
            s = {k: top[k] if s[k] > top[k] else s[k] for k in s}
        # '아세트아미노펜 과립 555.556 mg' is the weight of the coated granules; the product name states the drug amount
        if len(s) == 1 and len(items) == 1 and GRANULE.search(items[0][0]):
            m = re.search(r"(\d+(?:\.\d+)?)\s*(밀리그램|밀리그람|mg)", str(names.get(seq, "")))
            k0 = next(iter(s))
            if per == "unit" and m and float(m.group(1)) <= s[k0] <= float(m.group(1)) * 1.35:
                s[k0] = float(m.group(1))  # the name states the amount per tablet / sachet
        # exactly one Korean name and one product ingredient left unmatched: they are the same ingredient
        en = [mapping.get(x.strip()) for x in str(eng.get(seq, "")).split("/") if x.strip()]
        rest = [k for k in dict.fromkeys(k for k in en if k) if k not in s]
        if len(left) == 1 and len(rest) == 1:
            s[rest[0]] = left[0]
        if s:
            out.append({"country": "KR", "item_seq": seq, "unit": unit, "per": per, "strengths": s})
    log.info(f"KR: {len(out):,} products with strengths ({len(k2):,} Korean ingredient names learned)")
    return out


# --------------------------------------------------------------------------- other countries
STR_RE = re.compile(r"([\d.,]+)\s*(mg|g|mcg|ug|µg|μg)\s*(?:/\s*([\d.,]*)\s*([a-zA-Z]+))?", re.I)


def _parse_strength(s: str) -> tuple[float, str] | None:
    """'160 mg/5mL' -> (32.0, 'mL'); '500 mg/1' -> (500.0, 'unit')."""
    m = STR_RE.search(s or "")
    if not m:
        return None
    mg = _mg(m.group(1), m.group(2))
    if mg is None:
        return None
    denom_n, denom_u = m.group(3), (m.group(4) or "").lower()
    n = float(denom_n.replace(",", ".")) if denom_n else 1.0
    if denom_u in ("ml",):
        return mg / n, "mL"
    if denom_u in ("g",):
        return mg / n, "g"
    if n != 1 and denom_u:
        return None  # e.g. per 24h patches - not dosed in units
    return mg, "unit"


def _us(mapper: Mapper) -> list[dict]:
    with zipfile.ZipFile(RAW / "world" / "us_ndc.zip") as z:
        res = json.load(z.open(z.namelist()[0]))["results"]
    # same filter as the product table (world._us): finished human products, no homeopathy
    res = [r for r in res if r.get("finished") and "HUMAN" in (r.get("product_type") or "").upper()
           and "HOMEOPATHIC" not in (r.get("marketing_category") or "").upper()]
    out = []
    # map every ingredient name once (mapping per product would re-save the cache 100k times)
    mp = mapper.map_all({a.get("name", "") for r in res for a in (r.get("active_ingredients") or [])})
    for r in res:  # keyed per NDC: the product table keeps one NDC per brand+composition, any of them matches
        ais = r.get("active_ingredients") or []
        if not ais or not r.get("finished"):
            continue
        parsed = [(a.get("name", ""), _parse_strength(a.get("strength", ""))) for a in ais]
        if any(p is None for _, p in parsed) or len({p[1] for _, p in parsed}) != 1:
            continue
        per = parsed[0][1][1]
        form = (r.get("dosage_form") or "").lower()
        unit = "mL" if per == "mL" else "g" if per == "g" else ("capsule" if "capsule" in form else "tablet")
        s = {mp[n]: v[0] for n, v in parsed if mp.get(n)}
        if s:
            out.append({"country": "US", "item_seq": r.get("product_ndc", ""), "unit": unit, "per": per,
                        "strengths": s})
    return out


def _ca(mapper: Mapper) -> list[dict]:
    with zipfile.ZipFile(RAW / "world" / "ca_dpd.zip") as z:
        cols = ["code", "aic", "ingredient", "supplied", "strength", "unit", "stype", "dval", "base", "dunit"]
        ing = pd.read_csv(io.BytesIO(z.read("ingred.txt")), header=None, dtype=str, encoding="latin-1",
                          usecols=range(10), names=cols, on_bad_lines="skip")
        drug = pd.read_csv(io.BytesIO(z.read("drug.txt")), header=None, dtype=str, encoding="latin-1",
                           usecols=range(5), names=["code", "cat", "cls", "din", "brand"], on_bad_lines="skip")
    din = drug.set_index("code")["din"].to_dict()
    mp = mapper.map_all(set(ing["ingredient"].dropna()))
    out = []
    for code, g in ing.groupby("code"):
        s, per = {}, set()
        for r in g.itertuples():
            mg = _mg(r.strength, r.unit or "")
            key = mp.get(r.ingredient)
            if mg is None or not key:
                s = {}
                break
            du = (r.dunit or "").upper() if isinstance(r.dunit, str) else ""
            dv = float(r.dval) if isinstance(r.dval, str) and r.dval.replace(".", "", 1).isdigit() else 1.0
            if du == "ML":
                s[key] = mg / dv
                per.add("mL")
            else:
                s[key] = mg
                per.add("unit")
        if s and len(per) == 1:
            p = per.pop()
            out.append({"country": "CA", "item_seq": din.get(code, ""), "unit": "mL" if p == "mL" else "tablet",
                        "per": p, "strengths": s})
    return out


def _fr(mapper: Mapper) -> list[dict]:
    comp = pd.read_csv(RAW / "world" / "fr_compo.txt", sep="\t", header=None, dtype=str, encoding="latin-1",
                       quoting=3, on_bad_lines="skip", usecols=[0, 3, 4, 5, 6, 7],
                       names=["cis", "subst", "dose", "ref", "nature", "link"])
    comp["rank"] = comp["nature"].map({"FT": 0, "SA": 1}).fillna(2)
    comp = comp.sort_values("rank").drop_duplicates(["cis", "link"])
    mp = mapper.map_all(set(comp["subst"].dropna()), "fr")
    out = []
    for cis, g in comp.groupby("cis"):
        s, per = {}, set()
        for r in g.itertuples():
            m = re.match(r"([\d.,\s]+)\s*(mg|g|microgrammes?|µg)", str(r.dose), re.I)
            key = mp.get(r.subst)
            if not m or not key:
                s = {}
                break
            u = m.group(2).lower()
            mg = _mg(m.group(1).replace(" ", ""), "mcg" if u.startswith(("micro", "µ")) else u)
            ref = str(r.ref).lower()
            rm = re.match(r"([\d.,]+)\s*ml", ref)
            if rm:
                s[key] = (mg or 0) / float(rm.group(1).replace(",", "."))
                per.add("mL")
            elif re.match(r"(un|une)\s", ref):
                s[key] = mg or 0
                per.add("unit")
            else:
                s = {}
                break
        if s and len(per) == 1 and all(s.values()):
            p = per.pop()
            out.append({"country": "FR", "item_seq": cis, "unit": "mL" if p == "mL" else "comprimé/gélule",
                        "per": p, "strengths": s})
    return out


JP_UNIT = {"錠": "錠", "カプセル": "カプセル", "包": "包", "管": "管", "瓶": "瓶", "袋": "袋", "個": "個", "枚": "枚"}


def _jp(products: pd.DataFrame, mapper: Mapper) -> list[dict]:
    """規格 is only parseable for single-ingredient products: '60mg1錠', '20%1g', '500mg1g', '2mg1mL'."""
    import openpyxl

    rows = []
    for k in ("1", "2", "3"):
        f = RAW / "world" / f"jp_nhi_{k}.xlsx"
        if not f.exists():
            continue
        for r in openpyxl.load_workbook(f, read_only=True).active.iter_rows(min_row=2, values_only=True):
            if r and r[3] and r[7]:
                rows.append((unicodedata.normalize("NFKC", str(r[7])).strip(), unicodedata.normalize("NFKC", str(r[3]))))
    jp = products[products["country"] == "JP"].set_index("name")
    out = []
    for name, spec in rows:
        if name not in jp.index:
            continue
        row = jp.loc[name]
        row = row.iloc[0] if isinstance(row, pd.DataFrame) else row
        ings = list(row["ingredients"])
        if len(ings) != 1:
            continue
        m = re.match(r"^([\d.]+)(mg|g|μg|mcg)1(錠|カプセル|包|管|瓶|袋|個|枚|g|mL)$", spec.replace(" ", ""))
        m2 = re.match(r"^([\d.]+)%1(g|mL)$", spec.replace(" ", ""))
        if m:
            mg, unit = _mg(m.group(1), m.group(2)), m.group(3)
            per = "mL" if unit == "mL" else "g" if unit == "g" else "unit"
        elif m2:
            mg, unit = float(m2.group(1)) * 10, m2.group(2)  # % w/w: 1% = 10 mg per g (or mL)
            per = unit
        else:
            continue
        if mg:
            out.append({"country": "JP", "item_seq": row["item_seq"], "unit": unit, "per": per,
                        "strengths": {ings[0]: mg}})
    return out


def build() -> pd.DataFrame:
    mapper = Mapper()
    kr = pd.read_parquet(PROC / "kr_products.parquet") if (PROC / "kr_products.parquet").exists() else None
    world = pd.read_parquet(PROC / "world_products.parquet") if (PROC / "world_products.parquet").exists() else None
    rows: list[dict] = []
    if kr is not None:
        rows += _kr(kr, mapper)
    for fn in (_us, _ca, _fr):
        try:
            got = fn(mapper)
            log.info(f"{fn.__name__[1:].upper()}: {len(got):,} products with strengths")
            rows += got
        except Exception as e:  # one broken source must not block the others
            log.error(f"{fn.__name__}: {e}")
    if world is not None:
        got = _jp(world, mapper)
        log.info(f"JP: {len(got):,} products with strengths")
        rows += got
    df = pd.DataFrame(rows)
    df["strengths"] = df["strengths"].map(lambda d: json.dumps({k: round(v, 4) for k, v in d.items()}))
    df = df.drop_duplicates(["country", "item_seq"])
    df.to_parquet(PROC / "product_strengths.parquet")
    log.info(f"product_strengths: {len(df):,} products")
    return df
