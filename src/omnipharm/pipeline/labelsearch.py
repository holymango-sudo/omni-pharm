"""Full-text index of EVERY openFDA drug label (DailyMed SPL content), not one label per drug.

Different manufacturers' labels for the same ingredient word interactions differently, and OTC "Drug Facts"
labels carry their own warnings ("Do not use ... if you are now taking a prescription MAOI", "Ask a doctor
or pharmacist before use if you are taking the blood thinning drug warfarin"). Indexing all ~250k labels
lets the engine find a documented mention whenever ANY label of drug A names drug B.

Output: processed/labels.db (SQLite, FTS5)
  label(id, set_id, brand, rx)          label_key(label_id, key)          sec(text, section, label_id)  [fts5]
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import zipfile

import pandas as pd

from omnipharm.knowledge import BIOLOGIC_LABEL_NAMES, BIOLOGICS, lab_name, normalize_ingredient
from omnipharm.pipeline.common import PROC, RAW

log = logging.getLogger("pipeline.labelsearch")
DB = PROC / "labels.db"
SECTIONS = {  # openFDA field -> section label shown to the user
    "contraindications": "금기",
    "boxed_warning": "박스 경고",
    "drug_interactions": "상호작용",
    "do_not_use": "일반약: 사용 금지",
    "ask_doctor_or_pharmacist": "일반약: 복용 전 상담",
    "warnings_and_cautions": "경고·주의",
    "warnings": "경고·주의",
}


def build() -> None:
    master = pd.read_parquet(PROC / "drug_master.parquet")
    ext = PROC / "ext_ingredients.parquet"
    ext_names = {k.removeprefix("ing:") for k in pd.read_parquet(ext)["key"]} if ext.exists() else set()
    # trained drugs + untrained molecules + peptide/protein drugs, so every resolvable ingredient has its labels
    known = frozenset(master["name"]) | ext_names | set(BIOLOGICS) | set(BIOLOGIC_LABEL_NAMES)

    def key_of(s: str) -> str | None:
        k = normalize_ingredient(s, known)
        if k:
            return BIOLOGIC_LABEL_NAMES.get(k, k)
        return lab_name(s)  # any other substance (antibody, vitamin, herbal ...): label/rule-only ingredient
    tmp = DB.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript("""
        PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
        CREATE TABLE label(id INTEGER PRIMARY KEY, set_id TEXT, brand TEXT, rx INTEGER, n_keys INTEGER);
        CREATE TABLE label_key(label_id INTEGER, key TEXT);
        CREATE VIRTUAL TABLE sec USING fts5(text, section UNINDEXED, label_id UNINDEXED,
                                            tokenize='porter unicode61 remove_diacritics 2');
        -- rowid -> label / section without touching the (large) text: lets the engine filter full-text hits in
        -- memory instead of reading every matching section (30x faster per drug pair)
        CREATE TABLE sec_map(rowid INTEGER PRIMARY KEY, label_id INTEGER, section TEXT);
    """)
    lid, n_sec = 0, 0
    for z in sorted((RAW / "openfda_label").glob("*.zip")):
        with zipfile.ZipFile(z) as zf, zf.open(zf.namelist()[0]) as f:
            data = json.load(f)
        lab_rows, key_rows, sec_rows = [], [], []
        for res in data.get("results", []):
            o = res.get("openfda", {}) or {}
            gens = [p.strip() for g in o.get("generic_name", []) or []
                    for p in re.split(r"\s+and\s+|,|/", g.lower()) if p.strip()]
            subs = [s.lower() for s in o.get("substance_name", []) or []] or gens
            keys = {k for k in (key_of(s) for s in subs + gens) if k}
            if not keys:
                continue
            texts = [(SECTIONS[f_], " ".join(res.get(f_) or [])) for f_ in SECTIONS if res.get(f_)]
            if not texts:
                continue
            lid += 1
            rx = int("PRESCRIPTION" in " ".join(o.get("product_type", []) or []).upper())
            # n_keys > 1 = combination product: its warnings may be about the OTHER ingredient
            lab_rows.append((lid, res.get("set_id", ""), ", ".join(o.get("brand_name", []) or [])[:200], rx,
                             max(len(keys), len(set(subs)))))
            key_rows += [(lid, k) for k in keys]
            for s, t in texts:
                n_sec += 1
                sec_rows.append((n_sec, t[:60000], s, lid))
        con.executemany("INSERT INTO label VALUES (?,?,?,?,?)", lab_rows)
        con.executemany("INSERT INTO label_key VALUES (?,?)", key_rows)
        con.executemany("INSERT INTO sec(rowid, text, section, label_id) VALUES (?,?,?,?)", sec_rows)
        con.executemany("INSERT INTO sec_map VALUES (?,?,?)", [(r, lab, s) for r, _, s, lab in sec_rows])
        con.commit()
        del data
        log.info(f"{z.name}: {lid:,} labels, {n_sec:,} sections indexed")
    con.executescript("CREATE INDEX ix_key ON label_key(key, label_id); INSERT INTO sec(sec) VALUES('optimize');")
    con.close()
    DB.unlink(missing_ok=True)
    tmp.rename(DB)
    log.info(f"labels.db: {lid:,} labels, {n_sec:,} sections, {DB.stat().st_size / 1e9:.1f} GB")
