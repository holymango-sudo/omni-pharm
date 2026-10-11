"""Foreign marketed products -> ingredient lists (all free, no API key):

  US  FDA NDC Directory          every listed US product, Rx and OTC (Tylenol Cold, NyQuil, ...)
  CA  Health Canada DPD          marketed Canadian products (allfiles.zip)
  EU  EMA medicines report       centrally authorised EU medicines
  FR  BDPM (ANSM)                French public drug database (substance names in French)

Writes processed/world_products.parquet with the same columns as kr_products plus `country`.
Countries without a free bulk source are covered at the ingredient level (generic/INN names, PubChem
synonyms - see Engine.resolve_full).
"""

from __future__ import annotations

import io
import json
import logging
import re
import unicodedata
import zipfile

import httpx
import openpyxl
import pandas as pd

from omnipharm.pipeline.common import PROC, RAW
from omnipharm.pipeline.ingredients import Mapper, resolve_list

log = logging.getLogger("pipeline.world")
OUT_RAW = RAW / "world"
SOURCES = {
    "us_ndc.zip": "https://download.open.fda.gov/drug/ndc/drug-ndc-0001-of-0001.json.zip",
    "ca_dpd.zip": "https://www.canada.ca/content/dam/hc-sc/documents/services/drug-product-database/allfiles.zip",
    "eu_ema.xlsx": "https://www.ema.europa.eu/en/documents/report/medicines-output-medicines-report_en.xlsx",
    "fr_cis.txt": "https://base-donnees-publique.medicaments.gouv.fr/download/file/CIS_bdpm.txt",
    "fr_compo.txt": "https://base-donnees-publique.medicaments.gouv.fr/download/file/CIS_COMPO_bdpm.txt",
}
FORM = re.compile(
    r"\b(tablets?|caplets?|capsules?|softgels?|liquid|syrup|suspension|solution|oral|injection|injectable|"
    r"cream|ointment|gel|patch|spray|drops|powder|granules|extended[- ]release|delayed[- ]release|film[- ]coated|"
    r"chewable|for|and|comprim[ée]s?|g[ée]lules?|sirop|solution buvable|pellicul[ée]s?|s[ée]cables?|"
    r"[àa] lib[ée]ration prolong[ée]e)\b", re.I)


def base_name(name: str) -> str:
    """'TYLENOL Extra Strength 500 mg tablets' -> 'tylenol extra strength'; French: 'DOLIPRANE 1000 mg, comprimé'
    -> 'doliprane'."""
    s = name.lower().split(",")[0]
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)
    s = re.sub(r"[\d.,/]+\s*(mg|g|mcg|µg|ml|%|ui|iu|microgrammes?|milligrammes?)?\b", " ", s)
    s = FORM.sub(" ", s)
    return " ".join(s.split())


def _download() -> None:
    OUT_RAW.mkdir(parents=True, exist_ok=True)
    for f, url in SOURCES.items():
        p = OUT_RAW / f
        if p.exists() and p.stat().st_size > 1000:
            continue
        log.info(f"downloading {url}")
        with httpx.stream("GET", url, follow_redirects=True, timeout=600) as r:
            r.raise_for_status()
            p.write_bytes(b"".join(r.iter_bytes()))


def _us() -> pd.DataFrame:
    with zipfile.ZipFile(OUT_RAW / "us_ndc.zip") as z:
        res = json.load(z.open(z.namelist()[0]))["results"]
    rows = []
    for r in res:
        if not r.get("finished") or "HOMEOPATHIC" in (r.get("marketing_category") or "").upper():
            continue
        ptype = (r.get("product_type") or "").upper()
        if "HUMAN" not in ptype:
            continue
        ais = [a.get("name", "") for a in r.get("active_ingredients") or []]
        brand = (r.get("brand_name") or r.get("generic_name") or "").strip()
        if not ais or not brand:
            continue
        rows.append({"country": "US", "item_seq": r.get("product_ndc", ""), "name": brand,
                     "base": base_name(brand), "eng_name": brand, "company": r.get("labeler_name", ""),
                     "kind": "OTC" if "OTC" in ptype else "Rx", "status": "정상",
                     "category": r.get("dosage_form", ""), "ingredients_raw": "/".join(ais), "raw_list": ais,
                     "lang": "en"})
    df = pd.DataFrame(rows)
    # thousands of repackager/labeler duplicates: one row per (brand, composition)
    df["_k"] = df["name"].str.lower() + "|" + df["raw_list"].map(lambda v: "/".join(sorted(x.lower() for x in v)))
    return df.drop_duplicates("_k").drop(columns="_k")


def _ca() -> pd.DataFrame:
    with zipfile.ZipFile(OUT_RAW / "ca_dpd.zip") as z:
        def read(n, cols):
            return pd.read_csv(io.BytesIO(z.read(n)), header=None, dtype=str, encoding="latin-1",
                               usecols=range(len(cols)), names=cols, on_bad_lines="skip")
        drug = read("drug.txt", ["code", "cat", "cls", "din", "brand", "desc"])
        ing = read("ingred.txt", ["code", "aic", "ingredient"])
        sch = read("schedule.txt", ["code", "schedule"])
        comp = read("comp.txt", ["code", "mfr_code", "company"])
    drug = drug[drug["cls"].fillna("").str.upper().eq("HUMAN")]
    ings = ing.groupby("code")["ingredient"].agg(lambda s: [x for x in s if isinstance(x, str)])
    rx = sch.groupby("code")["schedule"].agg(lambda s: " ".join(map(str, s)).upper())
    co = comp.drop_duplicates("code").set_index("code")["company"]
    drug = drug[drug["code"].isin(ings.index)]
    return pd.DataFrame({
        "country": "CA", "item_seq": drug["din"].fillna(""), "name": drug["brand"].fillna("").str.strip(),
        "base": drug["brand"].fillna("").map(base_name), "eng_name": drug["brand"].fillna(""),
        "company": drug["code"].map(co).fillna(""),
        "kind": drug["code"].map(rx).fillna("").map(lambda s: "OTC" if "NON-PRESCRIPTION" in s or "OTC" in s else "Rx"),
        "status": "정상", "category": "", "ingredients_raw": drug["code"].map(ings).map("/".join),
        "raw_list": drug["code"].map(ings), "lang": "en"})


def _eu() -> pd.DataFrame:
    ws = openpyxl.load_workbook(OUT_RAW / "eu_ema.xlsx", read_only=True).active
    rows = list(ws.iter_rows(values_only=True))
    hi = next(i for i, r in enumerate(rows) if r and r[0] == "Category")
    hdr = [str(c) for c in rows[hi]]
    df = pd.DataFrame(rows[hi + 1:], columns=hdr)
    df = df[df["Category"].eq("Human")]
    col_sub = "Active substance"
    lists = df[col_sub].fillna("").map(lambda s: [x.strip() for x in re.split(r"\s*/\s*|;\s*|,\s+(?=[a-z])", s) if x.strip()])
    name = df["Name of medicine"].fillna("").astype(str)
    return pd.DataFrame({
        "country": "EU", "item_seq": df["EMA product number"].fillna(""), "name": name, "base": name.map(base_name),
        "eng_name": name, "company": "", "kind": "Rx",
        "status": df["Medicine status"].fillna("").map(lambda s: "정상" if s == "Authorised" else s),
        "category": df.get("Therapeutic area (MeSH)", ""), "ingredients_raw": df[col_sub].fillna(""),
        "raw_list": lists, "lang": "en"})


def _fr() -> pd.DataFrame:
    cis = pd.read_csv(OUT_RAW / "fr_cis.txt", sep="\t", header=None, dtype=str, encoding="latin-1",
                      quoting=3, on_bad_lines="skip", usecols=[0, 1, 4, 6, 10],
                      names=["cis", "name", "amm", "comm", "company"])
    comp = pd.read_csv(OUT_RAW / "fr_compo.txt", sep="\t", header=None, dtype=str, encoding="latin-1",
                       quoting=3, on_bad_lines="skip", usecols=[0, 3, 6, 7], names=["cis", "subst", "nature", "link"])
    # an active substance (SA) may be declared as its therapeutic fraction (FT): prefer FT when present
    comp["rank"] = comp["nature"].map({"FT": 0, "SA": 1}).fillna(2)
    comp = comp.sort_values("rank").drop_duplicates(["cis", "link"])
    ings = comp.groupby("cis")["subst"].agg(lambda s: list(dict.fromkeys(x.strip() for x in s if isinstance(x, str))))
    cis = cis[cis["cis"].isin(ings.index) & ~cis["name"].fillna("").str.contains("homéopath|BOIRON|LEHNING", case=False)]
    name = cis["name"].fillna("").str.strip()
    return pd.DataFrame({
        "country": "FR", "item_seq": cis["cis"], "name": name.str.split(",").str[0].str.strip(),
        "base": name.map(base_name), "eng_name": "", "company": cis["company"].fillna("").str.strip(),
        "kind": "", "status": cis["comm"].fillna("").map(lambda s: "정상" if s.startswith("Commercialis") else s),
        "category": "", "ingredients_raw": cis["cis"].map(ings).map("/".join), "raw_list": cis["cis"].map(ings),
        "lang": "fr"})


# --------------------------------------------------------------------------- Japan
# MHLW NHI price list (薬価基準収載品目リスト: every insured Rx product, Japanese names) + KEGG DRUG
# (Kyoto Univ., free REST for academic use) to translate Japanese ingredient names (JAN) to English.
JP_LIST = "https://www.mhlw.go.jp/topics/2026/04/tp20260401-01.html"
JP_SALT = re.compile(
    r"(ナトリウム水和物|カリウム水和物|カルシウム水和物|水和物|ナトリウム|カリウム|カルシウム|マグネシウム|塩酸塩|"
    r"硫酸塩|臭化水素酸塩|マレイン酸塩|メシル酸塩|ベシル酸塩|フマル酸塩|酒石酸塩|クエン酸塩|リン酸塩|酢酸塩|"
    r"コハク酸塩|乳酸塩|トシル酸塩|シュウ酸塩|エステル|水素)$")
JP_SALT_PREFIX = re.compile(r"^(塩酸|硫酸|臭化水素酸|マレイン酸|メシル酸|ベシル酸|フマル酸|酒石酸|クエン酸|リン酸|酢酸)")
JP_FORM = re.compile(
    r"(ＯＤ|OD)?(配合)?(錠|カプセル|細粒|顆粒|散|内用液|液|シロップ|ドライシロップ|注射液|注射用|注|キット|軟膏|"
    r"クリーム|ゲル|ローション|テープ|パップ|貼付剤|坐剤|点眼液|点鼻液|吸入液|吸入|エアゾール|ゼリー|チュアブル|徐放|"
    r"腸溶|フィルム|口腔内崩壊|小児用|静注|筋注|皮下注|バッグ|シリンジ|ペン)")


def jp_base_name(name: str) -> str:
    """'ロキソプロフェンNa錠60mg「サワイ」' -> 'ロキソプロフェンna', 'ロキソニン錠６０ｍｇ' -> 'ロキソニン'."""
    s = unicodedata.normalize("NFKC", name or "").lower()
    s = re.sub(r"「.*?」|\(.*?\)|（.*?）", "", s)
    s = re.sub(r"[\d.,/%]+\s*(mg|g|μg|mcg|ml|単位|万単位|w/v|%)?", "", s)
    segs = [x for x in JP_FORM.sub("|", s).split("|") if x.strip()]  # name before the dosage-form word
    return (segs[0] if segs else s).replace(" ", "").replace("　", "")


def _kegg_ja_en() -> dict[str, str]:
    """KEGG: Japanese drug name -> preferred English name (JAN/INN/JP/USP-tagged first)."""
    ja = (OUT_RAW / "kegg_drug_ja.tsv").read_text(encoding="utf-8")
    out: dict[str, str] = {}
    for line in ja.splitlines():
        names = [n.strip() for n in line.split("\t", 1)[-1].split(";") if n.strip()]
        en = [n for n in names if n.isascii()]
        jp = [n for n in names if not n.isascii()]
        if not en or not jp:
            continue
        pref = next((n for n in en if re.search(r"\((JAN|INN|JP\d*|USP|USAN|BAN)", n)), en[0])
        pref = re.sub(r"\s*\(.*?\)\s*$", "", pref).strip()
        for j in jp:
            j = unicodedata.normalize("NFKC", re.sub(r"\s*\((JP\d*|JAN|局外規|INN|TN)[^)]*\)\s*$", "", j)).strip()
            out.setdefault(j, pref)
    return out


def _jp_translate(ja: str, k2e: dict[str, str]) -> list[str]:
    """Japanese ingredient field -> English names. Combination products list components with '・'."""
    s = unicodedata.normalize("NFKC", ja).strip()

    def one(x: str) -> str | None:
        x = x.strip()
        for cand in (x, JP_SALT.sub("", x), JP_SALT_PREFIX.sub("", x), JP_SALT.sub("", JP_SALT_PREFIX.sub("", x))):
            if cand in k2e:
                return k2e[cand]
        return None

    e = one(s)
    if e:
        # KEGG names some combinations "Morphine and atropine": keep the components separate
        return [x.strip() for x in re.split(r"\s+and\s+|,\s*", e) if x.strip()] if "・" in s else [e]
    parts = [p for p in re.split(r"・|、", s) if p]
    return [one(p) or p for p in parts] if len(parts) > 1 else [s]


def _jp() -> pd.DataFrame:
    html = httpx.get(JP_LIST, follow_redirects=True, timeout=120).text
    links = re.findall(r'href="(/topics/\d{4}/\d{2}/xls/tp\d+-01_0([123])\.xlsx)"', html)
    latest: dict[str, str] = {}
    for href, kind in links:  # newest file per category (内用/注射/外用); names sort by date
        if kind not in latest or href > latest[kind]:
            latest[kind] = href
    for kind, href in latest.items():
        p = OUT_RAW / f"jp_nhi_{kind}.xlsx"
        if not p.exists():
            p.write_bytes(httpx.get("https://www.mhlw.go.jp" + href, follow_redirects=True, timeout=300).content)
    for f, url in (("kegg_drug_ja.tsv", "https://rest.kegg.jp/list/drug_ja"),):
        if not (OUT_RAW / f).exists():
            (OUT_RAW / f).write_text(httpx.get(url, timeout=300).text, encoding="utf-8")
    k2e = _kegg_ja_en()
    (PROC / "jp_ingredient_names.json").write_text(json.dumps(k2e, ensure_ascii=False), encoding="utf-8")
    rows = []
    for kind in sorted(latest):
        ws = openpyxl.load_workbook(OUT_RAW / f"jp_nhi_{kind}.xlsx", read_only=True).active
        for r in ws.iter_rows(min_row=2, values_only=True):
            if not r or not r[2] or not r[7]:
                continue
            name = unicodedata.normalize("NFKC", str(r[7])).strip()
            ings = _jp_translate(str(r[2]), k2e)
            rows.append({"country": "JP", "item_seq": str(r[1] or ""), "name": name, "base": jp_base_name(name),
                         "eng_name": "", "company": str(r[8] or ""), "kind": "Rx", "status": "정상",
                         "category": str(r[0] or ""), "ingredients_raw": unicodedata.normalize("NFKC", str(r[2])),
                         "raw_list": ings, "lang": "en"})
    df = pd.DataFrame(rows).drop_duplicates(["name", "ingredients_raw"])
    jp_left = sum(not x.isascii() for v in df["raw_list"] for x in v)
    log.info(f"JP: KEGG dictionary {len(k2e):,} names; untranslated ingredient mentions {jp_left:,}")
    return df


def build() -> pd.DataFrame:
    _download()
    parts = []
    for fn in (_us, _ca, _eu, _fr, _jp):
        try:
            d = fn()
            log.info(f"{fn.__name__[1:].upper()}: {len(d):,} products")
            parts.append(d)
        except Exception as e:  # one broken source must not block the others
            log.error(f"{fn.__name__}: {e}")
    df = pd.concat(parts, ignore_index=True)
    df = df[(df["raw_list"].map(len) > 0) & (df["name"].str.len() > 0)].reset_index(drop=True)

    mapper = Mapper()
    resolved: dict[int, tuple[list[str], list[str]]] = {}
    for lang in ("en", "fr"):
        sub = df[df["lang"] == lang]
        mp = mapper.map_all((s for v in sub["raw_list"] for s in v), lang)
        for i, v in sub["raw_list"].items():
            resolved[i] = resolve_list(v, mp)
    df["ingredients"] = [resolved[i][0] for i in df.index]
    df["unresolved"] = [resolved[i][1] for i in df.index]
    df = df.drop(columns=["raw_list", "lang"])
    df.to_parquet(PROC / "world_products.parquet")
    for c, g in df.groupby("country"):
        full = (g["unresolved"].map(len) == 0) & (g["ingredients"].map(len) > 0)
        log.info(f"{c}: {len(g):,} products, fully analysable {full.mean():.0%}, "
                 f"partially {((g['ingredients'].map(len) > 0) & ~full).mean():.0%}")
    log.info(f"world_products: {len(df):,}; untrained molecules (ext_ingredients): {len(mapper.ext)}")
    return df
