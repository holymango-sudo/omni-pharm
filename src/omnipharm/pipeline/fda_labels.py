"""Index openFDA drug labels: interaction / boxed-warning / contraindication text per drug.

One label per drug name in drug_master. Preference order when several labels match:
  single-ingredient > combination, prescription > OTC, longest interactions section.
"""

from __future__ import annotations

import json
import logging
import re
import zipfile

import pandas as pd

from omnipharm.knowledge import normalize_ingredient
from omnipharm.pipeline.common import PROC, RAW

log = logging.getLogger("pipeline.fda_labels")


def _text(res: dict, key: str, limit: int) -> str:
    return " ".join(res.get(key, []) or [])[:limit]


def build() -> pd.DataFrame:
    master = pd.read_parquet(PROC / "drug_master.parquet")
    known = frozenset(master["name"])
    best: dict[str, tuple[tuple, dict]] = {}
    zips = sorted((RAW / "openfda_label").glob("*.zip"))
    for z in zips:
        with zipfile.ZipFile(z) as zf, zf.open(zf.namelist()[0]) as f:
            data = json.load(f)
        for res in data.get("results", []):
            ofda = res.get("openfda", {}) or {}
            # combination labels often have no substance_name and a generic like "NIRMATRELVIR AND RITONAVIR"
            gens = [p.strip() for g in ofda.get("generic_name", []) or []
                    for p in re.split(r"\s+and\s+|,|/", g.lower()) if p.strip()]
            subs = [s.lower() for s in ofda.get("substance_name", []) or []] or gens
            names = {n for n in (normalize_ingredient(s, known) for s in subs + gens) if n}
            if not names:
                continue
            di = _text(res, "drug_interactions", 30000)
            rx = "PRESCRIPTION" in " ".join(ofda.get("product_type", []) or []).upper()
            single = len(subs) <= 1
            rank = (bool(di), single, rx, len(di))
            row = {
                "drug_interactions": di,
                "boxed_warning": _text(res, "boxed_warning", 6000),
                "contraindications": _text(res, "contraindications", 6000),
                "warnings": _text(res, "warnings_and_cautions", 8000) or _text(res, "warnings", 8000),
                "set_id": res.get("set_id", ""),
                "brand_names": ", ".join(sorted({b.strip() for b in ofda.get("brand_name", []) or []}))[:300],
                "n_substances": len(subs),
            }
            for n in names:
                if n not in best or rank > best[n][0]:
                    best[n] = (rank, row)
        del data
        log.info(f"{z.name}: {len(best)}/{len(known)} drugs have a label")
    df = pd.DataFrame([{"name": n, **r} for n, (_, r) in sorted(best.items())])
    df.to_parquet(PROC / "label_index.parquet")
    log.info(f"label_index: {len(df)} drugs, {int((df['drug_interactions'].str.len() > 0).sum())} with interactions")
    return df
