"""Aggregate TWOSIDES (42M rows) -> co-reported pairs + positive (pair, event) signals."""

from __future__ import annotations

import logging

import pandas as pd

from omnipharm.knowledge import TARGET_LOWER
from omnipharm.pipeline.common import CACHE, RAW

log = logging.getLogger("pipeline.twosides")
MIN_PRR, MIN_REPORTS = 2.0, 3


def build() -> None:
    pairs_out, pos_out = CACHE / "ts_pairs.parquet", CACHE / "ts_pos.parquet"
    if pairs_out.exists() and pos_out.exists():
        log.info("cached")
        return
    cols = ["drug_1_rxnorn_id", "drug_1_concept_name", "drug_2_rxnorm_id",  # 'rxnorn' typo is in the file
            "drug_2_concept_name", "condition_concept_name", "A", "PRR"]
    pair_frames, pos_frames = [], []
    reader = pd.read_csv(RAW / "twosides" / "TWOSIDES.csv.gz", usecols=cols, chunksize=2_000_000, low_memory=False)
    for i, ch in enumerate(reader):
        ch.columns = ["d1", "n1", "d2", "n2", "cond", "A", "PRR"]
        for c in ("PRR", "A", "d1", "d2"):
            ch[c] = pd.to_numeric(ch[c], errors="coerce")
        ch = ch.dropna(subset=["d1", "d2", "cond"])
        ch["d1"] = ch["d1"].astype("int64").astype(str)
        ch["d2"] = ch["d2"].astype("int64").astype(str)
        pair_frames.append(ch[["d1", "n1", "d2", "n2"]].drop_duplicates())
        c = ch["cond"].astype(str).str.lower()
        m = c.isin(TARGET_LOWER.keys()) & (ch["PRR"] >= MIN_PRR) & (ch["A"] >= MIN_REPORTS)
        sub = ch.loc[m, ["d1", "d2"]].copy()
        sub["cond"] = c[m].map(TARGET_LOWER)
        pos_frames.append(sub)
        log.info(f"chunk {i + 1}")
    pairs = pd.concat(pair_frames).drop_duplicates(subset=["d1", "d2"])
    pos = pd.concat(pos_frames).drop_duplicates()
    pairs.to_parquet(pairs_out)
    pos.to_parquet(pos_out)
    log.info(f"{len(pairs):,} co-reported pairs, {len(pos):,} positive (pair, event) records")
