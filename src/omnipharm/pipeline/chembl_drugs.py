"""Every approved drug in ChEMBL (max_phase 4): names, trade names, ATC classes and structures.

Gives the engine
  * world-wide trade names / INN / other synonyms -> ingredient (brands from any country)
  * the WHO ATC classification for automatic same-class duplication checks (two statins, two SSRIs ...)
  * structures for approved small molecules not yet in the ingredient DB (-> "ing:" untrained molecules)
  * approved biologics (antibodies, proteins ...) as label/rule-only ingredients ("lab:")
Uses the ChEMBL REST API (the bulk dump downloads too slowly here); pages cached in data/raw/chembl/api.
Output: processed/chembl_drugs.json  {"synonyms": {name: key}, "atc": {key: [ATC level 5]}, "atc4": {code: name}}
Licence: ChEMBL CC BY-SA 3.0 (attribution).
"""

from __future__ import annotations

import collections
import json
import logging
import re

import pandas as pd

from omnipharm.chem.featurize import MoleculeError, standardize
from omnipharm.knowledge import bio_key, lab_name
from omnipharm.pipeline.chembl import Client
from omnipharm.pipeline.common import PROC
from omnipharm.pipeline.ingredients import EXT, MAX_HEAVY, PREFIX, Mapper

log = logging.getLogger("pipeline.chembl_drugs")
FIELDS = "molecule_chembl_id,pref_name,molecule_type,molecule_structures,molecule_synonyms,atc_classifications"
SYN_TYPES = {"TRADE_NAME", "INN", "USAN", "BAN", "JAN", "OTHER", "FDA", "USP", "BNF", "MERCK_INDEX", "INN_SPANISH"}


def build() -> dict:
    api = Client()
    mols = api.all("molecule", {"max_phase": 4, "only": FIELDS}, "molecules")
    atc = api.all("atc_class", {}, "atc")
    log.info(f"ChEMBL approved molecules: {len(mols):,}; ATC classes: {len(atc):,}")
    mapper = Mapper()
    n_new = 0

    def key_of(m: dict) -> str | None:
        nonlocal n_new
        name = (m.get("pref_name") or "").strip().lower()
        st = m.get("molecule_structures") or {}
        smi, ik = st.get("canonical_smiles"), st.get("standard_inchi_key")
        if ik and ik in mapper.by_key:
            return mapper.by_key[ik]
        if ik and ik in mapper.ext_by_key:
            return mapper.ext_by_key[ik]
        if name:
            k = mapper.by_name(name)
            if k:
                return k
        if smi and name and (m.get("molecule_type") == "Small molecule"):
            try:
                std = standardize(smi)
            except MoleculeError:
                std = None
            if std is not None and 3 <= std.mol.GetNumHeavyAtoms() <= MAX_HEAVY:
                if std.inchikey in mapper.by_key:
                    return mapper.by_key[std.inchikey]
                if std.inchikey in mapper.ext_by_key:
                    return mapper.ext_by_key[std.inchikey]
                k = PREFIX + name
                mapper.ext[k] = {"key": k, "name": name, "std_smiles": std.canonical_smiles,
                                 "std_inchikey": std.inchikey}
                mapper.ext_by_key[std.inchikey] = k
                n_new += 1
                return k
        if name:
            b = bio_key(name)
            return f"bio:{b}" if b else (f"lab:{lab_name(name)}" if lab_name(name) else None)
        return None

    syn: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    atc_of: dict[str, set[str]] = collections.defaultdict(set)
    types = collections.Counter()
    for m in mols:
        k = key_of(m)
        if not k:
            continue
        types[m.get("molecule_type") or "?"] += 1
        names = {(m.get("pref_name") or "").lower()}
        for s in m.get("molecule_synonyms") or []:
            if s.get("syn_type") in SYN_TYPES:
                names.add((s.get("molecule_synonym") or "").lower())
        for n in names:
            n = " ".join(n.split())
            # skip research codes ("ABT-199") and very short names; they collide with other words
            if len(n) >= 4 and not re.fullmatch(r"[a-z]{1,4}[- ]?\d+[a-z]?", n):
                syn[n][k] += 1
        for code in m.get("atc_classifications") or []:
            atc_of[k].add(code)
    synonyms = {n: c.most_common(1)[0][0] for n, c in syn.items() if len(c) == 1}  # ambiguous names dropped
    atc4 = {a["level4"]: a.get("level4_description", "") for a in atc if a.get("level4")}
    out = {"synonyms": synonyms, "atc": {k: sorted(v) for k, v in atc_of.items()}, "atc4": atc4,
           "meta": {"approved_molecules": len(mols), "by_type": dict(types), "new_untrained_molecules": n_new}}
    mapper.save()
    (PROC / "chembl_drugs.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    log.info(f"synonyms {len(synonyms):,} | ATC-coded ingredients {len(atc_of):,} | new untrained molecules {n_new:,} "
             f"| types {dict(types)} | ext_ingredients now {len(pd.read_parquet(EXT)):,}")
    return out
