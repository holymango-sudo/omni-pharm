"""Resolve real chemical structures (PubChem) for every drug that appears in TWOSIDES."""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

from omnipharm.chem.featurize import MoleculeError, standardize
from omnipharm.pipeline.common import CACHE, PROC

log = logging.getLogger("pipeline.structures")
_lock = threading.Lock()
_last = [0.0]
MIN_INTERVAL = 0.22  # PubChem policy: <= 5 requests / second


def _throttle() -> None:
    with _lock:
        wait = _last[0] + MIN_INTERVAL - time.time()
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()


def pubchem(name: str) -> dict | None:
    url = ("https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
           f"{urllib.parse.quote(name)}/property/SMILES,IsomericSMILES,CanonicalSMILES,InChIKey/JSON")
    for attempt in range(4):
        _throttle()
        try:
            r = requests.get(url, timeout=20)
            if r.status_code in (400, 404):
                return None
            if r.status_code in (429, 503):
                time.sleep(2 + attempt * 2)
                continue
            r.raise_for_status()
            p = r.json()["PropertyTable"]["Properties"][0]
            smi = p.get("IsomericSMILES") or p.get("SMILES") or p.get("CanonicalSMILES")
            return {"smiles": smi, "inchikey": p.get("InChIKey"), "cid": p.get("CID")} if smi else None
        except Exception:
            time.sleep(1 + attempt)
    return {"error": True}  # distinguish "network failed" (retry next run) from "not found"


def build(max_drugs: int | None = None) -> pd.DataFrame:
    pairs = pd.read_parquet(CACHE / "ts_pairs.parquet")
    a = pairs[["d1", "n1"]].rename(columns={"d1": "rxcui", "n1": "name"})
    b = pairs[["d2", "n2"]].rename(columns={"d2": "rxcui", "n2": "name"})
    allr = pd.concat([a, b])
    allr["rxcui"] = allr["rxcui"].astype(str)
    freq = allr.groupby("rxcui").size().sort_values(ascending=False)
    names = allr.drop_duplicates("rxcui").set_index("rxcui")["name"].astype(str).str.lower()
    todo_all = list(freq.index[:max_drugs] if max_drugs else freq.index)

    cache_f = CACHE / "pubchem.json"
    cache: dict = json.loads(cache_f.read_text(encoding="utf-8")) if cache_f.exists() else {}
    todo = [r for r in todo_all if r not in cache or (cache[r] or {}).get("error")]
    log.info(f"{len(todo_all)} drugs in TWOSIDES; resolving {len(todo)} via PubChem ({len(cache)} cached)")

    def work(rx: str):
        return rx, pubchem(names[rx])

    with ThreadPoolExecutor(4) as ex:
        for k, (rx, res) in enumerate(ex.map(work, todo), 1):
            cache[rx] = res
            if k % 100 == 0 or k == len(todo):
                cache_f.write_text(json.dumps(cache), encoding="utf-8")
                log.info(f"PubChem {k}/{len(todo)}")
    cache_f.write_text(json.dumps(cache), encoding="utf-8")

    rows, reasons = [], {"not_found": 0, "network": 0, "invalid_or_too_large": 0}
    for rx in todo_all:
        info = cache.get(rx)
        if not info:
            reasons["not_found"] += 1
            continue
        if info.get("error"):
            reasons["network"] += 1
            continue
        try:
            std = standardize(info["smiles"])
        except MoleculeError:
            reasons["invalid_or_too_large"] += 1
            continue
        rows.append({"rxcui": rx, "name": names[rx], "std_smiles": std.canonical_smiles,
                     "std_inchikey": std.inchikey, "pubchem_cid": info.get("cid"),
                     "n_heavy": std.mol.GetNumHeavyAtoms(), "n_pairs": int(freq[rx])})
    df = pd.DataFrame(rows).drop_duplicates("name").reset_index(drop=True)
    df.to_parquet(PROC / "drug_master.parquet")
    log.info(f"drug_master: {len(df)} drugs with structures; skipped {reasons}")
    return df
