"""OMNI-PHARM inference engine.

For a regimen (2+ drugs, given by name, Korean/English brand, ingredient list or raw SMILES) and a
patient, combine three independent kinds of evidence, never mixing them up:

  A. Patient-aware regimen model (FAERS, millions of reports): P(event | these drugs, this patient)
     -> also: reference-patient comparison (patient effect), leave-one-out (which drug drives it),
        single-drug baselines (does the combination add risk beyond either drug alone).
  B. Pair structure model (TWOSIDES, GNN + fingerprint ensemble): P(pair shows a significant
     co-reporting signal for event) -> works for molecules never seen before (custom SMILES).
  C. Documented evidence: FDA label interaction / contraindication text, observed TWOSIDES signal.

Code layout (v2.18 split; the Engine API is unchanged and every name below is importable from here as before):
  engine.py           Engine: loading data and models, the AI parts, predict()
  engine_resolve.py   ResolveMixin: names / products / foods / SMILES -> ingredients, search, Korean display names
  engine_evidence.py  EvidenceMixin: documented evidence and the documented level of a pair
  engine_labels.py    reading FDA label sentences (interaction? contraindication? allergy, own class, cross-reference)
  engine_clinical.py  ClinicalMixin: drug-disease / age / pregnancy alerts, the plain-language summary
  engine_base.py      shared types (Ingredient, ResolveError), thresholds, small helpers
  rules/*.json        clinical rules with sources and review records (knowledge.py loads them)
"""

from __future__ import annotations

import itertools
import json
import logging
import threading

import numpy as np
import pandas as pd
import torch

from omnipharm.chem.featurize import smiles_to_graph
from omnipharm.clinical import Patient, assess
from omnipharm.config import settings
from omnipharm.dosing import check_doses
from omnipharm.engine_base import (
    ACTIONS,
    COMBO_X,
    CYP_INHIBITION_P,
    KO_NAME,
    MIN_P,
    REFERENCE_PATIENT,
    SIGNAL_P,
    Ingredient,
    ResolveError,
    _combo,
    _diluent_pair,
    _key,
    _load_json,
    _minor,
    added_risk,
    lab_key,
    script,
)
from omnipharm.engine_clinical import ClinicalMixin
from omnipharm.engine_evidence import (
    EvidenceMixin,
)
from omnipharm.engine_labels import (
    _about,
    _bare,
    _context,
    _is_contra,
    _is_strong,
    _own,
)
from omnipharm.engine_resolve import ResolveMixin
from omnipharm.journey import build_journey
from omnipharm.knowledge import (
    BIOLOGICS,
    COMORBIDITIES,
    DRUG_CLASSES,
    DRUG_SYNONYMS,
    EVENT_INDICATION,
    INTENDED_COMBINATIONS,
    KR_BRANDS,
    KR_INGREDIENTS,
    ORGANS,
    SERIOUS_CONDITIONS,
    drug_class,
    drug_groups,
    kr_avoid_targets,
    structureless_category,
)
from omnipharm.models.gnn import FingerprintEncoder, OmniPharmModel, RegimenPatientModel
from omnipharm.models.patient import MAX_DRUGS, PATIENT_DIM, patient_vector
from omnipharm.release import RELEASE_KO, release_of

# v2.18: the engine is split into modules (engine_base / engine_labels / engine_resolve / engine_evidence /
# engine_clinical); every name that used to live here is still importable from omnipharm.engine
__all__ = ["ACTIONS", "COMBO_X", "MIN_P", "SIGNAL_P", "Engine", "Ingredient", "ResolveError", "_combo", "_is_contra",
           "_is_strong", "_own", "added_risk", "script"]
log = logging.getLogger(__name__)


class Engine(ResolveMixin, EvidenceMixin, ClinicalMixin):
    def __init__(self) -> None:
        self.lock = threading.Lock()  # GPU inference
        self._online_lock = threading.Lock()  # PubChem lookup cache file
        self.dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        p, art = settings.processed_dir, settings.artifacts_dir / "v2"
        self.master = pd.read_parquet(p / "drug_master.parquet").reset_index(drop=True)
        self.master["rxcui"] = self.master["rxcui"].astype(str)
        self.name_idx = {n: i for i, n in enumerate(self.master["name"])}
        self.known = frozenset(self.name_idx)
        self.inchikey_idx = {k: i for i, k in enumerate(self.master["std_inchikey"])}
        self.cm = pd.read_parquet(p / "condition_mapping.parquet")
        self.C = len(self.cm)
        self.serious = np.array([c in SERIOUS_CONDITIONS for c in self.cm["condition"]])
        lab = p / "label_index.parquet"
        self.labels = pd.read_parquet(lab).fillna("").set_index("name") if lab.exists() else pd.DataFrame()
        self.brand: dict[str, str] = {}
        if len(self.labels) and "n_substances" in self.labels:
            for name, r in self.labels[self.labels["n_substances"] <= 1].iterrows():
                for b in str(r["brand_names"]).split(", "):
                    b = b.strip().lower()
                    if b and b not in self.name_idx:
                        self.brand.setdefault(b, name)
        pairs = pd.read_parquet(p / "twosides_pairs.parquet")
        ridx = {r: i for i, r in enumerate(self.master["rxcui"])}
        self.observed: dict[frozenset, list[int]] = {}
        y = np.zeros(self.C)
        for r in pairs.itertuples():
            labs = list(r.labels)
            y[labs] += 1
            a, b = ridx.get(str(r.drug_1)), ridx.get(str(r.drug_2))
            if labs and a is not None and b is not None:
                self.observed[frozenset((a, b))] = labs
        self.pair_prev = y / max(len(pairs), 1)
        if settings.commercial_safe:  # TWOSIDES is research-licensed: no observed pair signals
            self.observed = {}
        self.mol3d = _load_json(p / "mol3d.json") or {}

        # Marketed products: Korea (식약처, stage "mfds") + US/Canada/EU/France (stage "world"); both optional
        parts = []
        if (p / "kr_products.parquet").exists():
            parts.append(pd.read_parquet(p / "kr_products.parquet").assign(country="KR"))
        if (p / "world_products.parquet").exists():
            parts.append(pd.read_parquet(p / "world_products.parquet"))
        # Korean label (용법·주의사항·연령 제한) per product: "Q:<item_seq>" 의약외품 (stage "dur"), "D:<item_seq>" 의약품
        # (stage "kr-labels"; the two registries number their items separately). Texts are cut to what the chat and
        # the age check read, so 42k prescription labels do not fill the memory.
        self.label_ko: dict[str, dict] = {}
        if (p / "kr_quasi_products.parquet").exists():  # 의약외품 (stage "dur")
            q = pd.read_parquet(p / "kr_quasi_products.parquet").assign(country="KR")
            parts.append(q)
            if "usage" in q.columns:
                self.label_ko = {f"Q:{r.item_seq}": {"usage": r.usage, "cautions": r.cautions, "kind": "의약외품",
                                                     "min_age": None if pd.isna(r.min_age) else int(r.min_age)}
                                 for r in q.itertuples() if r.usage or r.cautions}
        if (p / "kr_hff_products.parquet").exists():  # 건강기능식품 (stage "supplements"): "H:<품목제조번호>"
            h = pd.read_parquet(p / "kr_hff_products.parquet").assign(country="KR")
            parts.append(h)
            for r in h.itertuples():
                self.label_ko[f"H:{r.item_seq}"] = {"usage": r.usage[:400], "cautions": r.cautions[:1500],
                                                    "kind": "건강기능식품", "min_age": None,
                                                    "avoid_with": list(r.avoid_with)}
        if (p / "kr_labels.parquet").exists():
            for r in pd.read_parquet(p / "kr_labels.parquet").itertuples():
                if not (r.usage or r.cautions):
                    continue
                rx = "전문" in r.kind
                self.label_ko[f"D:{r.item_seq}"] = {
                    # a prescription medicine is taken as prescribed: the label dose is the licensed range only
                    "usage": ("(전문의약품 허가 용법 — 처방받은 용법이 우선) " if rx else "") + r.usage[:800],
                    "cautions": r.cautions[:2000], "kind": r.kind,
                    "min_age": None if pd.isna(r.min_age) else int(r.min_age),
                    "avoid_with": list(r.avoid_with)}
        cols = ["country", "item_seq", "name", "base", "company", "kind", "status", "ingredients", "unresolved"]
        prod = pd.concat([d[cols] for d in parts], ignore_index=True) if parts else pd.DataFrame(columns=cols)
        # ingredients without a structure (herbal, vitamins, minerals, antibodies ...) stay in the product as
        # label/rule-only "lab:" ingredients instead of making the product unusable
        prod["ingredients"] = [list(i) + [k for k in (lab_key(u) for u in un) if k and k not in i]
                               for i, un in zip(prod["ingredients"], prod["unresolved"], strict=True)]
        prod["unresolved"] = [[] for _ in range(len(prod))]
        prod = prod[prod["ingredients"].map(len) > 0].reset_index(drop=True)
        if settings.commercial_safe:  # Japanese ingredient names come from KEGG (academic licence)
            prod = prod[prod["country"] != "JP"].reset_index(drop=True)
        prod["active"] = prod["status"].eq("정상")
        prod["otc"] = prod["kind"].str.contains("일반|OTC", regex=True)
        prod["key"] = prod["name"].map(_key)
        self.products = prod
        # ingredient pairs combined in one marketed product (Symbyax olanzapine + fluoxetine, Prezcobix darunavir +
        # cobicistat, bupivacaine + epinephrine, midazolam in sodium chloride): an approved combination, so the
        # labels' wording about them is not an interaction warning - also when the two are entered separately
        self.combo_pairs: set[frozenset[str]] = {
            frozenset(pr) for ings in prod["ingredients"]
            if 2 <= len(b := {_bare(k) for k in ings}) <= 6 for pr in itertools.combinations(sorted(b), 2)}
        self.combo_pairs |= {frozenset((a, b)) for a, b, _ in INTENDED_COMBINATIONS}  # boosters, regimens
        self.prod_by_key: dict[str, list[int]] = {}
        for i, k in enumerate(prod["key"]):
            self.prod_by_key.setdefault(k, []).append(i)
        self.jp_names = ({} if settings.commercial_safe else
                         _load_json(p / "jp_ingredient_names.json") or {})  # JAN (Japanese) -> English (KEGG)
        # Korean ingredient names learned from MFDS product details (stage "strengths"): '인플릭시맵' -> key
        self.kr_names: dict[str, str] = _load_json(p / "kr_ingredient_names.json") or {}
        self.kr_names = {ko: k for ko, k in self.kr_names.items() if len(ko) >= 3}  # '염화' (salt-stripped) is no name
        for ko, key in sorted(self.kr_names.items(), key=lambda kv: len(kv[0])):
            KO_NAME.setdefault(key.removeprefix("ing:").removeprefix("lab:"), ko)
        # full-text index of all FDA labels (stage "labelsearch") and ChEMBL pharmacology (stage "chembl")
        import sqlite3

        self._lab_lock = threading.Lock()
        self._labdb = (sqlite3.connect(f"file:{p / 'labels.db'}?mode=ro", uri=True, check_same_thread=False)
                       if (p / "labels.db").exists() else None)
        # in-memory maps so full-text hits (rowids only - fast) are filtered without reading the label text
        self._sec_label = np.zeros(0, dtype=np.int32)
        self._sec_kind = np.zeros(0, dtype=np.int8)
        self._sections: list[str] = []
        self._lab_rx: dict[int, int] = {}
        self._lab_nkeys: dict[int, int] = {}
        self._lab_keys: dict[int, frozenset] = {}
        self._key_labels: dict[str, set[int]] = {}
        self._evidence_cache: dict[tuple[str, str], list[dict]] = {}
        self._hit_cache: dict[str, np.ndarray] = {}
        self._key_label_arr: dict[str, np.ndarray] = {}
        if self._labdb is not None:
            sm = self._labdb.execute("SELECT rowid, label_id, section FROM sec_map").fetchall()
            n = max((r for r, _, _ in sm), default=0) + 1
            self._sections = sorted({s for _, _, s in sm})
            code = {s: i for i, s in enumerate(self._sections)}
            self._sec_label = np.zeros(n, dtype=np.int32)
            self._sec_kind = np.full(n, -1, dtype=np.int8)
            for r, lid, s in sm:
                self._sec_label[r], self._sec_kind[r] = lid, code[s]
            for lid, rx, nk in self._labdb.execute("SELECT id, rx, n_keys FROM label"):
                self._lab_rx[lid], self._lab_nkeys[lid] = rx, nk
            keys_of: dict[int, set] = {}
            for lid, k in self._labdb.execute("SELECT label_id, key FROM label_key"):
                keys_of.setdefault(lid, set()).add(k)
                self._key_labels.setdefault(k, set()).add(lid)
            self._lab_keys = {lid: frozenset(v) for lid, v in keys_of.items()}
        self.label_keys: frozenset[str] = frozenset(self._key_labels)
        self.pharm: dict[str, dict] = {}
        if (p / "pharmacology.parquet").exists():
            for r in pd.read_parquet(p / "pharmacology.parquet").itertuples():
                self.pharm[r.name] = {"sub": set(r.cyp_substrate), "inh": json.loads(r.cyp_inhibition),
                                      "herg": r.herg_pchembl if r.herg_pchembl == r.herg_pchembl else None,
                                      "moa": json.loads(r.moa)}
        # ChEMBL approved drugs (stage "chembl-drugs"): world-wide trade names / synonyms and WHO ATC classes
        cd = _load_json(p / "chembl_drugs.json") or {}
        self.syn: dict[str, str] = cd.get("synonyms", {})
        self.atc_of = {k.removeprefix("ing:").removeprefix("lab:").removeprefix("bio:"): v
                       for k, v in cd.get("atc", {}).items()}
        self.atc4: dict[str, str] = cd.get("atc4", {})
        # time to peak and half-life per ingredient from the FDA labels (stage "pk"): the body view's time axis
        self.pk: dict[str, dict] = _load_json(p / "pk.json") or {}
        # MFDS DUR (stage "dur"): the official Korean contraindication / caution lists, keyed by plain ingredient name
        self.dur = _load_json(p / "dur.json") or {}
        bare = lambda k: k.removeprefix("ing:").removeprefix("lab:").removeprefix("bio:")  # noqa: E731
        self.dur_pairs = {frozenset((bare(r["a"]), bare(r["b"]))): r for r in self.dur.get("pairs", [])}
        self.dur_by: dict[str, dict[str, list]] = {}
        for kind in ("age", "pregnancy", "elderly", "efficacy"):
            for r in self.dur.get(kind, []):
                self.dur_by.setdefault(kind, {}).setdefault(bare(r["key"]), []).append(r)
        from omnipharm import dosing

        for r in self.dur.get("dose", []):
            dosing.EXTRA_LIMITS.setdefault(bare(r["key"]), dosing.Limit(r["max_mg"]))
        for r in self.dur.get("duration", []):
            dosing.EXTRA_DURATION.setdefault(bare(r["key"]), r["days"])
        for r in self.dur.get("pairs", []):  # DUR Korean ingredient names are also search names
            for k, ko in ((r["a"], r.get("a_ko")), (r["b"], r.get("b_ko"))):
                if ko:
                    KO_NAME.setdefault(bare(k), ko)
        # ingredient mg per dosing unit, per product (stage "strengths")
        self.strengths: dict[tuple[str, str], dict] = {}
        sf = p / "product_strengths.parquet"
        if sf.exists():
            for r in pd.read_parquet(sf).itertuples():
                self.strengths[(r.country, str(r.item_seq))] = {"unit": r.unit, "per": r.per,
                                                                "mg": json.loads(r.strengths)}
        ext = p / "ext_ingredients.parquet"
        self.ext_ing = {r.key: r for r in pd.read_parquet(ext).itertuples()} if ext.exists() else {}
        self._online_cache_f = settings.cache_dir / "pubchem_online.json"
        self._online_cache = _load_json(self._online_cache_f) or {}

        self.pair_metrics = _load_json(art / "pair_metrics.json")
        self.patient_metrics = _load_json(art / "patient_model_metrics.json")
        self.ablation = _load_json(art / "patient_model_nopatient_metrics.json")
        self.stats = _load_json(art / "stats_metrics.json")  # CIs / significance / baselines (stage "stats")
        # OFFSIDES single-drug signals meeting the Evans criteria (stage "offsides"): {(drug name, event)}
        off = p / "offsides.parquet"  # research-use data like TWOSIDES: not in commercial-safe mode
        self.offsides: set[tuple[str, str]] = (
            {(r.drug, r.condition) for r in pd.read_parquet(off).itertuples() if r.evans}
            if off.exists() and not settings.commercial_safe else set())
        # foods by name with what they contain per 100 g / 100 mL (MFDS food nutrient DB, stage "food-nutrients")
        fn = p / "food_nutrients.parquet"
        self.food_nutr: dict[str, dict] = (  # a value the DB does not give is None (NaN is not valid JSON)
            {r["key"]: {k: (None if isinstance(v, float) and v != v else v) for k, v in r.items()}
             for r in pd.read_parquet(fn).to_dict("records")} if fn.exists() else {})
        self._food_keys = sorted(self.food_nutr)  # prefix search (autocomplete)
        # what each drug is given for (ChEMBL drug_indication, stage "chembl-dump"): ingredient key -> terms
        ind = p / "drug_indications.parquet"
        self.indications: dict[str, str] = (
            {r.name: " | ".join(r.indications) for r in pd.read_parquet(ind).itertuples()} if ind.exists() else {})
        self.pair_models: list[tuple[str, OmniPharmModel, torch.Tensor]] = []
        self.pair_thr_unseen, self.pair_seen = None, set()
        self.patient_model = None
        from omnipharm.pipeline.trainutil import all_graphs, morgan_matrix

        G = all_graphs(self.master, self.dev)
        FP = torch.as_tensor(morgan_matrix(self.master), device=self.dev)
        self._feat = {"gnn": lambda s: smiles_to_graph(s).to(self.dev),
                      "fingerprint": lambda s: torch.as_tensor(
                          morgan_matrix(pd.DataFrame({"std_smiles": [s]})), device=self.dev),
                      "patient": lambda s: smiles_to_graph(s).to(self.dev)}
        with torch.no_grad():
            if self.pair_metrics and self.pair_metrics["n_classes"] == self.C and not settings.commercial_safe:
                for kind, inp in (("gnn", G), ("fingerprint", FP)):
                    w = art / f"pair_{kind}.pt"
                    if not w.exists():
                        continue
                    m = OmniPharmModel(num_classes=self.C,
                                       encoder=FingerprintEncoder() if kind == "fingerprint" else None).to(self.dev)
                    m.load_state_dict(torch.load(w, map_location=self.dev, weights_only=True))
                    m.eval()
                    self.pair_models.append((kind, m, m.encoder.encode(inp)))
                self.pair_thr = np.array(self.pair_metrics["thresholds"])
                tu = self.pair_metrics.get("thresholds_unseen")
                self.pair_thr_unseen = np.array(tu) if tu else None
                self.pair_seen = set(self.pair_metrics.get("seen_drugs", self.master["name"]))
                self.pair_prev = np.array(self.pair_metrics.get("train_prevalence", self.pair_prev))
            if (self.patient_metrics and self.patient_metrics["n_classes"] == self.C
                    and (art / "patient_model.pt").exists()):
                m = RegimenPatientModel(num_classes=self.C, patient_dim=PATIENT_DIM).to(self.dev)
                m.load_state_dict(torch.load(art / "patient_model.pt", map_location=self.dev, weights_only=True))
                self.patient_model = m.eval()
                self.pat_emb = m.encoder.encode(G)
                self.prev = np.array(self.patient_metrics["prevalence"])
        self.pair_model = bool(self.pair_models)
        self._custom: dict[tuple[str, str], torch.Tensor] = {}
        log.info(f"engine: {len(self.master)} drugs, {self.C} events, {len(self.labels)} FDA labels, "
                 f"{len(self.brand)} brand names, pair models={[k for k, _, _ in self.pair_models]}, "
                 f"patient model={self.patient_model is not None}")

    def _emb(self, ings: list[Ingredient], kind: str, model, base: torch.Tensor) -> tuple[torch.Tensor, list[int]]:
        """Embedding table = all DB drugs + any custom molecules of this request (encoded once, cached)."""
        extra, idx = [], []
        for g in ings:
            if g.idx is not None:
                idx.append(g.idx)
                continue
            e = self._custom.get((kind, g.key))
            if e is None:
                with torch.no_grad():
                    e = model.encoder.encode(self._feat[kind](g.smiles))
                if len(self._custom) > 2000:  # bounded: user-typed SMILES must not grow memory forever
                    self._custom.pop(next(iter(self._custom)))
                self._custom[(kind, g.key)] = e
            idx.append(len(base) + len(extra))
            extra.append(e)
        return (torch.cat([base, *extra]) if extra else base), idx

    # ------------------------------------------------------------------ prediction
    def predict(self, products: list[dict], patient: Patient) -> dict:
        with self.lock:
            return self._predict(products, patient)

    def _predict(self, products: list[dict], patient: Patient) -> dict:
        cm = self.cm
        pa = assess(patient)
        # extended / delayed release from the product name (or the typed name: 'metformin XR')
        products = [{**p, "release": p.get("release") or release_of(p.get("product") or p["input"])} for p in products]
        seen: dict[str, Ingredient] = {}
        duplicates = []
        owner: dict[str, str] = {}
        prods_of: dict[str, set[str]] = {}
        for prod in products:
            for g in prod["ingredients"]:
                seen.setdefault(g.key, g)
                owner.setdefault(g.key, prod["input"])
                prods_of.setdefault(g.key, set()).add(prod["input"])
        U = list(seen.values())
        order = [p["input"] for p in products]
        # Korean OTC labels: 'do not take with …' lists of the products in this analysis, per ingredient
        # (one ingredient carries the product's list, so the finding is shown once per product, not once per ingredient)
        kr_avoid: dict[str, list[tuple]] = {}
        for prod in products:
            own = {g.key for g in prod["ingredients"]}
            lab = prod.get("label_ko") or {}
            for item in lab.get("avoid_with") or []:
                t = kr_avoid_targets(item)
                if t and prod["ingredients"]:
                    hff = lab.get("kind") == "건강기능식품"  # '…복용 시 섭취에 주의': a caution, never more
                    kr_avoid.setdefault(prod["ingredients"][0].key, []).append(
                        (prod.get("product") or prod["input"], item, t[0], "주의" if hff else t[1], own, hff))
        for g in U:  # one entry per ingredient, listing every product that supplies it
            if len(prods_of[g.key]) > 1:
                # vitamins / minerals / caffeine in two products (energy drink + painkiller) are not an overdose by
                # themselves; the dose check catches real excess (caffeine > 400 mg/day etc.)
                duplicates.append({"ingredient": self.ko(g), "products": [p for p in order if p in prods_of[g.key]],
                                   "minor": _minor(g)})
        notes = []

        # same therapeutic class supplied by two different products (e.g. two cold medicines)
        class_dups = []
        for cid, (cname, _) in DRUG_CLASSES.items():
            mem = [g for g in U if drug_class(g.key) == cid]
            src = set().union(*(prods_of[g.key] for g in mem)) if mem else set()
            if len(mem) >= 2 and len(src) >= 2:
                class_dups.append({"class": cname, "ingredients": [self.ko(g) for g in mem], "products": sorted(src),
                                   # sold together in one product (aspirin + clopidogrel): a regimen, not a mistake
                                   "intended": all(frozenset((x.bare, y.bare)) in self.combo_pairs
                                                   for x, y in itertools.combinations(mem, 2))})
        # MFDS DUR same-efficacy-group duplication (효능군중복) across different products
        # compared within the same SERIES (ARB + ARB, SSRI + SSRI); different series of one efficacy group
        # (ARB + CCB, metformin + DPP-4 inhibitor) are standard combination therapy, not duplication
        eff: dict[str, list[Ingredient]] = {}
        for g in U:
            for r in self.dur_by.get("efficacy", {}).get(g.bare, [])[:1]:
                series = (r.get("series") or "").strip()
                if series and series != "기타":
                    eff.setdefault(f"{r['group']}: {series}", []).append(g)
        covered = {frozenset(d["ingredients"]) for d in class_dups}
        for grp, mem in eff.items():
            src = set().union(*(prods_of[g.key] for g in mem))
            names = [self.ko(g) for g in mem]
            if len(mem) >= 2 and len(src) >= 2 and frozenset(names) not in covered:
                # v2.20 (pharmacist review round 2: ARB + ARB, DPP-4 + DPP-4 were '위험' for both pharmacists): two drugs of
                # the same series are a prescribing duplication to correct - '위험' unless sold together in one product
                class_dups.append({"class": f"{grp} (식약처 DUR 효능군중복)", "ingredients": names, "products": sorted(src),
                                   "dur_series": True,
                                   "intended": all(frozenset((x.bare, y.bare)) in self.combo_pairs
                                                   for x, y in itertools.combinations(mem, 2))})
                covered.add(frozenset(names))
        # WHO ATC level 4 (same chemical/therapeutic subgroup) for every approved drug (ChEMBL); vitamins,
        # minerals, topical and 'various' groups are excluded - combining those is usually intended
        # (C05: hemorrhoid / varicose preparations). Codes in order: a set's order changes between runs, and the first
        # shared code names the class shown
        atc_grp: dict[str, list[Ingredient]] = {}
        for g in U:
            for code in sorted({c[:5] for c in self.atc_of.get(g.bare, [])}):
                if not code.startswith(("A11", "A12", "D", "S", "V", "R02", "A01", "C05")):
                    atc_grp.setdefault(code, []).append(g)
        for code, mem in atc_grp.items():
            mem = list({g.key: g for g in mem}.values())
            src = set().union(*(prods_of[g.key] for g in mem))
            names = [self.ko(g) for g in mem]
            if len(mem) >= 2 and len(src) >= 2 and not any(frozenset(names) <= c for c in covered):
                class_dups.append({"class": f"{self.atc4.get(code, code).capitalize()} (ATC {code})",
                                   "ingredients": names, "products": sorted(src)})
                covered.add(frozenset(names))
        if len(U) > MAX_DRUGS:
            notes.append(f"성분이 {len(U)}개로 환자 모델 한도({MAX_DRUGS})를 넘어 앞의 {MAX_DRUGS}개만 환자 모델에 사용했습니다.")

        # ---- A. patient-aware regimen model
        events: list[dict] = []
        drug_contrib: list[dict] = []
        # The AI models take small-molecule drugs only. Vitamins / minerals / amino acids are left out as well: in
        # FAERS they mostly appear in very sick patients (hospital nutrition), so the model would attach alarming
        # events (seizure, liver failure) to the niacinamide of a tonic drink. They are still checked by the rules.
        nutrient = [g for g in U if _minor(g) and g.bare != "caffeine"]
        mols = [g for g in U if not g.biologic and g not in nutrient]
        bios = [g for g in U if g.biologic and g not in nutrient and not g.food]
        if bios:
            notes.append(f"생물의약품·생약 등 분자 구조가 없는 성분({', '.join(self.ko(g) for g in bios[:8])}"
                         f"{' 외' if len(bios) > 8 else ''})은 구조 AI 예측 대상이 아니어서 허가 문서·임상 규칙으로만 평가했습니다.")
        if nutrient:
            notes.append(f"비타민·미네랄·아미노산 성분({', '.join(self.ko(g) for g in nutrient[:8])}"
                         f"{' 외' if len(nutrient) > 8 else ''})은 AI 부작용 예측에서 제외하고 규칙(중복·용량·상호작용)으로만 확인했습니다.")
        if self.patient_model is not None and mols:
            V = mols[:MAX_DRUGS]
            emb, ix = self._emb(V, "patient", self.patient_model, self.pat_emb)
            n = len(V)
            fl = pa["flags"]

            def pv(nd: int, ref: bool = False) -> np.ndarray:
                if ref:
                    return patient_vector(REFERENCE_PATIENT["age"], patient.sex, REFERENCE_PATIENT["wt"], {}, nd)
                return patient_vector(patient.age, patient.sex, patient.weight_kg, fl, nd)

            sets: list[tuple[list[int], np.ndarray]] = [(ix, pv(n)), (ix, pv(n, True))]
            if n >= 2:
                sets += [([x for k, x in enumerate(ix) if k != d], pv(n - 1)) for d in range(n)]
            sets += [([x], pv(1)) for x in ix]
            didx = torch.full((len(sets), MAX_DRUGS), -1, dtype=torch.long)
            for r, (s, _) in enumerate(sets):
                didx[r, :len(s)] = torch.tensor(s)
            X = torch.tensor(np.stack([x for _, x in sets]), dtype=torch.float32)
            with torch.no_grad():
                P = torch.sigmoid(self.patient_model(emb, didx.to(self.dev), X.to(self.dev))).cpu().numpy()
            p_full, p_ref = P[0], P[1]
            p_loo = P[2:2 + n] if n >= 2 else None
            p_single = P[-n:]
            for c in range(self.C):
                p, prev = float(p_full[c]), float(self.prev[c])
                ev = {"id": c, "condition": cm.condition[c], "ko": cm.condition_ko[c], "organ": cm.organ[c],
                      "serious": bool(self.serious[c]), "prob": p, "baseline": prev, "lift": p / max(prev, 1e-6),
                      "patient_factor": p / max(float(p_ref[c]), 1e-6),
                      "best_single": float(p_single[:, c].max()),
                      "combo_excess": p - float(p_single[:, c].max())}
                drv = V[int((p - p_loo[:, c]).argmax())] if p_loo is not None else V[0]  # one drug: that drug
                ev["driver"] = self.ko(drv)
                # the same drug-event signal in OFFSIDES (single-drug, propensity-matched): a confirmed own risk -
                # unless the event is what the drug is given for (indication bias: cetirizine -> angioedema)
                rx = EVENT_INDICATION.get(cm.condition[c])
                given_for = self.indications.get(drv.key) or self.indications.get(drv.bare) or ""
                ev["indication"] = bool(rx and given_for and rx.search(given_for))
                ev["own_signal"] = (drv.bare, cm.condition[c]) in self.offsides and not ev["indication"]
                # what ANY medicine of the regimen is given for (warfarin -> stroke): reports of it come from the
                # illness being treated, so it informs but does not drive the verdict
                ev["indication_any"] = bool(rx) and any(
                    rx.search(self.indications.get(g.key) or self.indications.get(g.bare) or "") for g in V)
                events.append(ev)
            if p_loo is not None:
                for d in range(n):
                    delta = p_full - p_loo[d]
                    top = np.argsort(-delta)[:3]
                    drug_contrib.append({"drug": self.ko(V[d]),
                                         "top": [{"ko": cm.condition_ko[c], "delta": float(delta[c])} for c in top]})

        # ---- B + C. pairs
        pair_rows = []
        pair_probs = {}
        mol_pos = [i for i, g in enumerate(U) if g in mols]
        if self.pair_models and len(mol_pos) >= 2:
            combos = list(itertools.combinations(mol_pos, 2))
            at = {u: k for k, u in enumerate(mol_pos)}
            out = {}
            for kind, m, base in self.pair_models:
                emb, ix = self._emb([U[i] for i in mol_pos], kind, m, base)
                I = torch.tensor([ix[at[a]] for a, _ in combos], device=self.dev)
                J = torch.tensor([ix[at[b]] for _, b in combos], device=self.dev)
                with torch.no_grad():
                    out[kind] = torch.sigmoid(m(emb, I, J)).cpu().numpy()
            for k, (a, b) in enumerate(combos):
                novel = not (self._seen(U[a]) and self._seen(U[b]))
                if novel and "gnn" in out and self.pair_thr_unseen is not None:
                    pair_probs[(a, b)] = (out["gnn"][k], self.pair_thr_unseen, "GNN (신규 구조)")
                else:
                    pair_probs[(a, b)] = (np.mean([v[k] for v in out.values()], axis=0), self.pair_thr,
                                          "GNN+지문 앙상블")
        same_product = 0
        intended_pairs: list[str] = []
        for a, b in itertools.combinations(range(len(U)), 2):
            A, B = U[a], U[b]
            pa_, pb_ = prods_of[A.key], prods_of[B.key]
            if len(pa_) == 1 and pa_ == pb_ and len(products) > 1:
                same_product += 1  # components of one marketed combination product are combined by design
                continue
            obs = self.observed.get(frozenset((A.idx, B.idx)), []) if A.idx is not None and B.idx is not None else []
            # two vitamins / minerals / nutrients: labels list them side by side ("thiamine, niacinamide and ascorbic
            # acid decrease ...") but they do not interact with each other
            lev = [] if (_minor(A) and _minor(B)) or _diluent_pair(A, B) else self._label_evidence(A, B)
            model, model_kind = [], None
            if (a, b) in pair_probs:
                pp, thr, model_kind = pair_probs[(a, b)]
                for c in np.argsort(-pp / thr):
                    if pp[c] >= thr[c]:
                        model.append({"ko": cm.condition_ko[c], "organ": cm.organ[c], "prob": float(pp[c]),
                                      "threshold": float(thr[c]), "serious": bool(self.serious[c]),
                                      "lift": float(pp[c] / max(self.pair_prev[c], 1e-6))})
            obs_ev = [{"ko": cm.condition_ko[c], "organ": cm.organ[c], "serious": bool(self.serious[c])} for c in obs]
            # Documented interaction = caution by default; "high" only when the label itself uses strong wording
            # (contraindicated / avoid / reduce the dose / fatal ...) or lists it as a contraindication / boxed
            # warning. Report signals and model flags alone can raise "caution", never "high".
            intended = frozenset((A.bare, B.bare)) in self.combo_pairs  # sold together in one product
            for e in lev:
                e["context"] = _context(e)  # shown in the details: 'no interaction' / co-therapy mentions
            own = _own(lev)
            contra = any(_is_contra(e) for e in own)
            # strong wording counts only in interaction-specific sections (general warnings mention many drugs)
            # and only in the sentences that name the other drug
            # "avoid LARGE quantities of grapefruit juice", "avoid DRASTIC changes in vitamin K intake": an amount /
            # consistency caution, not a contraindication; "[see Contraindications (4)]" is a cross-reference
            strong = any(_is_strong(e) for e in own)
            mech = self._mechanism(A, B) + self._kr_label(A, B, kr_avoid)
            if intended:  # the pharmacology of an approved combination is its purpose (cobicistat boosts darunavir);
                mech = [m for m in mech if m["kind"] not in ("CYP-FDA", "CYP", "QT", "PD")]  # curated rules still apply
                intended_pairs.append(f"{self.ko(A)} + {self.ko(B)}")
            # Pair-level statistics (TWOSIDES signals, pair model) are shown as information only: they are confounded
            # by indication (antihistamine + angioedema) and the pair model learned TWOSIDES, so their agreement is
            # not independent. Statistical evidence reaches the verdict through the patient model's ADDED risk.
            dur = self.dur_pairs.get(frozenset((A.bare, B.bare)))  # official Korean contraindicated combination
            if dur or contra or strong or any(m.get("severe") for m in mech):  # FDA strong inhibitor x sensitive substrate
                level = "높음"
            elif own or mech:
                level = "주의"
            else:
                level = "낮음"
            # an approved combination product contains both (aspirin + clopidogrel, olanzapine + fluoxetine): label
            # wording about the pair is about using them together as intended - at most 'caution' (its real risks,
            # e.g. bleeding, stay visible), unless the official Korean DUR or a curated rule says otherwise
            if (intended and level == "높음" and not dur
                    and not any(m.get("severe") and m["kind"] == "규칙" for m in mech)):
                level = "주의"
            pair_rows.append({"a": self.ko(A), "b": self.ko(B), "a_key": A.key, "b_key": B.key,
                              "novel": any(g.idx is None and not g.biologic for g in (A, B)),
                              "biologic": A.biologic or B.biologic, "level": level, "model_kind": model_kind,
                              "label_evidence": lev, "observed": obs_ev, "model": model[:10], "mechanism": mech,
                              "dur": ({"content": dur.get("content"), "date": dur.get("date")} if dur else None)})

        # ---- organ summary + overall
        # FAERS base rates per event are 0.1-3%, so "elevated" is judged by lift over the base rate with a
        # small absolute floor (MIN_P) that keeps vanishingly rare events from dominating.
        # An organ is "high" only when a serious event is clearly elevated AND that elevation comes from the
        # combination or from this patient (not merely from one drug's inherent profile, e.g. warfarin and
        # bleeding - that alone is "caution").
        organs = {}
        for o, oname in ORGANS.items():
            evs = [e for e in events if e["organ"] == o]
            hits = sorted([e for e in evs if e["lift"] >= 1.5 and e["prob"] >= MIN_P], key=lambda e: -e["lift"])
            pair_hits = sorted({m["ko"] for r in pair_rows for m in r["model"] + r["observed"]
                                if m["organ"] == o and m["serious"]})
            high = any(e["serious"] and e["lift"] >= 3 and e["prob"] >= 2 * MIN_P and added_risk(e) >= 1.5
                       for e in evs)
            level = "high" if high else "caution" if (hits or pair_hits) else "normal"
            organs[o] = {"name": oname, "level": level,
                         "max_prob": max((e["prob"] for e in evs), default=0.0),
                         "max_lift": max((e["lift"] for e in evs if e["prob"] >= MIN_P), default=0.0),
                         "events": [{"ko": e["ko"], "prob": e["prob"], "lift": e["lift"]} for e in hits[:5]],
                         "pair_signals": pair_hits[:6]}
        # Decision rule (documented in docs/MODEL_CARD.md): "높음/위험" requires documented or rule-based
        # evidence - label contraindication / major-interaction wording, duplicated ingredient, severe class
        # duplication, drug-disease contraindication. Statistical (AI) signals alone can raise "주의" only.
        rank = {"높음": 2, "주의": 1, "낮음": 0}
        overall = max([r["level"] for r in pair_rows] or ["낮음"], key=rank.get)
        # An AI organ signal raises the verdict only when the risk is ADDED by the combination or by this patient
        # (a drug's own side-effect profile - cetirizine and drowsiness - is shown as information, not as an
        # interaction warning; otherwise nearly every combination would be '주의' and alerts would be ignored).
        # ... and only when it is not vanishingly rare (>= 1% of comparable reports): 0.4% jaundice for caffeine +
        # paracetamol reflects overdoses of combination tablets in FAERS, not normal use
        # ... and the combination effect must be strong (>= 3x the riskier drug alone): weaker co-prescription
        # signals are confounded (metformin + atorvastatin 'jaundice' 1.7x in sicker patients), while true
        # interactions are far above (sertraline + tramadol serotonin syndrome 12.6x).
        # v2.18: a signal that comes only from THIS PATIENT (age, body - 'falls' at 65) is not an interaction and no
        # longer raises the verdict on its own - on real FAERS regimens it was the only reason for 1 in 4 '주의'
        # (alert fatigue). Validated patient risks (Beers, kidney dosing, pregnancy, DUR age, disease rules) are
        # documented rules and still decide; the AI patient signal is shown as information ('알아두기').
        for e in events:
            e["drives_verdict"] = bool(e["serious"] and e["lift"] >= 1.5 and e["prob"] >= SIGNAL_P
                                       and _combo(e) >= COMBO_X and not e.get("indication_any"))
            e["patient_note"] = bool(e["serious"] and e["lift"] >= 1.5 and e["prob"] >= SIGNAL_P
                                     and not e["drives_verdict"] and e["patient_factor"] >= 1.5
                                     and not e.get("indication_any"))
        added_signal = any(e["drives_verdict"] for e in events)
        if any(not d["minor"] for d in duplicates):
            overall = "높음"
        elif overall == "낮음" and (added_signal or duplicates):
            overall = "주의"
        severe_classes = {DRUG_CLASSES[c][0] for c in ("nsaid", "opioid", "sedative", "anticoagulant", "pde5")}
        for d in class_dups:
            d["severe"] = (d["class"] in severe_classes or d.get("dur_series", False)) and not d.get("intended")
        if any(d["severe"] for d in class_dups):
            overall = "높음"
        elif class_dups and overall == "낮음":
            overall = "주의"
        if same_product:
            notes.append(f"같은 복합제 안에 함께 배합된 성분끼리의 조합 {same_product}쌍은 의도된 배합이므로 상호작용 평가에서 제외했습니다.")
        if intended_pairs:
            notes.append(f"{', '.join(intended_pairs[:3])}{' 등' if len(intended_pairs) > 3 else ''}은(는) 함께 쓰도록 허가된 "
                         "복합제에 들어 있는 조합입니다 — 처방받아 함께 쓰는 것이라면 정상적인 병용이며, 남은 주의점만 표시했습니다.")

        # the products each ingredient comes in (DUR age rules can be limited to a dosage form: '서방정')
        forms: dict[str, list[tuple[str | None, str]]] = {}
        for p in products:
            for g in p["ingredients"]:
                forms.setdefault(g.key, []).append((p.get("product"), p.get("release") or "IR"))
        disease_alerts = self._drug_disease(U, pa, patient, forms)
        # Korean product label (의약외품): '15세 미만은 복용하지 않는다' -> this product is not for this child
        for p in products:
            n = (p.get("label_ko") or {}).get("min_age")
            if n and patient.age is not None and patient.age < n:
                disease_alerts.append({
                    "key": "age_label", "disease": f"{n}세 미만", "drug": p.get("product") or p["input"],
                    "severity": "금기", "reason": f"제품 설명서(식약처 허가사항)에 {n}세 미만은 복용하지 않도록 되어 있습니다",
                    "source": "식약처 허가사항", "evidence": (p.get("label_ko") or {}).get("usage")})
        if any(d["severity"] == "금기" for d in disease_alerts):
            overall = "높음"
        elif disease_alerts and overall == "낮음":
            overall = "주의"

        # dose & duration (only for products the user gave a dose for)
        kid = pa.get("kidney") or {}
        ctx = {"crcl": kid.get("clearance"), "age": patient.age, "sex": (patient.sex or "").upper()[:1] or None,
               "weight": patient.weight_kg, "scr": patient.scr, "hepatic": bool(pa["flags"].get("hepatic")),
               "all_keys": [g.key for g in U]}
        dose_items = [{"input": p["input"], "keys": [g.key for g in p["ingredients"]], "dose": p.get("dose"),
                       "strength": p.get("strength"), "release": p["release"]} for p in products]
        dosing = check_doses(dose_items, ctx, drug_groups, lambda k: self.ko(self._ext_ingredient(k))
                             if (k in self.name_idx or k in self.ext_ing or k.startswith(("bio:", "lab:")))
                             else k.removeprefix("ing:"))
        if any(a["sev"] == "금기" for a in dosing["alerts"]):
            overall = "높음"
        elif dosing["alerts"] and overall == "낮음":
            overall = "주의"
        prn_keys = {g.key for p in products if p.get("dose") is not None and p["dose"].prn for g in p["ingredients"]}
        # recreational / controlled substances (recreational.py): the advice never suggests an amount for them
        ctl_keys = {g.key for p in products if p.get("controlled") for g in p["ingredients"]}

        top = sorted([e for e in events if e["serious"] and e["prob"] >= MIN_P and e["lift"] >= 1.5],
                     key=lambda e: -(e["lift"] * np.sqrt(e["prob"])))[:2]
        headline = None
        if top:
            t = top[0]
            organs_named = list(dict.fromkeys(ORGANS[e["organ"]] for e in top))
            headline = {"title": " · ".join(e["ko"] for e in top) + " 위험",
                        "organs": organs_named, "prob": t["prob"], "lift": t["lift"],
                        "patient_factor": t["patient_factor"], "event": t["ko"]}

        alerts = list(pa["alerts"])
        for d in duplicates:
            alerts.insert(0, f"동일 성분 중복: {d['ingredient']} ({' / '.join(d['products'])}) — 과량 투여 위험")
        for d in class_dups:
            alerts.insert(0, f"같은 계열 중복 [{d['class']}]: {', '.join(d['ingredients'])} "
                             f"({' / '.join(d['products'])}) — 효과·부작용이 겹칩니다")
        for e in events:
            e["added"] = added_risk(e)
            # plain word = how much the combination / this patient ADDS; a drug's inherent risk alone is "약 자체 위험";
            # the illness a medicine of the regimen is given for (warfarin -> stroke) is neither
            if e.get("indication_any"):
                e["level_word"] = "치료 중인 질환"
            elif e["lift"] >= 3 and e["added"] >= 1.5:
                e["level_word"] = "매우 높음"
            elif e["lift"] >= 2 and e["added"] >= 1.25:
                e["level_word"] = "높음"
            elif e["lift"] >= 1.5 and e["added"] < 1.25:
                e["level_word"] = "약 자체 위험"
            elif e["lift"] >= 1.3:
                e["level_word"] = "약간 높음"
            else:
                e["level_word"] = "보통"
            if not e["serious"] and e["level_word"] in ("매우 높음", "높음"):
                e["level_word"] = "약간 높음"  # nausea, headache ...: worth knowing, not a red alarm
        events = sorted(events, key=lambda e: -e["lift"] * np.sqrt(e["prob"]) * (1.5 if e["serious"] else 1))
        summary = self._summary(overall, duplicates, class_dups, disease_alerts, pair_rows, events, pa,
                                dosing, prn_keys, ctl_keys)
        special = [p["input"] for p in products if p["release"] in ("ER", "DR")]
        if special:  # crushing / chewing an extended-release tablet releases the whole dose at once (dose dumping)
            forms = "/".join(sorted({RELEASE_KO[p["release"]] for p in products if p["input"] in special}))
            summary["advice"].append(f"{', '.join(special)}은(는) {forms} 제제입니다 — 쪼개거나 씹거나 부수지 말고 통째로 삼키세요.")
        try:  # the animated body view: same evidence as above, laid out along the drug's path through the body
            pathway = build_journey(self, U, pair_rows, _about, CYP_INHIBITION_P, prods_of=prods_of, events=events,
                                    disease_alerts=disease_alerts, class_dups=class_dups, duplicates=duplicates,
                                    dosing=dosing, pa=pa, overall=overall, patient=patient, products=products)
        except Exception:  # a display aid must never break the analysis itself
            log.exception("journey")
            pathway = None
        return {
            "pathway": pathway,
            "class_duplicates": class_dups, "disease_alerts": disease_alerts, "summary": summary, "dosing": dosing,
            "overall_level": overall, "headline": headline,
            "ingredients": [{"key": g.key, "display": self.ko(g), "novel": g.idx is None and not g.biologic,
                             "biologic": g.biologic} for g in U],
            "patient": {k: pa[k] for k in ("kidney", "liver", "flags", "body", "capacity", "diseases")},
            "alerts": alerts, "notes": notes,
            "duplicates": duplicates, "events": events,
            "drug_contrib": drug_contrib, "pairs": sorted(pair_rows, key=lambda r: -rank[r["level"]]),
            "organs": organs,
            "models": {"patient": self.patient_model is not None, "pair": self.pair_model},
        }

    # ------------------------------------------------------------------ catalog
    def catalog(self) -> dict:
        supported = set(self.name_idx)
        ok = lambda v: (DRUG_SYNONYMS.get(v, v) in supported or f"ing:{v}" in self.ext_ing  # noqa: E731
                        or v in BIOLOGICS)
        # supplements / herbs have no structure (lab: keys) but are analysed by labels and rules - list them too
        lab_ok = lambda v: structureless_category(v) != "기타" or len(drug_groups(v)) > 1  # noqa: E731
        ko_ing = sorted(k for k, v in KR_INGREDIENTS.items() if ok(v) or lab_ok(v))
        ko_brand = sorted(k for k, v in KR_BRANDS.items() if all(ok(x) for x in v))
        return {"korean_brands": ko_brand, "korean_ingredients": ko_ing, "ingredients": sorted(supported),
                "brands": sorted(b for b in self.brand if len(b) <= 40)}

    def status(self) -> dict:
        pm, tm = self.pair_metrics or {}, self.patient_metrics or {}
        return {"n_drugs": len(self.master), "n_events": self.C, "n_fda_labels": len(self.labels),
                "n_products": {c: int(n) for c, n in self.products["country"].value_counts().items()},
                "n_brands": len(self.brand), "n_observed_pairs": len(self.observed),
                "pair_model": self.pair_model, "patient_model": self.patient_model is not None,
                "commercial_safe": settings.commercial_safe, "n_dur_pairs": len(self.dur_pairs),
                "n_reports": tm.get("n_reports"), "trained_at": tm.get("trained_at") or pm.get("trained_at"),
                "device": str(self.dev), "comorbidities": COMORBIDITIES}
