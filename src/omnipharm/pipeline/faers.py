"""FAERS quarterly ASCII files -> patient-level report table.

FAERS (FDA Adverse Event Reporting System) is the raw source TWOSIDES was mined from.
Unlike TWOSIDES it keeps the *patient*: age, sex, weight and what they were treated for.
That is what lets the model learn "these drugs in THIS patient".

Per quarter we write data/interim/faers/<YYYYQn>.parquet with one row per report:
  primaryid, caseid, caseversion, quarter, age (years), sex (F/M/""), wt (kg),
  ingredients (list of raw lowercase active-ingredient strings),
  events (list of target adverse-event names), renal/hepatic/cardiac/diabetes/hypertension (bool)
"""

from __future__ import annotations

import io
import logging
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests

from omnipharm.knowledge import COMORBIDITY_PATTERNS, PT_SYNONYMS, TARGET_LOWER
from omnipharm.pipeline.common import INTERIM, RAW

log = logging.getLogger("pipeline.faers")

BASE = "https://fis.fda.gov/content/Exports/faers_ascii_{y}{q}{n}.zip"
FAERS_RAW = RAW / "faers"
FAERS_INTERIM = INTERIM / "faers"

_AGE_TO_YEARS = {"YR": 1.0, "DEC": 10.0, "MON": 1 / 12, "WK": 1 / 52.18, "DY": 1 / 365.25, "HR": 1 / 8766}
_WT_TO_KG = {"KG": 1.0, "LBS": 0.45359237, "GMS": 0.001}
_EVENT_MAP = {**{k: v for k, v in TARGET_LOWER.items()}, **PT_SYNONYMS}


def quarters(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    out, (y, q) = [], start
    while (y, q) <= end:
        out.append((y, q))
        y, q = (y, q + 1) if q < 4 else (y + 1, 1)
    return out


def download_quarter(y: int, q: int, retries: int = 3) -> Path | None:
    """Download one quarter (resumable; skips complete files). Returns None if not published."""
    FAERS_RAW.mkdir(parents=True, exist_ok=True)
    dest = FAERS_RAW / f"faers_ascii_{y}Q{q}.zip"
    if dest.exists() and zipfile.is_zipfile(dest):
        return dest
    legacy = (y, q) <= (2012, 3)  # Legacy AERS (2004Q1-2012Q3) is published as aers_ascii_YYYYqN.zip
    for letter in ("Q", "q"):
        url = BASE.format(y=y, q=letter, n=q)
        if legacy:
            url = url.replace("faers_ascii_", "aers_ascii_")
        for attempt in range(retries):
            try:
                with requests.get(url, stream=True, timeout=60) as r:
                    if r.status_code == 404:
                        break
                    r.raise_for_status()
                    if "zip" not in r.headers.get("Content-Type", "zip") and "octet" not in r.headers.get(
                            "Content-Type", "octet"):
                        break  # the site answers 200 + HTML for unknown files
                    tmp = dest.with_suffix(".part")
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_content(1 << 20):
                            f.write(chunk)
                if zipfile.is_zipfile(tmp):
                    tmp.replace(dest)
                    log.info(f"downloaded {dest.name} ({dest.stat().st_size / 1e6:.0f} MB)")
                    return dest
                tmp.unlink(missing_ok=True)
                break
            except Exception as e:  # network hiccup -> retry
                log.warning(f"{url} attempt {attempt + 1} failed: {e}")
                time.sleep(3 * (attempt + 1))
    return None


def _read(zf: zipfile.ZipFile, prefix: str, cols: list[str]) -> pd.DataFrame:
    """Read one '$'-delimited table from the zip (file names / header case vary by year)."""
    names = [n for n in zf.namelist() if Path(n).name.upper().startswith(prefix) and n.lower().endswith(".txt")]
    if not names:
        raise FileNotFoundError(f"{prefix}*.txt not in archive")
    raw = zf.read(names[0])
    if raw.startswith(b"\xef\xbb\xbf"):  # some 2012-2014 files start with a UTF-8 BOM
        raw = raw[3:]

    def norm(c: str) -> str:
        return c.strip().lower()

    # index_col=False: legacy files end every row with an extra '$' (one more field than the header)
    df = pd.read_csv(io.BytesIO(raw), sep="$", dtype=str, encoding="latin-1", quoting=3, index_col=False,
                     on_bad_lines="skip", usecols=lambda c: norm(c) in cols)
    df.columns = [norm(c) for c in df.columns]
    for c in cols:
        if c not in df.columns:
            df[c] = None
    return df


LEGACY_CASE_OFFSET = 10**11  # legacy AERS case numbers live in their own namespace


def parse_quarter(zip_path: Path, tag: str) -> pd.DataFrame:
    """FAERS (2012Q4-) and legacy AERS (2004Q1-2012Q3) quarters -> one row per report.
    Legacy: ISR/CASE instead of primaryid/caseid, GNDR_COD instead of SEX, no PROD_AI (verbatim DRUGNAME only,
    mapped to ingredients later in the labels stage). FAERS before 2014Q3 also lacks PROD_AI."""
    with zipfile.ZipFile(zip_path) as zf:
        demo = _read(zf, "DEMO", ["primaryid", "caseid", "caseversion", "age", "age_cod", "sex", "wt", "wt_cod",
                                  "isr", "case", "gndr_cod"])
        drug = _read(zf, "DRUG", ["primaryid", "prod_ai", "drugname", "isr"])
        reac = _read(zf, "REAC", ["primaryid", "pt", "isr"])
        indi = _read(zf, "INDI", ["primaryid", "indi_pt", "isr"])
    legacy = demo["primaryid"].isna().all() and demo["isr"].notna().any()
    if legacy:
        for df in (demo, drug, reac, indi):
            df["primaryid"] = df["isr"]
        demo["caseid"] = pd.to_numeric(demo["case"], errors="coerce") + LEGACY_CASE_OFFSET
        demo["caseversion"] = pd.to_numeric(demo["isr"], errors="coerce")  # later ISR = later version
        demo["sex"] = demo["gndr_cod"]
    demo["sex"] = demo["sex"].where(demo["sex"].notna(), demo["gndr_cod"])  # 2012Q4-2014Q2 FAERS: gndr_cod

    # --- drugs: active ingredients (combination products '\'-separated); verbatim name when PROD_AI is absent
    ai_col = drug["prod_ai"].where(drug["prod_ai"].notna() & (drug["prod_ai"].str.strip() != ""), "@" + drug["drugname"].fillna(""))
    drug = drug.assign(prod_ai=ai_col).dropna(subset=["primaryid"])
    drug = drug[drug["prod_ai"].str.len() > 1]
    ai = drug["prod_ai"].str.lower().str.split("\\", regex=False)
    d = pd.DataFrame({"primaryid": drug["primaryid"].values, "ai": ai.values}).explode("ai")
    d["ai"] = d["ai"].str.strip()
    d = d[d["ai"].str.len() > 1].drop_duplicates()
    ingredients = d.groupby("primaryid", sort=False)["ai"].agg(list)

    # --- reactions: keep only modeled events (with MedDRA renames folded in)
    reac = reac.dropna(subset=["primaryid", "pt"])
    ev = reac["pt"].str.lower().str.strip().map(_EVENT_MAP)
    r = pd.DataFrame({"primaryid": reac["primaryid"].values, "ev": ev.values}).dropna().drop_duplicates()
    events = r.groupby("primaryid", sort=False)["ev"].agg(list)

    # --- indications -> comorbidity flags
    indi = indi.dropna(subset=["primaryid", "indi_pt"])
    t = indi["indi_pt"].str.lower()
    flags = pd.DataFrame({"primaryid": indi["primaryid"].values})
    for k, pat in COMORBIDITY_PATTERNS.items():
        flags[k] = t.str.contains(pat, regex=True).values
    flags = flags.groupby("primaryid", sort=False).any()

    # --- demographics
    demo = demo.dropna(subset=["primaryid"]).drop_duplicates("primaryid")
    age = pd.to_numeric(demo["age"], errors="coerce") * demo["age_cod"].str.upper().map(_AGE_TO_YEARS)
    age = age.where((age >= 0) & (age <= 120))
    wt = pd.to_numeric(demo["wt"], errors="coerce") * demo["wt_cod"].str.upper().map(_WT_TO_KG)
    wt = wt.where((wt >= 1) & (wt <= 400))
    sex = demo["sex"].str.upper().where(demo["sex"].str.upper().isin(["F", "M"]), "")
    out = pd.DataFrame({
        "primaryid": demo["primaryid"].values,
        "caseid": demo["caseid"].values,
        "caseversion": pd.to_numeric(demo["caseversion"], errors="coerce").fillna(0).astype("int32").values,
        "quarter": tag,
        "age": age.astype("float32").values,
        "sex": sex.fillna("").values,
        "wt": wt.astype("float32").values,
    }).set_index("primaryid")
    out = out.join(ingredients.rename("ingredients"), how="inner")
    out = out.join(events.rename("events"), how="left").join(flags, how="left")
    out["events"] = out["events"].apply(lambda v: v if isinstance(v, list) else [])
    for k in COMORBIDITY_PATTERNS:
        out[k] = out[k].fillna(False).astype(bool)
    return out.reset_index()


def build(start: tuple[int, int] = (2015, 1), end: tuple[int, int] | None = None) -> list[Path]:
    """Download + parse every quarter in [start, end]. Idempotent."""
    if end is None:
        now = time.gmtime()
        end = (now.tm_year, (now.tm_mon - 1) // 3 + 1)
    FAERS_INTERIM.mkdir(parents=True, exist_ok=True)
    done: list[Path] = []
    for y, q in quarters(start, end):
        tag = f"{y}Q{q}"
        out = FAERS_INTERIM / f"{tag}.parquet"
        if out.exists():
            done.append(out)
            continue
        z = download_quarter(y, q)
        if z is None:
            log.info(f"{tag}: not available (not yet published?)")
            continue
        t0 = time.time()
        try:
            df = parse_quarter(z, tag)
        except Exception as e:
            log.error(f"{tag}: parse failed: {e}")
            continue
        df.to_parquet(out)
        done.append(out)
        log.info(f"{tag}: {len(df):,} reports, {int((df['events'].str.len() > 0).sum()):,} with a target event, "
                 f"age known {df['age'].notna().mean():.0%}, comorbidity flag {df[list(COMORBIDITY_PATTERNS)].any(axis=1).mean():.0%}"
                 f" ({time.time() - t0:.0f}s)")
    return done


__all__ = ["build", "download_quarter", "parse_quarter", "quarters"]
