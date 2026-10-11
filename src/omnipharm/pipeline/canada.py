"""Health Canada - Canada Vigilance adverse reaction database extract -> FAERS-shaped report tables.

A second national pharmacovigilance source (about 1 million Canadian reports since 1965) with the same
fields the patient model uses: age, sex, weight, indications, all drugs taken, MedDRA reactions.
Source: https://www.canada.ca/en/health-canada/services/drugs-health-products/medeffect-canada/
        adverse-reaction-database/canada-vigilance-online-database-data-extract.html
Licence: Open Government Licence - Canada.

Output: data/interim/faers/CA_<YYYY>Q<n>.parquet (same columns as faers.parse_quarter). The labels stage
marks these rows as source 1 so evaluation can keep using FAERS-only test sets plus a Canadian external set.
"""

from __future__ import annotations

import logging
import zipfile

import pandas as pd

from omnipharm.knowledge import COMORBIDITY_PATTERNS
from omnipharm.pipeline.common import INTERIM, RAW
from omnipharm.pipeline.faers import _EVENT_MAP

log = logging.getLogger("pipeline.canada")
URL = ("https://www.canada.ca/content/dam/hc-sc/migration/hc-sc/dhp-mps/alt_formats/zip/medeff/databasdon/"
       "extract_extrait.zip")
ZIP = RAW / "canada_vigilance" / "extract_extrait.zip"
OUT = INTERIM / "faers"
CASE_OFFSET = 2 * 10**11  # own case-id namespace (FAERS and legacy AERS use < 2e11)

REPORT_COLS = ["report_id", "report_no", "version_no", "datreceived", "datintreceived", "mah_no",
               "report_type_code", "report_type_eng", "report_type_fr", "gender_code", "gender_eng", "gender_fr",
               "age", "age_y", "age_unit_eng", "age_unit_fr", "outcome_code", "outcome_eng", "outcome_fr",
               "weight", "weight_unit_eng", "weight_unit_fr", "height", "height_unit_eng", "height_unit_fr",
               "seriousness_code", "seriousness_eng", "seriousness_fr", "death", "disability", "congenital_anomaly",
               "life_threatening", "hosp_required", "other_medically_imp_cond", "reporter_type_eng",
               "reporter_type_fr", "source_code", "source_eng", "source_fr", "e2b_imp_safetyreport_id",
               "authority_numb", "company_numb"]
DRUG_COLS = ["report_drug_id", "report_id", "drug_product_id", "drugname", "druginvolv_eng", "druginvolv_fr",
             "routeadmin_eng", "routeadmin_fr", "unit_dose_qty", "dose_unit_eng", "dose_unit_fr", "frequency",
             "freq_time", "frequency_time_eng", "frequency_time_fr", "freq_time_unit_eng", "freq_time_unit_fr",
             "therapy_duration", "therapy_duration_unit_eng", "therapy_duration_unit_fr", "dosageform_eng",
             "dosageform_fr"]
ING_COLS = ["drug_product_ingredient_id", "drug_product_id", "drugname", "active_ingredient_id",
            "active_ingredient_name"]
REAC_COLS = ["reaction_id", "report_id", "duration", "duration_unit_eng", "duration_unit_fr", "pt_name_eng",
             "pt_name_fr", "soc_name_eng", "soc_name_fr", "meddra_version"]
INDI_COLS = ["report_drug_id", "report_id", "drug_product_id", "drugname", "indication_name_eng",
             "indication_name_fr"]
WT_TO_KG = {"kilogram": 1.0, "pound": 0.45359237, "gram": 0.001}


def _read(zf: zipfile.ZipFile, name: str, cols: list[str], use: list[str]) -> pd.DataFrame:
    with zf.open(name) as f:
        df = pd.read_csv(f, sep="$", quotechar='"', header=None, names=cols, usecols=use, dtype=str,
                         encoding="latin-1", on_bad_lines="skip", engine="c", low_memory=False)
    return df


def download() -> None:
    if ZIP.exists() and ZIP.stat().st_size > 300e6:
        return
    import requests

    ZIP.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(URL, stream=True, timeout=60) as r:
        r.raise_for_status()
        tmp = ZIP.with_suffix(".part")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
        tmp.replace(ZIP)


def build() -> None:
    download()
    OUT.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ZIP) as zf:
        names = {n.lower().rsplit("/", 1)[-1]: n for n in zf.namelist()}
        rep = _read(zf, names["reports.txt"], REPORT_COLS,
                    ["report_id", "version_no", "datintreceived", "datreceived", "gender_eng", "age_y", "weight",
                     "weight_unit_eng"])
        drug = _read(zf, names["report_drug.txt"], DRUG_COLS, ["report_id", "drug_product_id", "drugname"])
        ing = _read(zf, names["drug_product_ingredients.txt"], ING_COLS, ["drug_product_id", "active_ingredient_name"])
        reac = _read(zf, names["reactions.txt"], REAC_COLS, ["report_id", "pt_name_eng"])
        indi = _read(zf, names["report_drug_indication.txt"], INDI_COLS, ["report_id", "indication_name_eng"])
    log.info(f"Canada Vigilance: {len(rep):,} reports, {len(drug):,} drug rows, {len(reac):,} reactions")

    # drugs: product -> active ingredients; products without an ingredient row keep the verbatim brand name
    ing["ai"] = ing["active_ingredient_name"].str.lower().str.strip()
    prod_ai = ing.dropna(subset=["ai"]).groupby("drug_product_id")["ai"].agg(lambda s: sorted(set(s)))
    drug = drug.dropna(subset=["report_id"])
    ai = drug["drug_product_id"].map(prod_ai)
    verb = "@" + drug["drugname"].fillna("").str.lower()
    drug = drug.assign(ai=[a if isinstance(a, list) else [v] for a, v in zip(ai, verb, strict=True)])
    d = drug[["report_id", "ai"]].explode("ai")
    d = d[d["ai"].str.len() > 1].drop_duplicates()
    ingredients = d.groupby("report_id", sort=False)["ai"].agg(list)

    ev = reac["pt_name_eng"].str.lower().str.strip().map(_EVENT_MAP)
    r = pd.DataFrame({"report_id": reac["report_id"].values, "ev": ev.values}).dropna().drop_duplicates()
    events = r.groupby("report_id", sort=False)["ev"].agg(list)

    indi = indi.dropna()
    t = indi["indication_name_eng"].str.lower()
    flags = pd.DataFrame({"report_id": indi["report_id"].values})
    for k, pat in COMORBIDITY_PATTERNS.items():
        flags[k] = t.str.contains(pat, regex=True).values
    flags = flags.groupby("report_id", sort=False).any()

    rep = rep.dropna(subset=["report_id"]).drop_duplicates("report_id")
    date = pd.to_datetime(rep["datintreceived"].fillna(rep["datreceived"]), format="%d-%b-%y", errors="coerce")
    date = date.where(date.dt.year <= pd.Timestamp.now().year, date - pd.DateOffset(years=100))  # 2-digit years
    age = pd.to_numeric(rep["age_y"], errors="coerce")
    age = age.where((age >= 0) & (age <= 120))
    wt = pd.to_numeric(rep["weight"], errors="coerce") * rep["weight_unit_eng"].str.lower().map(WT_TO_KG)
    wt = wt.where((wt >= 1) & (wt <= 400))
    sex = rep["gender_eng"].str.lower().map({"female": "F", "male": "M"}).fillna("")
    out = pd.DataFrame({
        "primaryid": rep["report_id"].values,
        "caseid": pd.to_numeric(rep["report_id"], errors="coerce").values + CASE_OFFSET,
        "caseversion": pd.to_numeric(rep["version_no"], errors="coerce").fillna(0).astype("int32").values,
        "quarter": (date.dt.year.astype("Int64").astype(str) + "Q" + date.dt.quarter.astype("Int64").astype(str)).values,
        "age": age.astype("float32").values, "sex": sex.values, "wt": wt.astype("float32").values,
    }).set_index("primaryid")
    out = out[out["quarter"].str.match(r"^\d{4}Q[1-4]$", na=False)]
    out = out.join(ingredients.rename("ingredients"), how="inner")
    out = out.join(events.rename("events"), how="left").join(flags, how="left")
    out["events"] = out["events"].apply(lambda v: v if isinstance(v, list) else [])
    for k in COMORBIDITY_PATTERNS:
        out[k] = out[k].fillna(False).astype(bool)
    out = out.reset_index()
    for old in OUT.glob("CA_*.parquet"):
        old.unlink()
    for tag, g in out.groupby("quarter"):
        g.reset_index(drop=True).to_parquet(OUT / f"CA_{tag}.parquet")
    log.info(f"Canada Vigilance -> {len(out):,} reports in {out['quarter'].nunique()} quarters "
             f"({out['quarter'].min()}..{out['quarter'].max()}), "
             f"{int((out['events'].str.len() > 0).sum()):,} with a target event")
