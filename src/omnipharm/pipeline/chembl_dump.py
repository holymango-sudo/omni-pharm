"""ChEMBL 37 full SQLite dump (EMBL-EBI, CC BY-SA 3.0): drug indications and pharmacology for every molecule we have.

Download: scripts/download_data.py chembl_sqlite (5.4 GB, parallel ranges, checksum-verified); this stage extracts it
(about 30 GB) under data/raw/chembl_sqlite/ the first time.

Outputs (data/processed):
  drug_indications.parquet  name, indications (MeSH / EFO terms of APPROVED indications incl. salt forms, lower case)
                            -> engine: a drug's own-risk line is not shown when the event is what the drug is given
                               for (knowledge.EVENT_INDICATION: cetirizine -> angioedema, warfarin -> stroke)
  pharmacology.parquet      same columns as stage "chembl" (REST API), now for every molecule that ChEMBL has:
                            drug_master + untrained ingredients + the curated recreational structures, matched by
                            InChIKey and, failing that, by its connectivity block (salt / stereo forms). Molecules the
                            dump does not match keep their REST-API rows. The REST version is kept as
                            pharmacology_rest.parquet.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import tarfile
from pathlib import Path

import pandas as pd

from omnipharm.pipeline.chembl import CYP, HERG
from omnipharm.pipeline.common import PROC, RAW

log = logging.getLogger("pipeline.chembl_dump")
DIR = RAW / "chembl_sqlite"
TAR = DIR / "chembl_37_sqlite.tar.gz"


def database() -> Path:
    found = sorted(DIR.rglob("*.db"))
    if found:
        return found[0]
    if not TAR.exists():
        raise FileNotFoundError("ChEMBL dump not downloaded: python scripts/download_data.py chembl_sqlite")
    log.info("extracting the ChEMBL dump (about 30 GB) ...")
    with tarfile.open(TAR, "r:gz") as t:
        members = [m for m in t.getmembers() if m.isfile() and m.name.endswith(".db")]
        t.extractall(DIR, members=members, filter="data")
    return sorted(DIR.rglob("*.db"))[0]


def our_molecules() -> dict[str, str]:
    """InChIKey -> our ingredient key (drug_master name, 'ing:<name>' untrained, 'ing:<key>' recreational)."""
    from omnipharm import recreational
    from omnipharm.chem.featurize import standardize

    m = pd.read_parquet(PROC / "drug_master.parquet")[["name", "std_inchikey"]]
    out = dict(zip(m["std_inchikey"], m["name"], strict=False))
    ext = PROC / "ext_ingredients.parquet"
    if ext.exists():
        e = pd.read_parquet(ext)
        out.update({ik: k for ik, k in zip(e["std_inchikey"], e["key"], strict=False) if ik not in out})
    for k, s in recreational.SUBSTANCES.items():
        if "smiles" in s:
            ik = standardize(s["smiles"]).inchikey
            out.setdefault(ik, f"ing:{k}")
    return {ik: n for ik, n in out.items() if isinstance(ik, str) and ik}


def build() -> pd.DataFrame:
    db = database()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    mol = our_molecules()
    cs = pd.read_sql("SELECT cs.molregno, cs.standard_inchi_key AS ik, mh.parent_molregno AS parent "
                     "FROM compound_structures cs LEFT JOIN molecule_hierarchy mh ON mh.molregno = cs.molregno", con)
    exact = cs[cs["ik"].isin(mol)]
    block = {ik[:14]: n for ik, n in mol.items()}
    matched = set(exact["ik"].map(mol))
    rest = cs[~cs["ik"].isin(mol) & cs["ik"].str[:14].isin(block)]
    rest = rest[~rest["ik"].str[:14].map(block).isin(matched)]  # a connectivity match only for still unmatched names
    pairs = pd.concat([exact.assign(name=exact["ik"].map(mol)), rest.assign(name=rest["ik"].str[:14].map(block))])
    reg2names: dict[int, set[str]] = {}
    for r in pairs.itertuples():
        for reg in {r.molregno, r.parent}:
            if pd.notna(reg):
                reg2names.setdefault(int(reg), set()).add(r.name)
    # salt forms share the parent: their records belong to our molecule too (approved indications are often recorded on
    # the salt - 'WARFARIN SODIUM', 'CETIRIZINE HYDROCHLORIDE')
    by_parent: dict[int, set[str]] = {}
    for r in pairs.itertuples():
        p = int(r.parent) if pd.notna(r.parent) else int(r.molregno)
        by_parent.setdefault(p, set()).add(r.name)
    salts = cs[cs["parent"].isin(by_parent) & ~cs["molregno"].isin(reg2names)]
    for r in salts.itertuples():
        reg2names.setdefault(int(r.molregno), set()).update(by_parent[int(r.parent)])
    log.info(f"{pairs['name'].nunique():,}/{len(set(mol.values())):,} molecules matched to ChEMBL "
             f"({exact['ik'].nunique():,} by InChIKey, {rest['ik'].str[:14].map(block).nunique():,} by connectivity)")
    regs = ",".join(str(x) for x in reg2names)

    def names(reg) -> set[str]:
        return reg2names.get(int(reg), set())

    # APPROVED indications (max_phase_for_ind 4): what the drug is given for. Phase 1-3 rows include every trial the drug
    # appeared in, comparator arms too (warfarin 'multiple sclerosis', 'liver cirrhosis'), so they are not used.
    ind: dict[str, set[str]] = {}
    for r in pd.read_sql(f"SELECT molregno, mesh_heading, efo_term, max_phase_for_ind FROM drug_indication "
                         f"WHERE molregno IN ({regs})", con).itertuples():
        if (r.max_phase_for_ind or 0) >= 4:
            for n in names(r.molregno):
                ind.setdefault(n, set()).update(t.lower() for t in (r.mesh_heading, r.efo_term) if isinstance(t, str))
    di = pd.DataFrame([{"name": n, "indications": sorted(v)} for n, v in sorted(ind.items())])
    di.to_parquet(PROC / "drug_indications.parquet")
    log.info(f"indications for {len(di):,} molecules")

    # CYP inhibition + hERG potency, mechanisms, metabolism - as stage "chembl", from the dump
    tids = {**CYP, "hERG": HERG}
    label_of = {v: k for k, v in tids.items()}
    q = ("SELECT act.molregno, td.chembl_id AS target, MAX(act.pchembl_value) AS p FROM activities act "
         "JOIN assays a ON a.assay_id = act.assay_id JOIN target_dictionary td ON td.tid = a.tid "
         f"WHERE td.chembl_id IN ({','.join(repr(t) for t in tids.values())}) AND act.pchembl_value IS NOT NULL "
         f"AND act.standard_type IN ('IC50','Ki','Kd') AND act.molregno IN ({regs}) GROUP BY act.molregno, td.chembl_id")
    inh: dict[str, dict[str, float]] = {}
    herg: dict[str, float] = {}
    for r in pd.read_sql(q, con).itertuples():
        for n in names(r.molregno):
            lab = label_of[r.target]
            if lab == "hERG":
                herg[n] = max(herg.get(n, 0.0), float(r.p))
            else:
                inh.setdefault(n, {})[lab] = max(inh.get(n, {}).get(lab, 0.0), float(r.p))
    moa: dict[str, list[dict]] = {}
    for r in pd.read_sql("SELECT dm.molregno, dm.mechanism_of_action AS text, dm.action_type AS action, "
                         "td.chembl_id AS target FROM drug_mechanism dm LEFT JOIN target_dictionary td ON td.tid = dm.tid "
                         f"WHERE dm.molregno IN ({regs})", con).itertuples():
        for n in names(r.molregno):
            moa.setdefault(n, []).append({"action": r.action, "target": r.target, "text": r.text})
    sub: dict[str, set[str]] = {}
    for r in pd.read_sql("SELECT cr.molregno, m.enzyme_name FROM metabolism m JOIN compound_records cr "
                         f"ON cr.record_id = m.substrate_record_id WHERE cr.molregno IN ({regs})", con).itertuples():
        enz = str(r.enzyme_name or "").upper().replace(" ", "")
        for lab in CYP:
            if lab in enz:
                for n in names(r.molregno):
                    sub.setdefault(n, set()).add(lab)
    con.close()

    hit = set(pairs["name"])
    dump = pd.DataFrame([{"name": n, "cyp_substrate": sorted(sub.get(n, ())), "cyp_inhibition": json.dumps(inh.get(n, {})),
                          "herg_pchembl": herg.get(n), "moa": json.dumps(moa.get(n, [])[:6], ensure_ascii=False)}
                         for n in sorted(hit)])
    old_f, keep_f = PROC / "pharmacology.parquet", PROC / "pharmacology_rest.parquet"
    if old_f.exists() and not keep_f.exists():
        old_f.replace(keep_f)
    old = pd.read_parquet(keep_f) if keep_f.exists() else dump.iloc[:0]
    # a mechanism the REST extraction had but the dump records elsewhere (radiolabelled tracers): keep it
    rest_moa = {r.name: r.moa for r in old.itertuples() if json.loads(r.moa)}
    dump["moa"] = [m if json.loads(m) else rest_moa.get(n, m) for n, m in zip(dump["name"], dump["moa"], strict=True)]
    df = pd.concat([dump, old[~old["name"].isin(hit)]], ignore_index=True)
    df.to_parquet(old_f)
    log.info(f"pharmacology: {len(df):,} molecules (dump {len(dump):,}, REST-only {len(df) - len(dump):,}) | "
             f"CYP inhibition {len(inh):,}, hERG {len(herg):,}, mechanism {len(moa):,}, CYP substrate {len(sub):,}")
    return df
