"""Extend drug_master with modern small molecules that are frequent in FAERS but absent from TWOSIDES.

TWOSIDES covers drugs reported up to ~2014, so the structure DB built from it misses most drugs approved
since (e.g. nirmatrelvir, sacubitril, edoxaban, upadacitinib, osimertinib). FAERS (2015 -> latest) names
them, PubChem gives their structure. Biologics / peptides / mixtures are excluded by name rules and by a
heavy-atom limit; they have no meaningful small-molecule graph.
These drugs have no TWOSIDES pairs, so they train the patient model and are "unseen" for the pair model.
"""

from __future__ import annotations

import collections
import json
import logging
import re

import pandas as pd

from omnipharm.chem.featurize import MoleculeError, standardize
from omnipharm.knowledge import SALT_WORDS, normalize_ingredient
from omnipharm.pipeline.common import CACHE, INTERIM, PROC
from omnipharm.pipeline.structures import pubchem

log = logging.getLogger("pipeline.extend")
MIN_REPORTS = 1500  # reports mentioning the ingredient (2015 -> latest)
MAX_HEAVY = 100  # small molecules only (peptides like semaglutide are ~300)
EXCLUDE = re.compile(
    r"mab\b|mab-|cept\b|ase\b|insulin|vaccine|globulin|vitamin|herbal|supplement|mineral|unspecified|oil\b|"
    r"extract|toxin|factor|interferon|somatropin|tide\b|pegol|filgrastim|poetin|alfa|beta\b|cells|dextrose|"
    r"water|sodium chloride|electrolyte|device|placebo|nos\b|\bpancrelipase|heparin|immune|serum|allergen|"
    r"polyethylene glycol|corticotropin|botulinum|-[a-z]{4}$"
)


def _core(s: str) -> str:
    words = [w for w in s.lower().replace(",", " ").split() if w not in SALT_WORDS]
    return " ".join(words)


def build() -> pd.DataFrame:
    master = pd.read_parquet(PROC / "drug_master.parquet")
    master = master[~master["rxcui"].astype(str).str.startswith("x:")]  # idempotent: drop previous extension
    known = frozenset(master["name"])
    cnt: collections.Counter[str] = collections.Counter()
    for f in sorted((INTERIM / "faers").glob("*.parquet")):
        for ings in pd.read_parquet(f, columns=["ingredients"])["ingredients"]:
            for s in set(ings):
                cnt[s] += 1
    agg: collections.Counter[str] = collections.Counter()
    for s, c in cnt.items():
        if normalize_ingredient(s, known) is None:
            core = _core(s)
            if core and not EXCLUDE.search(core) and not re.search(r"\d", core) and len(core.split()) <= 2:
                agg[core] += c
    todo = [n for n, c in agg.most_common() if c >= MIN_REPORTS]
    log.info(f"{len(todo)} unmapped FAERS ingredients with >= {MIN_REPORTS} reports -> PubChem")

    cache_f = CACHE / "pubchem_extend.json"
    cache = json.loads(cache_f.read_text(encoding="utf-8")) if cache_f.exists() else {}
    for k, n in enumerate(todo, 1):
        if n not in cache or (cache[n] or {}).get("error"):
            cache[n] = pubchem(n)
        if k % 50 == 0:
            cache_f.write_text(json.dumps(cache), encoding="utf-8")
            log.info(f"PubChem {k}/{len(todo)}")
    cache_f.write_text(json.dumps(cache), encoding="utf-8")

    rows, have_key = [], set(master["std_inchikey"])
    for n in todo:
        info = cache.get(n)
        if not info or info.get("error"):
            continue
        try:
            std = standardize(info["smiles"])
        except MoleculeError:
            continue
        if std.mol.GetNumHeavyAtoms() > MAX_HEAVY or std.mol.GetNumHeavyAtoms() < 5 or std.inchikey in have_key:
            continue
        have_key.add(std.inchikey)
        rows.append({"rxcui": f"x:{n}", "name": n, "std_smiles": std.canonical_smiles, "std_inchikey": std.inchikey,
                     "pubchem_cid": info.get("cid"), "n_heavy": std.mol.GetNumHeavyAtoms(), "n_pairs": 0})
    ext = pd.DataFrame(rows)
    out = pd.concat([master, ext], ignore_index=True)
    out["rxcui"] = out["rxcui"].astype(str)
    out.to_parquet(PROC / "drug_master.parquet")
    log.info(f"drug_master: {len(master)} TWOSIDES drugs + {len(ext)} modern FAERS drugs = {len(out)}; "
             f"added e.g. {', '.join(ext['name'].head(25))}")
    return ext
