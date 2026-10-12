"""OFFSIDES (Tatonetti lab, nSIDES): single-drug adverse-event signals from FAERS, each drug's reports compared with
propensity-score matched reports of other drugs (which reduces confounding by indication).

Used as an independent check of the patient model's 'a drug's own risk' lines: the result shows "'간부전'은 아세트아미노펜
자체의 위험" only when OFFSIDES also finds that drug-event signal by the Evans criteria (PRR >= 2, at least 3 reports,
lower 95% bound of the PRR above 1) - '경련' for acetaminophen (PRR 1.5) is not shown.

Input : data/raw/offsides/OFFSIDES.csv.gz (download: scripts/download_data.py offsides)
Output: processed/offsides.parquet  drug (drug_master name), condition (one of the 52 modelled events), prr, prr_low, a
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from omnipharm.knowledge import PT_SYNONYMS, TARGET_LOWER
from omnipharm.pipeline.common import PROC, RAW

log = logging.getLogger("pipeline.offsides")
MIN_REPORTS = 3
EVENT = {**TARGET_LOWER, **{k.lower(): v for k, v in PT_SYNONYMS.items()}}  # same MedDRA mapping as the FAERS stage


def build() -> pd.DataFrame:
    master = pd.read_parquet(PROC / "drug_master.parquet")
    by_rxcui = dict(zip(master["rxcui"].astype(str), master["name"], strict=True))
    cm = pd.read_parquet(PROC / "condition_mapping.parquet")
    modelled = set(cm["condition"])
    cols = ["drug_rxnorn_id", "condition_concept_name", "A", "PRR", "PRR_error"]  # 'rxnorn' typo is in the file
    out = []
    for ch in pd.read_csv(RAW / "offsides" / "OFFSIDES.csv.gz", usecols=cols, chunksize=2_000_000, low_memory=False):
        ch["cond"] = ch["condition_concept_name"].astype(str).str.lower().map(EVENT)
        ch["drug"] = pd.to_numeric(ch["drug_rxnorn_id"], errors="coerce").astype("Int64").astype(str).map(by_rxcui)
        for c in ("A", "PRR", "PRR_error"):
            ch[c] = pd.to_numeric(ch[c], errors="coerce")
        ch = ch.dropna(subset=["cond", "drug", "PRR", "PRR_error"])
        ch = ch[ch["cond"].isin(modelled) & (ch["A"] >= MIN_REPORTS) & (ch["PRR"] > 0)]
        low = np.exp(np.log(ch["PRR"]) - 1.96 * ch["PRR_error"])  # PRR_error = standard error of ln(PRR)
        sig = ch[low > 1].assign(prr_low=low[low > 1])
        out.append(sig[["drug", "cond", "PRR", "prr_low", "A"]])
    df = pd.concat(out).rename(columns={"cond": "condition", "PRR": "prr", "A": "a"})
    # several MedDRA terms can map to one modelled event: keep the strongest signal
    df = df.sort_values("prr_low", ascending=False).drop_duplicates(["drug", "condition"]).reset_index(drop=True)
    # Evans et al. 2001 signal criteria (PRR >= 2, >= 3 reports; with the lower bound > 1): what the engine accepts
    df["evans"] = (df["prr"] >= 2) & (df["a"] >= MIN_REPORTS)
    df.to_parquet(PROC / "offsides.parquet")
    log.info(f"OFFSIDES: {len(df):,} significant (drug, event) signals for {df['drug'].nunique():,} of {len(master):,} "
             f"drugs, {df['condition'].nunique()} of {len(cm)} events; meeting the Evans criteria {int(df['evans'].sum()):,}")
    return df
