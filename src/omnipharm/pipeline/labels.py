"""Build the two training tables.

1. twosides_pairs.parquet  - drug pair -> adverse-event signals (PRR >= 2, >= 3 reports)
2. faers_reports.npz       - one row per FAERS report: drugs taken, patient, events
Both share condition_mapping.parquet (event id <-> name, Korean name, organ).
"""

from __future__ import annotations

import json
import logging
import re

import numpy as np
import pandas as pd

from omnipharm.knowledge import COMORBIDITIES, TARGET_CONDITIONS, normalize_ingredient
from omnipharm.models.patient import MAX_DRUGS, SEX_CODE
from omnipharm.pipeline.common import CACHE, INTERIM, PROC

log = logging.getLogger("pipeline.labels")
MIN_POS_PAIRS = 150
COLD_FRACTION = 0.08


def cold_drugs(master: pd.DataFrame) -> set[str]:
    """Drug names held out of ALL training (both models) to measure unseen-structure accuracy."""
    f = PROC / "splits.json"
    names = sorted(master["name"])
    if f.exists():
        saved = json.loads(f.read_text(encoding="utf-8"))
        if saved.get("n_drugs") == len(names):
            return set(saved["cold"])
    rng = np.random.default_rng(0)
    cold = sorted(rng.choice(names, size=max(1, int(len(names) * COLD_FRACTION)), replace=False).tolist())
    f.write_text(json.dumps({"n_drugs": len(names), "cold": cold}, ensure_ascii=False), encoding="utf-8")
    return set(cold)


def build_pairs(master: pd.DataFrame) -> pd.DataFrame:
    valid = set(master["rxcui"].astype(str))
    pairs = pd.read_parquet(CACHE / "ts_pairs.parquet")
    pos = pd.read_parquet(CACHE / "ts_pos.parquet")
    for df in (pairs, pos):
        d1, d2 = df["d1"].astype(str), df["d2"].astype(str)
        df["d1"], df["d2"] = np.where(d1 < d2, d1, d2), np.where(d1 < d2, d2, d1)
    pairs = pairs[pairs.d1.isin(valid) & pairs.d2.isin(valid) & (pairs.d1 != pairs.d2)].drop_duplicates(["d1", "d2"])
    pos = pos[pos.d1.isin(valid) & pos.d2.isin(valid)].drop_duplicates()

    counts = pos.groupby("cond").size()
    keep = sorted(c for c in TARGET_CONDITIONS if counts.get(c, 0) >= MIN_POS_PAIRS)
    cid = {c: i for i, c in enumerate(keep)}
    pos = pos[pos.cond.isin(cid)].copy()
    pos["lid"] = pos.cond.map(cid)
    lab = pos.groupby(["d1", "d2"])["lid"].agg(lambda s: sorted(set(s))).reset_index()
    ds = pairs[["d1", "d2"]].merge(lab, on=["d1", "d2"], how="left")
    ds["lid"] = ds["lid"].apply(lambda v: list(v) if isinstance(v, (list, np.ndarray)) else [])
    ds = ds.rename(columns={"d1": "drug_1", "d2": "drug_2", "lid": "labels"}).reset_index(drop=True)
    ds.to_parquet(PROC / "twosides_pairs.parquet")
    cm = pd.DataFrame([{"label_id": i, "condition": c, "condition_ko": TARGET_CONDITIONS[c][0],
                        "organ": TARGET_CONDITIONS[c][1], "n_pos_pairs": int(counts[c])} for c, i in cid.items()])
    cm.to_parquet(PROC / "condition_mapping.parquet")
    log.info(f"pairs: {len(ds):,} ({int((ds['labels'].str.len() > 0).sum()):,} with >=1 event), {len(cm)} events")
    return cm


ABBREV = {"apap": "acetaminophen", "asa": "aspirin", "hctz": "hydrochlorothiazide", "mtx": "methotrexate",
          "ntg": "nitroglycerin", "kcl": "potassium chloride", "tmp": "trimethoprim", "smx": "sulfamethoxazole",
          "inh": "isoniazid", "ppi": "", "nsaid": ""}
# US brands frequent in 2004-2014 reports but discontinued / renamed since (absent from today's NDC directory)
LEGACY_BRANDS: dict[str, list[str]] = {
    "coumadin": ["warfarin"], "jantoven": ["warfarin"], "vicodin": ["hydrocodone", "acetaminophen"],
    "lortab": ["hydrocodone", "acetaminophen"], "norco": ["hydrocodone", "acetaminophen"],
    "percocet": ["oxycodone", "acetaminophen"], "darvocet": ["propoxyphene", "acetaminophen"],
    "vioxx": ["rofecoxib"], "bextra": ["valdecoxib"], "avandia": ["rosiglitazone"], "zantac": ["ranitidine"],
    "prilosec": ["omeprazole"], "prevacid": ["lansoprazole"], "aciphex": ["rabeprazole"], "zelnorm": ["tegaserod"],
    "ultram": ["tramadol"], "ultracet": ["tramadol", "acetaminophen"], "fosamax": ["alendronate"],
    "actos": ["pioglitazone"], "singulair": ["montelukast"], "lexapro": ["escitalopram"], "zyprexa": ["olanzapine"],
    "risperdal": ["risperidone"], "seroquel": ["quetiapine"], "lipitor": ["atorvastatin"], "crestor": ["rosuvastatin"],
    "zocor": ["simvastatin"], "vytorin": ["ezetimibe", "simvastatin"], "plavix": ["clopidogrel"],
    "nexium": ["esomeprazole"], "celebrex": ["celecoxib"], "lasix": ["furosemide"], "toprol": ["metoprolol"],
    "lopressor": ["metoprolol"], "norvasc": ["amlodipine"], "zestril": ["lisinopril"], "prinivil": ["lisinopril"],
    "glucophage": ["metformin"], "synthroid": ["levothyroxine"], "levoxyl": ["levothyroxine"],
    "neurontin": ["gabapentin"], "lyrica": ["pregabalin"], "cymbalta": ["duloxetine"], "effexor": ["venlafaxine"],
    "wellbutrin": ["bupropion"], "ambien": ["zolpidem"], "xanax": ["alprazolam"], "klonopin": ["clonazepam"],
    "ativan": ["lorazepam"], "valium": ["diazepam"], "depakote": ["valproate"], "tegretol": ["carbamazepine"],
    "lamictal": ["lamotrigine"], "keppra": ["levetiracetam"], "dilantin": ["phenytoin"], "prednisone": ["prednisone"],
    "medrol": ["methylprednisolone"], "advil": ["ibuprofen"], "motrin": ["ibuprofen"], "aleve": ["naproxen"],
    "tylenol": ["acetaminophen"], "bayer": ["aspirin"], "ecotrin": ["aspirin"], "lovenox": ["enoxaparin"],
    "cordarone": ["amiodarone"], "pacerone": ["amiodarone"], "lanoxin": ["digoxin"], "zyrtec": ["cetirizine"],
    "claritin": ["loratadine"], "allegra": ["fexofenadine"], "flomax": ["tamsulosin"], "proscar": ["finasteride"],
    "viagra": ["sildenafil"], "cialis": ["tadalafil"], "zithromax": ["azithromycin"], "cipro": ["ciprofloxacin"],
    "levaquin": ["levofloxacin"], "bactrim": ["sulfamethoxazole", "trimethoprim"], "diflucan": ["fluconazole"],
    "xarelto": ["rivaroxaban"], "eliquis": ["apixaban"], "pradaxa": ["dabigatran"], "januvia": ["sitagliptin"],
    "humira": [], "enbrel": [], "remicade": [],  # biologics: no structure, keep unmapped
}
_DOSE = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:mg|mcg|ug|g|ml|%|iu|units?|meq|mmol)\b(?:\s*/\s*\d*(?:\.\d+)?\s*(?:ml|g|hr|h))?")
_FORM = re.compile(r"\b(tablets?|tabs?|capsules?|caps?|caplets?|injection|injectable|inj|oral|solution|soln|suspension|"
                   r"susp|syrup|cream|ointment|gel|patch|spray|drops|er|xr|sr|cr|xl|la|dr|ec|odt|hcl|hydrochloride|"
                   r"sodium|potassium|generic|unknown|unk|nos|film[- ]coated|extended[- ]release|delayed[- ]release)\b")


class VerbatimResolver:
    """Verbatim product names in older reports ('COUMADIN 5MG TABS', 'HYDROCODONE/APAP', 'VICODIN') ->
    ingredient names in drug_master, via dose/form stripping, combination splitting, abbreviations and the
    US brand names of the FDA NDC directory and FDA labels."""

    def __init__(self, known: frozenset[str]) -> None:
        self.known = known
        self.brand: dict[str, list[str]] = {b: [k for k in ks if k in known] for b, ks in LEGACY_BRANDS.items()
                                            if ks and all(k in known for k in ks)}
        wp = PROC / "world_products.parquet"
        if wp.exists():
            w = pd.read_parquet(wp, columns=["country", "name", "base", "ingredients"])
            w = w[w["country"].isin(["US", "CA"])]
            for name, base, ings in zip(w["name"], w["base"], w["ingredients"], strict=True):
                keys = [k for k in ings if k in known]
                if keys and len(keys) == len(ings):
                    for b in {self._clean(str(name)), str(base)}:
                        if b:
                            self.brand.setdefault(b, keys)
        li = PROC / "label_index.parquet"
        if li.exists():
            lab = pd.read_parquet(li, columns=["name", "brand_names", "n_substances"])
            for r in lab[lab["n_substances"] <= 1].itertuples():
                for b in str(r.brand_names).split(", "):
                    if b.strip():
                        self.brand.setdefault(self._clean(b), [r.name])

    @staticmethod
    def _clean(s: str) -> str:
        t = re.sub(r"\(.*?\)|\[.*?\]", " ", s.lower())
        t = _DOSE.sub(" ", t)
        t = _FORM.sub(" ", t)
        t = re.sub(r"[^a-z0-9/+&,\- ]", " ", t)
        return " ".join(t.split())

    def _one(self, part: str) -> list[str]:
        part = part.strip(" -,")
        if not part:
            return []
        part = ABBREV.get(part, part)
        n = normalize_ingredient(part, self.known)
        if n:
            return [n]
        if part in self.brand:
            return self.brand[part]
        first = part.split()[0] if part.split() else ""
        if first in self.brand:
            return self.brand[first]
        n = normalize_ingredient(first, self.known) if first else None
        return [n] if n else []

    def __call__(self, raw: str) -> list[str]:
        t = self._clean(raw)
        if not t:
            return []
        whole = self._one(t)
        if whole:
            return whole
        parts = [p for p in re.split(r"\s*(?:/|\+|&|,|\band\b|\bwith\b)\s*", t) if p.strip()]
        if len(parts) > 1:
            out = [self._one(p) for p in parts]
            if all(out):
                return list(dict.fromkeys(k for ks in out for k in ks))
        return []


def build_faers(master: pd.DataFrame, cm: pd.DataFrame) -> None:
    files = sorted((INTERIM / "faers").glob("*.parquet"))
    if not files:
        log.warning("no FAERS quarters parsed yet - run the 'faers' stage first")
        return
    names = master["name"].tolist()
    known = frozenset(names)
    name_idx = {n: i for i, n in enumerate(names)}
    ev_idx = {c: i for i, c in enumerate(cm["condition"])}
    verbatim = VerbatimResolver(known)
    ai_cache: dict[str, list[int]] = {}

    def ai_to_idx(s: str) -> list[int]:
        """Active-ingredient string -> drug indices ([-1] if unknown). '@...' = verbatim product name
        (legacy AERS / pre-2014Q3 FAERS), which may be a brand or a combination -> several ingredients."""
        v = ai_cache.get(s)
        if v is None:
            if s.startswith("@"):
                v = [name_idx[n] for n in verbatim(s[1:])] or [-1]
            else:
                n = normalize_ingredient(s, known)
                v = [name_idx[n]] if n is not None else [-1]
            ai_cache[s] = v
        return v

    parts: dict[str, list[np.ndarray]] = {k: [] for k in
                                          ("drugs", "age", "sex", "wt", "flags", "labels", "quarter", "caseid", "ver",
                                           "n_unmapped", "src")}
    for f in files:
        df = pd.read_parquet(f)
        n = len(df)
        ex = df[["ingredients"]].explode("ingredients")
        ex = ex.assign(ingredients=ex["ingredients"].map(ai_to_idx)).explode("ingredients")
        idx = ex["ingredients"].to_numpy(dtype=np.int64)
        row = ex.index.to_numpy()
        unmapped = np.bincount(row[idx < 0], minlength=n)
        ok = idx >= 0
        e = pd.DataFrame({"row": row[ok], "d": idx[ok]}).drop_duplicates()
        e["k"] = e.groupby("row").cumcount()
        n_mapped = np.bincount(e["row"], minlength=n)
        e = e[e["k"] < MAX_DRUGS]
        drugs = np.full((n, MAX_DRUGS), -1, dtype=np.int16)
        drugs[e["row"].to_numpy(), e["k"].to_numpy()] = e["d"].to_numpy()

        ee = df[["events"]].explode("events").dropna()
        lid = ee["events"].map(ev_idx)
        lid = lid[lid.notna()]
        labels = np.zeros(n, dtype=np.int64)
        np.bitwise_or.at(labels, lid.index.to_numpy(), np.left_shift(np.int64(1), lid.to_numpy(dtype=np.int64)))

        keep = (n_mapped >= 1) & (n_mapped <= MAX_DRUGS)
        canada = f.stem.startswith("CA_")  # Canada Vigilance (stage "canada"): source 1
        y, q = f.stem.removeprefix("CA_").split("Q")
        parts["src"].append(np.full(int(keep.sum()), 1 if canada else 0, dtype=np.int8))
        parts["drugs"].append(drugs[keep])
        parts["age"].append(df["age"].to_numpy(dtype=np.float32)[keep])
        parts["sex"].append(df["sex"].map(SEX_CODE).fillna(0).to_numpy(dtype=np.int8)[keep])
        parts["wt"].append(df["wt"].to_numpy(dtype=np.float32)[keep])
        parts["flags"].append(df[COMORBIDITIES].to_numpy(dtype=np.uint8)[keep])
        parts["labels"].append(labels[keep])
        parts["quarter"].append(np.full(int(keep.sum()), int(y) * 4 + int(q) - 1, dtype=np.int16))
        parts["caseid"].append(pd.to_numeric(df["caseid"], errors="coerce").fillna(-1).to_numpy(dtype=np.int64)[keep])
        parts["ver"].append(df["caseversion"].to_numpy(dtype=np.int32)[keep])
        parts["n_unmapped"].append(np.clip(unmapped, 0, 127).astype(np.int8)[keep])
        log.info(f"{f.stem}: kept {int(keep.sum()):,}/{n:,} reports")

    A = {k: np.concatenate(v) for k, v in parts.items()}
    # a case is re-sent with a higher version when updated: keep only the latest version
    order = np.lexsort((A["ver"], A["caseid"]))
    cid = A["caseid"][order]
    last = np.ones(len(order), dtype=bool)
    last[:-1] = cid[1:] != cid[:-1]
    last |= cid < 0  # reports without a case id cannot be de-duplicated: keep them all
    sel = np.sort(order[last])
    A = {k: v[sel] for k, v in A.items() if k not in ("caseid", "ver")}
    np.savez(PROC / "faers_reports.npz", **A)

    n = len(A["age"])
    nd = (A["drugs"] >= 0).sum(1)
    C = len(cm)
    prev = [float(((A["labels"] >> c) & 1).mean()) for c in range(C)]
    stats = {"n_reports": int(n), "n_multi_drug": int((nd >= 2).sum()), "mean_drugs": float(nd.mean()),
             "age_known": float((~np.isnan(A["age"])).mean()), "wt_known": float((~np.isnan(A["wt"])).mean()),
             "sex_known": float((A["sex"] > 0).mean()),
             "comorbidity": {k: float(A["flags"][:, i].mean()) for i, k in enumerate(COMORBIDITIES)},
             "quarters": [fa[0].stem, fa[-1].stem] if (fa := [f for f in files if not f.stem.startswith("CA_")]) else [],
             "n_canada": int((A["src"] == 1).sum()), "ingredient_strings": len(ai_cache),
             "ingredient_strings_mapped": int(sum(v[0] >= 0 for v in ai_cache.values())),
             "event_prevalence": dict(zip(cm["condition"], prev, strict=True))}
    (PROC / "faers_stats.json").write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info(f"FAERS table: {n:,} unique reports (Canada {stats['n_canada']:,}; {stats['n_multi_drug']:,} with 2+ drugs), "
             f"age known {stats['age_known']:.0%}, mapped ingredient strings "
             f"{stats['ingredient_strings_mapped']:,}/{stats['ingredient_strings']:,}")


def build() -> None:
    master = pd.read_parquet(PROC / "drug_master.parquet")
    cold_drugs(master)
    cm = build_pairs(master)
    build_faers(master, cm)
