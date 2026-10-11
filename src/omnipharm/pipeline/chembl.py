"""ChEMBL (EMBL-EBI, CC BY-SA 3.0) pharmacology for mechanism-based interaction screening.

The full SQLite dump (~6 GB compressed / ~30 GB) downloads at only ~20 KB/s from EBI to this site, so the
data is extracted through the ChEMBL REST API for the drugs we actually use (drug_master + untrained
ingredients, matched by InChIKey). Raw API pages are cached under data/raw/chembl/api.
Per drug:
  cyp_substrate   CYP enzymes that metabolise the drug (ChEMBL metabolism records, literature-curated)
  cyp_inhibition  best in-vitro inhibition potency per CYP (pChEMBL = -log10 IC50/Ki in M; 6 = 1 uM)
  herg_pchembl    hERG (KCNH2) blockade potency -> QT risk
  moa             mechanism-of-action targets (pharmacodynamic overlap)
Output: processed/pharmacology.parquet
"""

from __future__ import annotations

import hashlib
import json
import logging
import time

import httpx
import pandas as pd

from omnipharm.pipeline.common import PROC, RAW

log = logging.getLogger("pipeline.chembl")
API = "https://www.ebi.ac.uk/chembl/api/data/"
CACHE = RAW / "chembl" / "api"
CYP = {"CYP3A4": "CHEMBL340", "CYP2D6": "CHEMBL289", "CYP2C9": "CHEMBL3397", "CYP2C19": "CHEMBL3622",
       "CYP1A2": "CHEMBL3356"}
HERG = "CHEMBL240"


class Client:
    def __init__(self) -> None:
        CACHE.mkdir(parents=True, exist_ok=True)
        self.c = httpx.Client(timeout=120, headers={"User-Agent": "OMNI-PHARM/2.2 (research)"})
        self.n = 0

    def get(self, resource: str, params: dict) -> dict:
        key = hashlib.sha1((resource + json.dumps(params, sort_keys=True)).encode()).hexdigest()[:20]
        f = CACHE / f"{resource}_{key}.json"
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
        for attempt in range(6):
            try:
                r = self.c.get(API + resource, params={**params, "format": "json"})
                r.raise_for_status()
                j = r.json()
                f.write_text(json.dumps(j), encoding="utf-8")
                self.n += 1
                return j
            except Exception as e:  # EBI rate limits / timeouts
                log.warning(f"{resource} attempt {attempt + 1}: {e}")
                time.sleep(5 * (attempt + 1))
        raise RuntimeError(f"ChEMBL API failed: {resource} {params}")

    def all(self, resource: str, params: dict, key: str) -> list[dict]:
        out, offset = [], 0
        while True:
            j = self.get(resource, {**params, "limit": 1000, "offset": offset})
            out += j.get(key, [])
            total = j["page_meta"]["total_count"]
            offset += 1000
            if offset >= total:
                return out


def _chunks(xs: list, n: int):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def build() -> pd.DataFrame:
    api = Client()
    keys = pd.read_parquet(PROC / "drug_master.parquet")[["name", "std_inchikey"]]
    ext = PROC / "ext_ingredients.parquet"
    if ext.exists():
        keys = pd.concat([keys, pd.read_parquet(ext)[["key", "std_inchikey"]].rename(columns={"key": "name"})])
    ik2name = dict(zip(keys["std_inchikey"], keys["name"], strict=False))

    # 1) InChIKey -> ChEMBL molecule (+ parent: activities are usually recorded on the parent compound)
    chem: dict[str, set[str]] = {}
    for batch in _chunks(sorted(ik2name), 40):
        j = api.get("molecule", {"molecule_structures__standard_inchi_key__in": ",".join(batch), "limit": 1000,
                                 "only": "molecule_chembl_id,molecule_structures,molecule_hierarchy"})
        for m in j.get("molecules", []):
            ik = (m.get("molecule_structures") or {}).get("standard_inchi_key")
            if ik in ik2name:
                ids = chem.setdefault(ik2name[ik], set())
                ids.add(m["molecule_chembl_id"])
                par = (m.get("molecule_hierarchy") or {}).get("parent_chembl_id")
                if par:
                    ids.add(par)
    id2names: dict[str, set[str]] = {}
    for n, ids in chem.items():
        for i in ids:
            id2names.setdefault(i, set()).add(n)
    log.info(f"{len(chem):,}/{len(ik2name):,} drugs matched to ChEMBL ({api.n} API calls)")

    # 2) CYP inhibition + hERG: all potency records for these 6 targets (then keep our molecules)
    inh: dict[str, dict[str, float]] = {}
    herg: dict[str, float] = {}
    for label, tid in [*CYP.items(), ("hERG", HERG)]:
        rows = api.all("activity", {"target_chembl_id": tid, "pchembl_value__isnull": "false",
                                    "standard_type__in": "IC50,Ki,Kd",
                                    "only": "molecule_chembl_id,pchembl_value,standard_relation"}, "activities")
        n_hit = 0
        for a in rows:
            for name in id2names.get(a["molecule_chembl_id"], ()):
                p = float(a["pchembl_value"])
                if label == "hERG":
                    herg[name] = max(herg.get(name, 0), p)
                else:
                    d = inh.setdefault(name, {})
                    d[label] = max(d.get(label, 0), p)
                n_hit += 1
        log.info(f"{label}: {len(rows):,} potency records, {n_hit:,} for our drugs")

    # 3) mechanism of action and 4) metabolism (substrate -> enzyme), batched by molecule id
    ids = sorted(id2names)
    moa: dict[str, list[dict]] = {}
    sub: dict[str, set[str]] = {}
    for batch in _chunks(ids, 50):
        for m in api.all("mechanism", {"molecule_chembl_id__in": ",".join(batch)}, "mechanisms"):
            for name in id2names.get(m["molecule_chembl_id"], ()) | id2names.get(m.get("parent_molecule_chembl_id"), set()):
                moa.setdefault(name, []).append({"action": m.get("action_type"), "target": m.get("target_chembl_id"),
                                                 "text": m.get("mechanism_of_action")})
        for m in api.all("metabolism", {"substrate_chembl_id__in": ",".join(batch)}, "metabolisms"):
            enz = str(m.get("enzyme_name") or "").upper().replace(" ", "")
            for label in CYP:
                if label in enz:
                    for name in id2names.get(m.get("substrate_chembl_id"), ()):
                        sub.setdefault(name, set()).add(label)
    log.info(f"mechanism for {len(moa):,} drugs, metabolism (CYP substrate) for {len(sub):,} drugs")

    rows = [{"name": n, "cyp_substrate": sorted(sub.get(n, ())), "cyp_inhibition": json.dumps(inh.get(n, {})),
             "herg_pchembl": herg.get(n), "moa": json.dumps(moa.get(n, [])[:6], ensure_ascii=False)}
            for n in sorted(chem)]
    df = pd.DataFrame(rows)
    df.to_parquet(PROC / "pharmacology.parquet")
    log.info(f"pharmacology: {len(df):,} drugs ({api.n} API calls)")
    return df
