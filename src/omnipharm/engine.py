"""OMNI-PHARM inference engine.

For a regimen (2+ drugs, given by name, Korean/English brand, ingredient list or raw SMILES) and a
patient, combine three independent kinds of evidence, never mixing them up:

  A. Patient-aware regimen model (FAERS, millions of reports): P(event | these drugs, this patient)
     -> also: reference-patient comparison (patient effect), leave-one-out (which drug drives it),
        single-drug baselines (does the combination add risk beyond either drug alone).
  B. Pair structure model (TWOSIDES, GNN + fingerprint ensemble): P(pair shows a significant
     co-reporting signal for event) -> works for molecules never seen before (custom SMILES).
  C. Documented evidence: FDA label interaction / contraindication text, observed TWOSIDES signal.
"""

from __future__ import annotations

import itertools
import json
import logging
import re
import threading
import unicodedata
from dataclasses import dataclass

import numpy as np
import pandas as pd
import torch

from omnipharm.chem.conformer import mol_3d
from omnipharm.chem.featurize import MoleculeError, smiles_to_graph, standardize
from omnipharm.clinical import Patient, assess
from omnipharm.config import settings
from omnipharm.dosing import check_doses
from omnipharm.journey import build_journey
from omnipharm.knowledge import (
    BIOLOGICS,
    COMORBIDITIES,
    CYP_FDA,
    DISEASE_LABEL_TERMS,
    DISEASES,
    DRUG_CLASSES,
    DRUG_DISEASE_RULES,
    DRUG_SYNONYMS,
    FOODS,
    KR_BRANDS,
    KR_INGREDIENTS,
    ORGANS,
    PAIR_RULES,
    PRODRUGS_ACTIVATED_BY,
    SERIOUS_CONDITIONS,
    bio_key,
    class_phrases,
    drug_class,
    drug_groups,
    food_key,
    lab_name,
    normalize_ingredient,
    resolve_korean,
    structureless_category,
)
from omnipharm.models.gnn import FingerprintEncoder, OmniPharmModel, RegimenPatientModel
from omnipharm.models.patient import MAX_DRUGS, PATIENT_DIM, patient_vector
from omnipharm.release import RELEASE_KO, release_of

log = logging.getLogger(__name__)
KO_NAME: dict[str, str] = {}
for _k, _v in KR_INGREDIENTS.items():
    KO_NAME.setdefault(DRUG_SYNONYMS.get(_v, _v), _k)  # first (most common) Korean name wins
REFERENCE_PATIENT = {"age": 45.0, "wt": 70.0}
MIN_P = 0.003  # absolute probability floor for calling an event "elevated"
SIGNAL_P = 0.01  # an AI signal must reach this (1% of comparable reports) to change the verdict
COMBO_X = 3.0  # ... and be this many times above the riskier single drug (unless the patient explains it)


def _combo(e: dict) -> float:
    return e["prob"] / max(e["best_single"], 1e-6)
ALLERGY_CONTEXT = re.compile(r"asthma|urticaria|allerg|hypersensitiv|anaphyla|bronchospasm", re.I)
ASK_DOCTOR = re.compile(r"ask a (doctor|pharmacist)", re.I)
CYP_INHIBITION_P = 6.0 # pChEMBL >= 6 (IC50/Ki <= 1 uM in vitro) counts as a CYP inhibitor
HERG_P = 5.5  # hERG IC50 <= ~3 uM
# label wording that makes a documented interaction clinically major
SEVERE_LABEL = re.compile(
    r"contraindicat|\bavoid\b|do not (use|coadminister|co-administer|administer|take)|not recommended|"
    r"fatal|life[- ]threatening|reduce the (dose|dosage)|dose reduction|decrease the dose|"
    r"should not be (used|given|coadministered)", re.I)
# a boxed warning ABOUT taking the two together (opioid + benzodiazepine), not one that merely names the other drug
# as background ("patients taking concomitant immunosuppressants such as methotrexate")
BOXED_COMBINATION = re.compile(r"concomitant use|co-?administration|in combination with|used together", re.I)


def added_risk(e: dict) -> float:
    """How much of an event's risk is *added* by combining the drugs or by this patient's condition,
    as opposed to the inherent risk of a drug taken alone by an average patient."""
    combo = e["prob"] / max(e["best_single"], 1e-6)
    return max(combo, e["patient_factor"])


class ResolveError(ValueError):
    def __init__(self, message: str, candidates: list[dict] | None = None) -> None:
        super().__init__(message)
        self.candidates = candidates or []


@dataclass
class Ingredient:
    key: str  # drug name in the DB, "ing:<name>" untrained molecule, "bio:<name>" peptide/protein, "smiles:<inchikey>"
    name: str  # display name
    smiles: str  # "" for biologics (no structure model)
    idx: int | None  # row in drug_master, None for custom molecules
    rxcui: str | None = None

    @property
    def biologic(self) -> bool:
        """No small-molecule structure: curated biologic ("bio:"), label/rule-only ingredient ("lab:") or
        food / drink / smoking ("food:")."""
        return self.key.startswith(("bio:", "lab:", "food:"))

    @property
    def food(self) -> bool:
        return self.key.startswith("food:")

    @property
    def bare(self) -> str:
        """Plain ingredient name (no prefix) as used by labels and rules."""
        return self.key.removeprefix("ing:").removeprefix("bio:").removeprefix("lab:").removeprefix("food:")


def _about(e: dict, lead: int = 0) -> str:
    """The sentences of a label excerpt that name the other drug (an excerpt may also cover unrelated drugs);
    lead = also include that many sentences before each one (table rows: 'Clinical Impact: ...  Examples: X')."""
    terms = [t.lower() for t in (e.get("terms") or BIOLOGICS.get(e["mentions"], {}).get("terms", [e["mentions"]]))]
    # whole name only, and not part of another product's name ("sacubitril/valsartan" is not valsartan)
    pats = [re.compile(r"(?<![\w/])" + re.escape(t) + r"(?![\w/])", re.I) for t in terms]
    sents = re.split(r"(?<=[.;!?])\s+|\s+[●•]\s*|\s+\(\s*\d", e["excerpt"])
    keep = set()
    for i, s in enumerate(sents):
        if any(p.search(s) for p in pats):
            keep.update(range(max(0, i - lead), i + 1))
    return " ".join(sents[i] for i in sorted(keep))


def _age_hit(base: str | None, age: float) -> bool:
    """DUR age bases: '18세 이하', '만 12세 미만', '6개월 미만', '65세 이상'."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(세|개월)\s*(이하|미만|이상|초과)", base or "")
    if not m:
        return False
    v = float(m.group(1)) / (12 if m.group(2) == "개월" else 1)
    return {"이하": age <= v, "미만": age < v, "이상": age >= v, "초과": age > v}[m.group(3)]


# Sentences that name the other drug but are NOT an interaction warning
NO_INTERACTION = re.compile(
    r"no clinically (significant|relevant|meaningful|important)|without clinically (significant|relevant|meaningful)|"
    r"not clinically (significant|relevant|meaningful)|no (dose|dosage|dosing) adjustments? (is|are|was|were)? ?"
    r"(needed|necessary|required|recommended)|did not (alter|affect|change|influence)|had no (clinically )?"
    r"(significant |relevant )?effect|no (significant|meaningful|relevant) (effect|change|interaction|alteration)|"
    r"no (pharmacokinetic|clinically) interaction|not (been )?(found|shown|observed) to (interact|affect|alter)|"
    r"(did|does|do) not result in (any |a )?clinically (significant|relevant|meaningful)|"
    r"(does|do|did) not (alter|affect|change|influence) the (exposure|pharmacokinetics|plasma)|"
    r"no (clinically )?(meaningful|relevant|significant|important) (drug-drug |drug )?(pharmacokinetic |pk )?"
    r"(drug )?interactions?", re.I)
# an interaction-section mention must say SOMETHING about an effect (table headings / bare lists do not)
EFFECT_WORDS = re.compile(
    r"increas|decreas|reduc|elevat|lower|rais|risk|monitor|avoid|caution|concomitant|co-?administ|potentiat|"
    r"inhibit|induc|level|concentration|exposure|effect|adjust|toxicit|may |contraindicat|bleeding|"
    r"hypoglyc|hyperkal|serotonin|qt|sedation|interact|serious|fatal|life[- ]threatening|potential for", re.I)
AMOUNT_ONLY = re.compile(r"large (quantities|amounts|volumes)|excessive( \w+){0,2} (consumption|intake|amounts|use|drinking)|"
                         r"binge drinking|heavy (drinking|alcohol use)|risk factors (for [\w\s-]{0,60})?include|"
                         r"more than [\d.,]+ ?(l|liters?|litres?|quarts?|cups?|glasses|drinks)\b|"
                         r"(drastic|sudden|major) changes|consistent (intake|amount|diet)", re.I)
XREF = re.compile(r"for (more |additional |complete |further )?information (about|on|regarding|concerning)", re.I)
STRONG_WORDS = re.compile(r"contraindicat|\bavoid|do not|must not|should not|life[- ]threatening|fatal", re.I)
CO_THERAPY = re.compile(
    r"indicated (for use )?in combination with|combination therapy with|triple therapy|dual therapy|"
    r"refer to the .{0,60}(package inserts?|prescribing information|contraindications section|labeling)|"
    r"must also (provide|prescribe)|for (the )?treatment of acute symptoms|should be treated with|"
    r"as part of (a |an )?(combination|regimen)|inactive ingredients|other information|"
    r"with or without|in (a|the|one) (large )?(clinical |randomized |controlled )*(trial|study) .{0,60}"
    r"(involving|enrolled|in patients|of patients)", re.I)  # trial-population descriptions
CONTRA_VERB = re.compile(
    r"contraindicat|do not|should not|must not|avoid|not (be )?(used|given|administered|co-?administered|taken|"
    r"combined|started)|concomitant|co-?administ|in patients (taking|receiving|treated with)|taking|receiving|"
    r"within \d+ (hours|days|weeks)", re.I)
INTERACTION_SECTIONS = ("상호작용", "병용금기/금기", "박스 경고", "일반약: 복용 전 상담")


def _context(e: dict) -> str | None:
    """'no_interaction' / 'co_therapy' when the sentences naming the other drug say so."""
    about = _about(e)
    if XREF.search(about):
        return "co_therapy"  # "For information about contraindications of amoxicillin ... refer to its label"
    terms = [t for t in (e.get("terms") or BIOLOGICS.get(e["mentions"], {}).get("terms", [e["mentions"]]))]
    if any(re.search(r"(combination|triple|dual) therapy with [^.:;]{0,80}?\b" + re.escape(t) + r"\b", about, re.I)
           for t in terms):
        return "co_therapy"  # H. pylori "Combination Therapy with Clarithromycin and Amoxicillin" headings
    if STRONG_WORDS.search(about):
        return None  # "Contraindicated ... refer to the venetoclax prescribing information" is a real warning
    if NO_INTERACTION.search(about):
        return "no_interaction"
    if not EFFECT_WORDS.search(about) and NO_INTERACTION.search(_about(e, lead=2)):
        return "no_interaction"  # a bare table row under "No dosing adjustments required for the following:"
    if CO_THERAPY.search(about):
        return "co_therapy"
    return None


def _own(lev: list[dict]) -> list[dict]:
    """Label evidence that is really an interaction statement for this pair: interaction / contraindication /
    boxed / OTC 'ask a doctor' sections (general warnings mention many drugs in passing), from a single-ingredient
    label or a combination label whose sentence names BOTH drugs, and not a 'no interaction' or co-therapy sentence."""
    out = []
    for e in lev:
        if e["section"] not in INTERACTION_SECTIONS:
            continue
        if e.get("combo"):
            about = _about(e).lower()
            name = re.escape(e["label_of"].lower())
            mentions = re.findall(r"\b" + name + r"\b", about)
            as_product = re.findall(r"\b" + name + r"\b[\w\s,/-]{0,80}?\b(tablets?|capsules?|injection|oral solution|"
                                    r"suspension|syrup|extended-release)\b", about)
            if not mentions or len(as_product) >= len(mentions):
                continue  # the combination label's warning concerns its OTHER ingredient (codeine, not paracetamol)
        if structureless_category(e["label_of"]) == "비타민·미네랄·영양":
            continue  # a vitamin product's own label (IV admixture notes, vague claims); drug labels and the
            #           curated rules (calcium/iron chelation, potassium ...) still apply
        if _context(e):
            continue
        about = _about(e)
        if not about:
            continue  # the name only occurs inside another product's name
        if e["section"] == "병용금기/금기" and not (CONTRA_VERB.search(about)
                                                     or EFFECT_WORDS.search(_about(e, lead=1))):
            continue  # "ketoacidosis should be treated with insulin" in a contraindication section
        if e["section"] == "상호작용" and not EFFECT_WORDS.search(_about(e, lead=2)):
            continue  # a table heading / bare list ("Oral Antidiabetic Agents Metformin") with no effect stated
        out.append(e)
    return out


def _minor(g: Ingredient) -> bool:
    """Vitamins, minerals, amino acids, nutrients and caffeine: low-toxicity ingredients of tonics / supplements."""
    return g.bare == "caffeine" or structureless_category(g.bare) == "비타민·미네랄·영양"


def lab_key(raw: str) -> str | None:
    """'Infliximab' -> 'lab:infliximab', 'Ginkgo Biloba Leaf Dried Extract' -> 'lab:ginkgo biloba leaf extract'."""
    s = lab_name(raw)
    if not s:
        return None
    b = bio_key(s)  # a curated biologic keeps its rules ("Adalimumab" -> bio:adalimumab)
    return f"bio:{b}" if b else f"lab:{s}"


def script(text: str) -> str:
    """'KR' (Hangul), 'JP' (kana / CJK ideographs) or 'LATIN' - selects which national product DB to search."""
    if re.search(r"[가-힣ㄱ-ㆎ]", text):
        return "KR"
    if re.search(r"[぀-ヿ一-鿿ｦ-ﾟ]", text):
        return "JP"
    return "LATIN"


def _key(name: str) -> str:
    return unicodedata.normalize("NFKC", name or "").lower().replace(" ", "")


def _load_json(path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


class Engine:
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
        if (p / "kr_quasi_products.parquet").exists():  # 의약외품 (stage "dur")
            parts.append(pd.read_parquet(p / "kr_quasi_products.parquet").assign(country="KR"))
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

    # ------------------------------------------------------------------ resolution
    def _db(self, i: int, display: str | None = None) -> Ingredient:
        r = self.master.iloc[i]
        return Ingredient(r["name"], display or r["name"], r["std_smiles"], i, r["rxcui"])

    def _seen(self, g: Ingredient) -> bool:
        """Was this molecule part of the pair model's training pairs?"""
        return g.idx is not None and g.key in self.pair_seen

    def resolve(self, text: str) -> list[Ingredient]:
        return self.resolve_full(text)[0]

    def resolve_full(self, text: str) -> tuple[list[Ingredient], dict]:
        """Name / brand / Korean product / 'a+b' / SMILES -> (ingredients, info).
        info = {"product": 식약처 product name or None, "unresolved": [ingredient strings not analysable]}.
        Raises ResolveError (with candidates when a product name is ambiguous)."""
        text = (text or "").strip()
        if not text:
            raise ResolveError("빈 입력")
        info: dict = {"product": None, "country": None, "unresolved": [], "strength": None}
        fk = food_key(text)  # 술, 자몽주스, 우유, 흡연 ...
        if fk:
            return [self._ext_ingredient(f"food:{fk}")], info
        smiles_like = text.isascii() and " " not in text and any(ch in text for ch in "()=#[]@0123456789")
        if smiles_like:
            return [self._from_smiles(text)], info
        jan = self.jp_names.get(unicodedata.normalize("NFKC", text))  # Japanese ingredient name (JAN)
        if jan:
            try:
                return self._resolve_names(jan.lower()), info
            except ResolveError:
                pass
        try:
            return self._resolve_names(text), info
        except ResolveError as e:
            if script(text) == "KR":  # Korean ingredient name ('인플릭시맵', '제미글립틴타르타르산염')
                from omnipharm.pipeline.strengths import KO_SALT

                k = self.kr_names.get(KO_SALT.sub("", re.sub(r"^(dl|d|l)-", "", text.replace(" ", ""))))
                if k and (k in self.name_idx or k in self.ext_ing or k.startswith(("bio:", "lab:"))):
                    return [self._ext_ingredient(k)], info
            row = self._product_lookup(text)  # raises ResolveError with candidates if ambiguous
            if row is not None:
                ings = [self._ext_ingredient(k) for k in row["ingredients"]]
                return ings, {"product": row["name"], "country": row["country"], "release": release_of(row["name"]),
                              "unresolved": list(row["unresolved"]),
                              "strength": self.strengths.get((row["country"], str(row["item_seq"])))}
            b = bio_key(text)  # peptide / protein product (위고비프리필드펜, 보톡스주 ...)
            if b:
                return [self._bio(b)], {**info, "product": text if text.lower() != b else None}
            g = self._online(text)  # any other country: INN / brand synonym via PubChem (needs internet)
            if g is not None:
                return [g], {**info, "product": f"{text} (PubChem)"}
            raise e

    @staticmethod
    def _bio(name: str) -> Ingredient:
        return Ingredient(f"bio:{name}", name, "", None)

    def _ext_ingredient(self, key: str) -> Ingredient:
        if key in self.name_idx:
            return self._db(self.name_idx[key])
        if key.startswith("bio:"):
            return self._bio(key.removeprefix("bio:"))
        if key.startswith("food:"):
            return Ingredient(key, key.removeprefix("food:"), "", None)
        if key.startswith("lab:"):
            b = bio_key(key.removeprefix("lab:"))
            return self._bio(b) if b else Ingredient(key, key.removeprefix("lab:"), "", None)
        r = self.ext_ing[key]
        return Ingredient(key, r.name, r.std_smiles, None)

    def _candidates(self, rows: pd.DataFrame) -> list[dict]:
        rows = rows.sort_values(["active", "otc", "name"], ascending=[False, False, True]).head(12)
        return [{"name": r["name"], "kind": self._kind(r), "company": r["company"],
                 "ingredients": ", ".join(self._display_key(k) for k in r["ingredients"])} for _, r in rows.iterrows()]

    @staticmethod
    def _kind(r) -> str:
        if r["country"] == "KR":
            return "일반약" if r["otc"] else "전문약"
        return f"{r['country']} {'OTC' if r['otc'] else 'Rx' if r['kind'] else ''}".strip()

    def _product_lookup(self, text: str) -> pd.Series | None:
        """Marketed product name -> one row. Korean text searches Korean products, Latin text the US/CA/EU/FR
        ones. Several products whose composition differs -> ResolveError with candidates."""
        if not len(self.products):
            return None
        from omnipharm.pipeline import mfds, world

        P = self.products
        pool = self._pool(script(text))
        key = _key(text)
        exact = [i for i in self.prod_by_key.get(key, []) if i in pool.index]
        if exact:
            rows = P.loc[exact]
        else:
            sc = script(text)
            b = (mfds.base_name(text.replace(" ", "")) if sc == "KR" else
                 world.jp_base_name(text) if sc == "JP" else world.base_name(text))
            rows = pool[pool["base"] == b]
            if not len(rows):
                rows = pool[pool["base"].str.startswith(b) | pool["key"].str.startswith(key)]
            if not len(rows) and len(key) >= 3:  # company prefix: '정로환' -> '동성정로환에프정'
                rows = pool[pool["key"].str.contains(key, regex=False)]
        if not len(rows):
            return None
        if rows["active"].any():
            rows = rows[rows["active"]]
        comps = rows["ingredients"].map(lambda v: tuple(sorted(v)))
        if comps.nunique() == 1:
            # same composition, different products (타이레놀정500 / 타이레놀산500 / 타이레놀8시간이알서방정): the one whose
            # name is closest to what was typed - its strength per unit and release form are what the dose refers to
            import difflib

            rel = release_of(text)

            def score(r) -> float:
                k = r["key"]
                return ((k == key) * 100 + k.startswith(key) * 40 + (key in k) * 20 + (release_of(r["name"]) == rel) * 15
                        + difflib.SequenceMatcher(None, key, k).ratio() * 10 + bool(r["otc"]))
            return rows.loc[max(rows.index, key=lambda i: score(rows.loc[i]))]
        raise ResolveError(f"'{text}'에 해당하는 제품이 {len(rows)}개이며 성분이 서로 다릅니다 — 정확한 제품을 선택하세요.",
                           self._candidates(rows))

    def _pool(self, sc: str) -> pd.DataFrame:
        P = self.products
        if sc in ("KR", "JP"):
            return P[P["country"].eq(sc)]
        return P[~P["country"].isin(["KR", "JP"])]

    def _online(self, text: str) -> Ingredient | None:
        """Last resort for drugs of countries without a bulk database: PubChem name/synonym lookup (cached)."""
        if not settings.online_lookup or not text.isascii() or len(text) > 60 or len(text.split()) > 4:
            return None
        name = text.lower().strip()
        if name not in self._online_cache:
            import urllib.parse

            import httpx

            url = ("https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
                   f"{urllib.parse.quote(name)}/property/IsomericSMILES,SMILES/JSON")
            try:
                r = httpx.get(url, timeout=5.0)
            except Exception:  # offline / slow network: just say we do not know it
                return None
            if r.status_code in (400, 404):
                res = None
            elif r.status_code == 200:
                p = r.json()["PropertyTable"]["Properties"][0]
                smi = p.get("IsomericSMILES") or p.get("SMILES")
                res = {"smiles": smi} if smi else None
            else:
                return None  # transient error: do not cache
            with self._online_lock:
                self._online_cache[name] = res
                try:
                    self._online_cache_f.write_text(json.dumps(self._online_cache), encoding="utf-8")
                except OSError:
                    pass
        info = self._online_cache.get(name)
        if not info:
            return None
        try:
            std = standardize(info["smiles"])
        except MoleculeError:
            return None
        if std.inchikey in self.inchikey_idx:
            return self._db(self.inchikey_idx[std.inchikey])
        if std.mol.GetNumHeavyAtoms() > 100:
            return None
        return Ingredient(f"ing:{name}", name, std.canonical_smiles, None)

    def _display_key(self, key: str) -> str:
        bare = key.removeprefix("ing:").removeprefix("bio:")
        return KO_NAME.get(key) or KO_NAME.get(bare) or bare

    def _resolve_names(self, text: str) -> list[Ingredient]:
        """Curated Korean names, 'a+b' ingredient lists, English generic/brand names."""
        names = resolve_korean(text) or []
        out, missing = [], []
        for n in names:
            n = DRUG_SYNONYMS.get(n.strip().lower(), n.strip().lower())
            n = n if n in self.name_idx or f"ing:{n}" in self.ext_ing else self.brand.get(n, n)
            if n not in self.name_idx and f"ing:{n}" not in self.ext_ing:  # "loxoprofen sodium hydrate"
                n = normalize_ingredient(n, self.known) or n
            if n in self.name_idx:
                out.append(self._db(self.name_idx[n]))
            elif f"ing:{n}" in self.ext_ing:
                out.append(self._ext_ingredient(f"ing:{n}"))
            elif bio_key(n):
                out.append(self._bio(bio_key(n)))
            elif n in self.syn:  # any approved drug's trade name / INN / synonym world-wide (ChEMBL)
                out.append(self._ext_ingredient(self.syn[n]))
            elif (n in self.label_keys or structureless_category(n) != "기타" or drug_groups(n) != {n}
                  or not text.isascii()):  # curated Korean name (KR_INGREDIENTS / KR_BRANDS) without a structure
                # any substance with an FDA label (antibodies, vitamins ...) or a recognised herbal / supplement
                out.append(self._ext_ingredient(f"lab:{lab_name(n) or n}"))
            else:
                missing.append(n)
        if out and not missing:
            return out
        if not text.isascii() or " " in text or "+" in text or not names:
            why = f"분석 DB에 없는 성분: {', '.join(missing)}" if missing else "알 수 없는 약물명"
            raise ResolveError(why + " (생물의약품·복합제일 수 있음 — 성분명 또는 SMILES 구조식으로 입력)")
        return [self._from_smiles(text)]  # an ASCII word that is no known name: maybe a SMILES

    def search(self, q: str, limit: int = 15) -> list[dict]:
        """Autocomplete across Korean products, Korean/English ingredient and brand names."""
        q = (q or "").strip()
        if not q:
            return []
        ql, qn = q.lower(), q.replace(" ", "")
        out: list[dict] = []
        for v in FOODS.values():  # 술, 자몽주스, 우유, 흡연 ...
            if any(a.lower().startswith(ql) for a in v["aliases"]):
                out.append({"name": v["aliases"][0], "type": "음식·생활", "sub": v["ko"]})
        cat = self.catalog()
        for kind, names in (("제품", cat["korean_brands"]), ("성분", cat["korean_ingredients"]),
                            ("ingredient", cat["ingredients"]), ("brand", cat["brands"])):
            for n in names:
                if n.lower().startswith(ql):
                    out.append({"name": n, "type": kind, "sub": ""})
        if len(self.products) and len(qn) >= 2:
            pool = self._pool(script(q))
            key = _key(q)
            m = pool[pool["key"].str.contains(re.escape(key), regex=True)]
            m = m.assign(_pre=m["key"].str.startswith(key), _len=m["name"].str.len())
            m = m.sort_values(["_pre", "active", "otc", "_len"], ascending=[False, False, False, True])
            m = m.drop_duplicates("key").head(limit)
            for _, r in m.iterrows():
                ings = ", ".join(self._display_key(k) for k in r["ingredients"])
                extra = f" +분석불가 {len(r['unresolved'])}" if len(r["unresolved"]) else ""
                out.append({"name": r["name"], "type": self._kind(r),
                            "sub": f"{ings}{extra}" + ("" if r["active"] else " · 허가취소/판매중단")})
        seen, res = set(), []
        for o in out:
            if o["name"] not in seen:
                seen.add(o["name"])
                res.append(o)
        return res[:limit]

    def _from_smiles(self, smi: str) -> Ingredient:
        try:
            std = standardize(smi)
        except MoleculeError as e:
            if not any(ch in smi for ch in "()=#[]@0123456789"):
                raise ResolveError("알 수 없는 약물명입니다 (성분명/제품명 또는 SMILES 구조식으로 입력)") from e
            raise ResolveError(f"SMILES 구조 오류: {e.message}") from e
        if std.inchikey in self.inchikey_idx:  # a known drug typed as a structure
            return self._db(self.inchikey_idx[std.inchikey])
        return Ingredient(f"smiles:{std.inchikey}", f"신규 구조 {std.inchikey[:8]}", std.canonical_smiles, None)

    # ------------------------------------------------------------------ helpers
    def ko(self, ing: Ingredient) -> str:
        if ing.food:
            return FOODS[ing.bare]["ko"]
        k = KO_NAME.get(ing.key) or KO_NAME.get(ing.bare)
        return f"{k}({ing.name})" if k else ing.name

    def molecule(self, ing: Ingredient) -> dict:
        if ing.biologic:  # biologic / herbal / vitamin ...: no 3D small-molecule model
            return {"key": ing.key, "name": ing.name, "display": self.ko(ing), "smiles": "", "known": True,
                    "biologic": True, "category": structureless_category(ing.key),
                    "atoms": [], "bonds": [], "props": {}}
        m = self.mol3d.get(ing.key) if ing.idx is not None else None
        if m is None:
            m = mol_3d(ing.smiles) or {"atoms": [], "bonds": [], "props": {}}
        return {"key": ing.key, "name": ing.name, "display": self.ko(ing), "smiles": ing.smiles,
                "known": ing.idx is not None, **m}

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

    def _label_evidence_fts(self, a: Ingredient, b: Ingredient) -> list[dict]:
        """Search ALL indexed labels of each drug for the other drug's name; one excerpt per section,
        with the number of labels that say so (labels.db, stage "labelsearch")."""
        key = (a.key, b.key)
        cached = self._evidence_cache.get(key)
        if cached is None:
            cached = self._label_evidence_compute(a, b)
            if len(self._evidence_cache) > 20000:  # bounded
                self._evidence_cache.pop(next(iter(self._evidence_cache)))
            self._evidence_cache[key] = cached
        return [dict(e) for e in cached]  # callers annotate the dicts

    def _hits(self, phrases: list[str]) -> np.ndarray:
        """rowids of all label sections containing any phrase (full-text index, rowids only: milliseconds)."""
        q = "text : (" + " OR ".join('"' + p.replace('"', "") + '"' for p in phrases) + ")"
        hit = self._hit_cache.get(q)
        if hit is None:
            with self._lab_lock:
                hit = np.fromiter((r for (r,) in self._labdb.execute("SELECT rowid FROM sec WHERE sec MATCH ?", (q,))),
                                  dtype=np.int64)
            if len(self._hit_cache) > 4000:
                self._hit_cache.pop(next(iter(self._hit_cache)))
            self._hit_cache[q] = hit
        return hit

    def _labels_of(self, bare: str) -> np.ndarray:
        arr = self._key_label_arr.get(bare)
        if arr is None:
            arr = self._key_label_arr[bare] = np.fromiter(self._key_labels.get(bare, ()), dtype=np.int32)
        return arr

    def _label_evidence_compute(self, a: Ingredient, b: Ingredient) -> list[dict]:
        out = []
        contra_codes = {"금기", "일반약: 사용 금지"}
        for src, other in ((a, b), (b, a)):
            if src.key.startswith("smiles:") or other.key.startswith("smiles:") or src.food:
                continue  # a typed structure has no name to find; a food has no drug label of its own
            terms = (FOODS.get(other.bare, {}).get("terms") if other.food else
                     BIOLOGICS.get(other.bare, {}).get("terms", [other.bare]))
            rowids = self._hits(terms)
            lids = self._sec_label[rowids]
            # labels of the source drug that do NOT also contain the other drug (a product containing both
            # describes that product, not an interaction)
            keep = np.isin(lids, self._labels_of(src.bare)) & ~np.isin(lids, self._labels_of(other.bare))
            rowids, lids = rowids[keep], lids[keep]
            nk = np.array([self._lab_nkeys.get(int(x), 1) for x in lids], dtype=np.int32)
            single = True
            sel = nk == 1  # single-ingredient labels first: a combination's warning may concern its OTHER ingredient
            if not sel.any():
                single, sel = False, np.ones(len(rowids), dtype=bool)
            groups: dict[str, list[tuple[int, int, int]]] = {}
            for r, lid in zip(rowids[sel], lids[sel], strict=True):
                groups.setdefault(self._sections[self._sec_kind[r]], []).append((self._lab_rx.get(int(lid), 0), int(lid),
                                                                                int(r)))
            for sec, items in groups.items():
                items.sort(key=lambda t: (-t[0], t[1]))  # prescription labels first
                n_labels = len({i[1] for i in items})
                # Several labels (manufacturers) may word it differently: look at a few candidates per section and
                # keep, per resulting section, the most informative one - a real interaction statement first,
                # strong wording ("contraindicated", "avoid") before a mild one.
                best: dict[str, tuple[int, dict]] = {}
                seen_lid: set[int] = set()
                for _, lid, r in items:
                    if lid in seen_lid:
                        continue
                    seen_lid.add(lid)
                    if len(seen_lid) > 6:
                        break
                    excerpt, local = self._window(r, terms)
                    actual = sec
                    if sec in contra_codes and ALLERGY_CONTEXT.search(local):
                        actual = "경고·주의"  # a contraindication about allergy to the other drug
                    elif sec in contra_codes and ASK_DOCTOR.search(local):
                        actual = "일반약: 복용 전 상담"  # OTC "Ask a doctor ... if taking warfarin" in the wrong field
                    e = {"label_of": src.bare, "mentions": other.bare,
                         "section": "병용금기/금기" if actual in contra_codes else actual, "excerpt": excerpt,
                         "n_labels": n_labels, "source_section": actual, "terms": list(terms),
                         "combo": not single}  # only combination-product labels mention it
                    score = 2 * bool(_own([e])) + bool(SEVERE_LABEL.search(_about(e)))
                    if actual not in best or score > best[actual][0]:
                        best[actual] = (score, e)
                    if actual == sec and score == 3:
                        break
                out += [e for _, e in best.values()]
            # the label may name the other drug's CLASS instead ("avoid strong CYP3A4 inducers", "MAOIs")
            phrases = [] if other.food else class_phrases(other.key)
            if phrases:
                found = {e["source_section"] for e in out if e["label_of"] == src.bare}
                rows = self._hits(phrases)
                lids = self._sec_label[rows]
                keep = np.isin(lids, self._labels_of(src.bare))
                by_c: dict[str, list] = {}
                for r, lid in zip(rows[keep], lids[keep], strict=True):
                    sec = self._sections[self._sec_kind[r]]
                    if (sec in ("금기", "상호작용", "박스 경고", "일반약: 사용 금지") and sec not in found
                            and self._lab_nkeys.get(int(lid), 1) == 1):
                        by_c.setdefault(sec, []).append((self._lab_rx.get(int(lid), 0), int(lid), int(r)))
                for sec, items in by_c.items():
                    items.sort(key=lambda t: (-t[0], t[1]))
                    ex, _ = self._window(items[0][2], phrases)
                    via = next((p for p in phrases if p.lower() in ex.lower()), phrases[0])
                    out.append({"label_of": src.bare, "mentions": other.bare, "via_class": via, "terms": list(phrases),
                                "section": "병용금기/금기" if sec in contra_codes else sec,
                                "excerpt": ex, "n_labels": len({i[1] for i in items}),
                                "source_section": sec, "combo": False})
        order = {"병용금기/금기": 0, "박스 경고": 1, "상호작용": 2, "일반약: 복용 전 상담": 3, "경고·주의": 4}
        return sorted(out, key=lambda e: order.get(e["section"], 9))

    def _window(self, rowid: int, terms, before: int = 450, after: int = 350) -> tuple[str, str]:
        """(excerpt, local) from the full section text around the first whole-word mention. The excerpt keeps
        enough lead-in for the heading / clinical-impact sentence a table row ('Examples: ... cimetidine') belongs
        to; 'local' is the +-150 characters used to classify the mention (allergy, OTC advice)."""
        with self._lab_lock:
            row = self._labdb.execute("SELECT text FROM sec WHERE rowid = ?", (rowid,)).fetchone()
        text = row[0] if row else ""
        for pat in (r"(?<![\w/]){}(?![\w/])", r"(?<!\w){}(?!\w)", r"{}"):  # whole word, then inside "a/b", then any
            m = next((m for t in terms if (m := re.search(pat.format(re.escape(t)), text, re.I))), None)
            if m:
                s, e = max(0, m.start() - before), min(len(text), m.end() + after)
                local = text[max(0, m.start() - 150): m.end() + 150]
                return "…" + " ".join(text[s:e].split()) + "…", local
        # stemmed / hyphenated match only: show the start of the section
        return "…" + " ".join(text[:before + after].split()) + "…", text[:300]

    def _cyp_fda(self, a: Ingredient, b: Ingredient) -> list[dict]:
        """FDA clinical index drugs: strong inhibitor/inducer x sensitive substrate (documented -> may be 'high')."""
        out = []
        for v, w in ((a, b), (b, a)):
            vn, wn = v.bare, w.bare
            for enz, t in CYP_FDA.items():
                sens, mod = vn in t["sensitive"], vn in t["moderate_sub"]
                if not (sens or mod):
                    continue
                prodrug = PRODRUGS_ACTIVATED_BY.get(vn) == enz
                if wn in t["strong_inh"] or wn in t["moderate_inh"]:
                    strong = wn in t["strong_inh"]
                    effect = (f"{self.ko(v)}의 활성화가 줄어 효과가 떨어질 수 있습니다" if prodrug else
                              f"{self.ko(v)}의 혈중 농도가 {'5배 이상' if strong and sens else '2~5배'} 오를 수 있습니다")
                    out.append({"kind": "CYP-FDA", "severe": strong and sens and not prodrug,
                                "text": f"{self.ko(w)}은(는) {enz} {'강한' if strong else '중간'} 억제제 → {effect} (FDA 지표 약물표)"})
                elif wn in t["strong_ind"] or wn in t["moderate_ind"]:
                    out.append({"kind": "CYP-FDA", "severe": False,
                                "text": f"{self.ko(w)}은(는) {enz} 유도제 → {self.ko(v)}의 농도가 떨어져 효과가 줄 수 있습니다 "
                                        "(FDA 지표 약물표)"})
        return out

    @staticmethod
    def _pair_rules(a: Ingredient, b: Ingredient) -> list[dict]:
        """Class-level interactions stated in labelling (knowledge.PAIR_RULES), e.g. GLP-1 + insulin."""
        ga, gb = drug_groups(a.key), drug_groups(b.key)
        return [{"kind": "규칙", "severe": sev == "높음", "text": text} for x, y, sev, text in PAIR_RULES
                if (x in ga and y in gb) or (x in gb and y in ga)]

    def _mechanism(self, a: Ingredient, b: Ingredient) -> list[dict]:
        """Pharmacological reasons two drugs may interact: class rules, FDA CYP index tables, ChEMBL in-vitro data."""
        out = self._pair_rules(a, b) + self._cyp_fda(a, b)
        pa, pb = self.pharm.get(a.key), self.pharm.get(b.key)
        if not pa or not pb:
            return out

        def conc(p: float) -> str:
            nm = 10 ** (9 - p)
            return f"{nm:.0f} nM" if nm < 1000 else f"{nm / 1000:.1f} µM"

        for victim, perp, v, w in ((pa, pb, a, b), (pb, pa, b, a)):
            for cyp in sorted(victim["sub"]):
                p = perp["inh"].get(cyp)
                if p and p >= CYP_INHIBITION_P and not any(m["kind"] in ("CYP-FDA", "CYP") for m in out):
                    out.append({"kind": "CYP", "text": f"{self.ko(w)}이(가) {cyp} 효소를 억제(시험관 IC50 약 {conc(p)}) → "
                                f"{cyp}로 분해되는 {self.ko(v)}의 혈중 농도가 올라갈 수 있습니다"})
        if pa["herg"] and pb["herg"] and min(pa["herg"], pb["herg"]) >= HERG_P:
            out.append({"kind": "QT", "text": f"두 약 모두 심장 hERG 채널을 차단(IC50 {conc(pa['herg'])}, {conc(pb['herg'])}) → "
                        "QT 연장·부정맥 위험이 더해질 수 있습니다"})
        ta = {(m["target"], m["action"]) for m in pa["moa"] if m.get("target")}
        tb = {(m["target"], m["action"]) for m in pb["moa"] if m.get("target")}
        same = ta & tb
        if same:
            txt = next((m["text"] for m in pa["moa"] if (m["target"], m["action"]) in same), "같은 작용점")
            out.append({"kind": "PD", "text": f"두 약의 작용 기전이 같습니다({txt}) → 효과·부작용이 겹칩니다"})
        sero = [m for m in pa["moa"] + pb["moa"] if "serotonin" in str(m.get("text", "")).lower()
                or "monoamine oxidase" in str(m.get("text", "")).lower()]
        if not same and any(m in pa["moa"] for m in sero) and any(m in pb["moa"] for m in sero):
            out.append({"kind": "PD", "text": "두 약 모두 세로토닌계에 작용 → 세로토닌 증후군(고열·떨림·혼돈) 위험이 있습니다"})
        return out

    def _label_evidence(self, a: Ingredient, b: Ingredient) -> list[dict]:
        if self._labdb is not None:
            return self._label_evidence_fts(a, b)
        out = []
        if not len(self.labels):
            return out
        for src, other in ((a, b), (b, a)):
            if src.idx is None or other.idx is None or src.key not in self.labels.index:
                continue
            row = self.labels.loc[src.key]
            for field, kind in (("contraindications", "병용금기/금기"), ("boxed_warning", "박스 경고"),
                                ("drug_interactions", "상호작용")):
                text = row[field] or ""
                m = re.search(rf"\b{re.escape(other.key)}\b", text, flags=re.I)
                if m:
                    s, e = max(0, m.start() - 220), min(len(text), m.end() + 320)
                    out.append({"label_of": src.key, "mentions": other.key, "section": kind,
                                "excerpt": "…" + " ".join(text[s:e].split()) + "…"})
        return out

    def pair_level(self, A: Ingredient, B: Ingredient) -> tuple[str, str | None]:
        """The pair verdict of predict() for one pair, with its main reason (used to screen suggested medicines)."""
        lev = [] if (_minor(A) and _minor(B)) else self._label_evidence(A, B)
        for e in lev:
            e["context"] = _context(e)
        own = _own(lev)
        contra = any(e["section"] == "병용금기/금기"
                     or (e["section"] == "박스 경고" and BOXED_COMBINATION.search(_about(e))
                         and not AMOUNT_ONLY.search(_about(e))) for e in own)
        strong = any(SEVERE_LABEL.search(_about(e)) and not AMOUNT_ONLY.search(_about(e)) for e in own
                     if e["section"] in ("상호작용", "병용금기/금기", "박스 경고"))
        mech = self._mechanism(A, B)
        dur = self.dur_pairs.get(frozenset((A.bare, B.bare)))
        if dur:
            return "높음", "식약처 DUR 병용금기" + (f" — {dur['content']}" if dur.get("content") else "")
        severe = next((m["text"] for m in mech if m.get("severe")), None)
        if contra or strong or severe:
            return "높음", severe or "허가 문서에 피해야 하는 조합으로 기재"
        if own or mech:
            return "주의", mech[0]["text"] if mech else "허가 문서에 상호작용으로 기재"
        return "낮음", None

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
                class_dups.append({"class": cname, "ingredients": [self.ko(g) for g in mem], "products": sorted(src)})
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
                class_dups.append({"class": f"{grp} (식약처 DUR 효능군중복)", "ingredients": names, "products": sorted(src)})
                covered.add(frozenset(names))
        # WHO ATC level 4 (same chemical/therapeutic subgroup) for every approved drug (ChEMBL); vitamins,
        # minerals, topical and 'various' groups are excluded - combining those is usually intended
        atc_grp: dict[str, list[Ingredient]] = {}
        for g in U:
            for code in {c[:5] for c in self.atc_of.get(g.bare, [])}:
                if not code.startswith(("A11", "A12", "D", "S", "V", "R02", "A01")):
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
                if p_loo is not None:
                    contrib = p - p_loo[:, c]
                    ev["driver"] = self.ko(V[int(contrib.argmax())])
                else:  # one drug in the model (the rest are foods / biologics): that drug
                    ev["driver"] = self.ko(V[0])
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
        for a, b in itertools.combinations(range(len(U)), 2):
            A, B = U[a], U[b]
            pa_, pb_ = prods_of[A.key], prods_of[B.key]
            if len(pa_) == 1 and pa_ == pb_ and len(products) > 1:
                same_product += 1  # components of one marketed combination product are combined by design
                continue
            obs = self.observed.get(frozenset((A.idx, B.idx)), []) if A.idx is not None and B.idx is not None else []
            # two vitamins / minerals / nutrients: labels list them side by side ("thiamine, niacinamide and ascorbic
            # acid decrease ...") but they do not interact with each other
            lev = [] if (_minor(A) and _minor(B)) else self._label_evidence(A, B)
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
            for e in lev:
                e["context"] = _context(e)  # shown in the details: 'no interaction' / co-therapy mentions
            own = _own(lev)
            contra = any(e["section"] == "병용금기/금기"
                         or (e["section"] == "박스 경고" and BOXED_COMBINATION.search(_about(e))
                             and not AMOUNT_ONLY.search(_about(e))) for e in own)
            # strong wording counts only in interaction-specific sections (general warnings mention many drugs)
            # and only in the sentences that name the other drug
            # "avoid LARGE quantities of grapefruit juice", "avoid DRASTIC changes in vitamin K intake": an amount /
            # consistency caution, not a contraindication
            strong = any(SEVERE_LABEL.search(_about(e)) and not AMOUNT_ONLY.search(_about(e)) for e in own
                         if e["section"] in ("상호작용", "병용금기/금기", "박스 경고"))
            mech = self._mechanism(A, B)
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
        # ... and the combination effect must be strong (>= 3x the riskier drug alone) or come from this patient:
        # weaker co-prescription signals are confounded (metformin + atorvastatin 'jaundice' 1.7x in sicker
        # patients), while true interactions are far above (sertraline + tramadol serotonin syndrome 12.6x)
        for e in events:
            e["drives_verdict"] = bool(e["serious"] and e["lift"] >= 1.5 and e["prob"] >= SIGNAL_P
                                       and (_combo(e) >= COMBO_X or e["patient_factor"] >= 1.5))
        added_signal = any(e["drives_verdict"] for e in events)
        if any(not d["minor"] for d in duplicates):
            overall = "높음"
        elif overall == "낮음" and (added_signal or duplicates):
            overall = "주의"
        severe_classes = {DRUG_CLASSES[c][0] for c in ("nsaid", "opioid", "sedative", "anticoagulant", "pde5")}
        for d in class_dups:
            d["severe"] = d["class"] in severe_classes
        if any(d["severe"] for d in class_dups):
            overall = "높음"
        elif class_dups and overall == "낮음":
            overall = "주의"
        if same_product:
            notes.append(f"같은 복합제 안에 함께 배합된 성분끼리의 조합 {same_product}쌍은 의도된 배합이므로 상호작용 평가에서 제외했습니다.")

        disease_alerts = self._drug_disease(U, pa, patient)
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
            # plain word = how much the combination / this patient ADDS; a drug's inherent risk alone is "약 자체 위험"
            if e["lift"] >= 3 and e["added"] >= 1.5:
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
                                dosing, prn_keys)
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

    # ------------------------------------------------------------------ drug-disease + plain summary
    def _drug_disease(self, U: list[Ingredient], pa: dict, patient: Patient) -> list[dict]:
        """Which of these drugs are especially dangerous for THIS patient's diseases / age / kidneys.
        Curated rules (Beers 2023 drug-disease table, labelling) + FDA label contraindication / boxed text."""
        dis = set(pa["diseases"])
        kid = pa.get("kidney") or {}
        found: dict[tuple[str, str], dict] = {}
        rank = {"금기": 2, "주의": 1}

        def add(dkey: str, g: Ingredient, sev: str, reason: str, source: str, evidence: str | None = None):
            k = (dkey, g.key)
            name = DISEASES[dkey]["ko"] if dkey in DISEASES else dkey
            cur = found.get(k)
            if cur is None or rank[sev] > rank[cur["severity"]]:
                found[k] = {"key": dkey, "disease": name, "drug": self.ko(g), "severity": sev, "reason": reason,
                            "source": source, "evidence": evidence or (cur or {}).get("evidence")}
            else:
                if evidence and not cur.get("evidence"):
                    cur["evidence"] = evidence
                if rank[sev] == rank[cur["severity"]] and source not in cur["source"]:
                    cur["source"] += f" · {source}"  # same conclusion from another reference (e.g. MFDS DUR)

        for g in U:
            groups = drug_groups(g.key)
            for dkey, grp, sev, reason in DRUG_DISEASE_RULES:
                if dkey in dis and grp in groups:
                    add(dkey, g, sev, reason, "임상 기준(Beers·허가사항)")
            if "metformin" in groups and kid.get("clearance") is not None and kid["clearance"] < 30:
                add("renal", g, "금기", "콩팥 기능이 크게 떨어져 젖산산증 위험이 있습니다", "허가사항(eGFR<30 금기)")
            if patient.age is not None and patient.age >= 65:
                if groups & {"sedating_antihistamine", "anticholinergic"}:
                    add("고령(65세 이상)", g, "주의", "혼돈·변비·소변 장애·낙상을 일으키기 쉽습니다", "Beers 기준")
                if "sedative" in groups:
                    add("고령(65세 이상)", g, "주의", "낙상·골절과 인지 저하 위험이 큽니다", "Beers 기준")
            # FDA label: contraindication / boxed-warning text that names one of the patient's diseases
            if g.idx is not None and len(self.labels) and g.key in self.labels.index:
                row = self.labels.loc[g.key]
                for dkey in dis:
                    term = DISEASE_LABEL_TERMS.get(dkey)
                    if not term:
                        continue
                    for field, sev in (("contraindications", "금기"), ("boxed_warning", "주의")):
                        text = row[field] or ""
                        m = re.search(term, text, flags=re.I)
                        if m:
                            s, e = max(0, m.start() - 160), min(len(text), m.end() + 220)
                            ex = "…" + " ".join(text[s:e].split()) + "…"
                            if (dkey, g.key) in found:
                                found[(dkey, g.key)]["evidence"] = found[(dkey, g.key)].get("evidence") or ex
                            else:
                                add(dkey, g, sev, f"FDA 허가 라벨의 {'금기' if sev == '금기' else '박스경고'} 항목에 "
                                    f"'{DISEASES[dkey]['ko']}' 관련 내용이 있습니다", "FDA 허가 라벨", ex)
                            break
        # MFDS DUR: age-group contraindication, pregnancy (grade 1 = avoid, 2 = only if clearly needed), elderly
        for g in U:
            b = g.bare
            for r in self.dur_by.get("age", {}).get(b, []):
                if patient.age is not None and _age_hit(r.get("age_base"), patient.age):
                    add(f"연령({r['age_base']})", g, "금기",
                        f"식약처 DUR 특정연령대금기 — {r.get('content') or '이 연령대에서 쓰지 않습니다'}", "식약처 DUR")
            if "pregnancy" in dis:
                for r in self.dur_by.get("pregnancy", {}).get(b, [])[:1]:
                    one = "1" in (r.get("grade") or "")
                    add("pregnancy", g, "금기" if one else "주의",
                        f"식약처 DUR 임부금기 {r.get('grade') or ''} — {r.get('content') or '임부에 대한 안전성 미확립'}"
                        + (" (사용 금지)" if one else " (치료상 유익성이 클 때만)"), "식약처 DUR")
            if patient.age is not None and patient.age >= 65:
                for r in self.dur_by.get("elderly", {}).get(b, [])[:1]:
                    add("고령(65세 이상)", g, "주의", f"식약처 DUR 노인주의 — {(r.get('content') or '').strip()[:160]}",
                        "식약처 DUR")
        return sorted(found.values(), key=lambda d: -rank[d["severity"]])

    def _summary(self, overall, duplicates, class_dups, disease_alerts, pair_rows, events, pa,
                 dosing=None, prn_keys=frozenset()) -> dict:
        """Plain-language verdict for people who are not clinicians: what is wrong and what to do."""
        reasons: list[dict] = []
        advice: list[str] = []
        for a in (dosing or {}).get("alerts", []):
            reasons.append({"sev": "high" if a["sev"] == "금기" else "mid", "icon": "⚖️", "text": a["text"] + "."})
        if any(a["sev"] == "금기" for a in (dosing or {}).get("alerts", [])):
            advice.append("하루 총 복용량·복용 횟수를 기준 이하로 줄이세요 (여러 제품의 같은 성분은 합쳐서 계산).")
        major = [d for d in duplicates if not d["minor"]]
        minor = [d for d in duplicates if d["minor"]]
        if major:  # one line for all overlaps, the worst first
            dups = sorted(major, key=lambda d: -len(d["products"]))
            items = ", ".join(f"{d['ingredient'].split('(')[0]}({len(d['products'])}개 제품)" for d in dups[:4])
            more = f" 외 {len(dups) - 4}개" if len(dups) > 4 else ""
            reasons.append({"sev": "high", "icon": "💊",
                            "text": f"같은 성분이 여러 제품에 겹쳐 과다 복용이 됩니다: {items}{more}."})
            prods = sorted({p for d in dups for p in d["products"]})
            advice.append(f"성분이 겹치는 제품({', '.join(prods)}) 중 하나만 고르세요.")
        if minor:
            items = ", ".join(d["ingredient"].split("(")[0] for d in minor[:5])
            reasons.append({"sev": "mid", "icon": "☕",
                            "text": f"{items}이(가) 여러 제품에 함께 들어 있습니다 — 보통 양이면 문제없지만 하루 총량을 확인하세요"
                                    + (" (카페인은 성인 하루 400mg 이하)" if any("caffeine" in d["ingredient"] for d in minor)
                                       else "") + "."})
        for d in class_dups:
            if d.get("severe"):  # NSAID x2, opioid x2, sedative x2, anticoagulant/antiplatelet x2, PDE5 x2
                reasons.append({"sev": "high", "icon": "🔁",
                                "text": f"같은 계열 약({d['class']}) 두 가지 이상을 함께 쓰면 효과·부작용이 겹쳐 위험합니다: "
                                        f"{', '.join(d['ingredients'])}."})
                advice.append(f"{', '.join(i.split('(')[0] for i in d['ingredients'])}을(를) 함께 쓰는 것이 의도된 처방인지 "
                              "의사·약사와 확인하세요.")
            else:
                reasons.append({"sev": "mid", "icon": "🔁",
                                "text": f"같은 계열 약({d['class']})이 여러 제품에 겹쳐 효과·부작용이 함께 커집니다: "
                                        f"{', '.join(d['ingredients'])}."})
        for d in disease_alerts:
            reasons.append({"sev": "high" if d["severity"] == "금기" else "mid", "icon": "🩺",
                            "text": f"{d['disease']} 환자에게 {d['drug']}: {d['reason']}."
                                    + (" (피해야 함)" if d["severity"] == "금기" else "")})
            if d["severity"] == "금기":
                advice.append(f"{d['drug']}은(는) 피하고, 대신 쓸 약을 의사·약사와 상의하세요.")
        for r in pair_rows:
            own = _own(r["label_evidence"])
            secs = {e["section"] for e in own}
            if r["level"] == "낮음":
                if any(e.get("context") == "no_interaction" for e in r["label_evidence"]):
                    reasons.append({"sev": "low", "icon": "✅",
                                    "text": f"{r['a']}과(와) {r['b']}은(는) 허가 문서에 '임상적으로 의미 있는 상호작용 없음'으로 "
                                            "적혀 있습니다."})
                continue
            if r.get("dur"):
                why = f" (사유: {r['dur']['content']})" if r["dur"].get("content") else ""
                reasons.append({"sev": "high", "icon": "⛔",
                                "text": f"{r['a']}과(와) {r['b']}은(는) 식약처 DUR 병용금기 성분 조합입니다{why}."})
                advice.append(f"{r['a']}과(와) {r['b']}을(를) 함께 복용하지 마세요 (식약처 병용금기).")
            elif "병용금기/금기" in secs:
                reasons.append({"sev": "high", "icon": "⛔",
                                "text": f"{r['a']}과(와) {r['b']}은(는) 허가 문서에 함께 쓰지 말라고 되어 있습니다."})
                advice.append(f"{r['a']}과(와) {r['b']}을(를) 함께 복용하지 마세요.")
            elif r["level"] == "높음" and not own and any(m.get("severe") for m in r.get("mechanism", [])):
                m = next(m for m in r["mechanism"] if m.get("severe"))
                reasons.append({"sev": "high", "icon": "🧬", "text": f"{r['a']} + {r['b']}: {m['text']}."})
                advice.append(f"{r['a']}과(와) {r['b']}은(는) 보통 함께 쓰지 않거나 용량을 크게 줄입니다. 의사·약사와 상의하세요.")
            elif r["level"] == "높음" and own:
                reasons.append({"sev": "high", "icon": "📄",
                                "text": f"{r['a']}과(와) {r['b']}은(는) 허가 문서에 '피하거나 용량을 줄여야 하는' "
                                        f"중대한 상호작용으로 적혀 있습니다."})
                advice.append(f"{r['a']}과(와) {r['b']}을(를) 함께 써야 한다면 용량 조절·검사 계획을 의사·약사와 정하세요.")
            elif not own and r.get("mechanism"):
                m = r["mechanism"][0]
                reasons.append({"sev": "mid", "icon": "📋" if m["kind"] == "규칙" else "🧬",
                                "text": f"{r['a']} + {r['b']} ({'임상 규칙' if m['kind'] == '규칙' else '약리 기전'}): "
                                        f"{m['text']}."})
            elif own:
                occasional = r["a_key"] in prn_keys or r["b_key"] in prn_keys
                rule = next((m["text"] for m in r.get("mechanism", []) if m["kind"] in ("규칙", "CYP-FDA")), None)
                if rule:  # the concrete consequence is more useful than "documented interaction"
                    text = f"{r['a']} + {r['b']}: {rule} (허가 문서에도 상호작용으로 기재)"
                else:
                    text = (f"{r['a']}과(와) {r['b']}은(는) 서로 영향을 주는 조합으로 허가 문서에 기재돼 있습니다 "
                            + ("(필요할 때만 가끔 복용하면 영향이 작습니다)." if occasional
                               else "(규칙적으로 함께 먹을 때 관찰 필요)."))
                reasons.append({"sev": "mid", "icon": "📄", "text": text})
        documented = any(r["level"] != "낮음" for r in pair_rows)  # a label / rule / DUR interaction was found
        # the AI signals that changed the verdict are always explained first; others are information only
        drivers = [e for e in events if e.get("drives_verdict")][:2]
        for e in drivers:
            organ, ev = ORGANS[e["organ"]], e["ko"]
            if _combo(e) >= COMBO_X:
                text = f"{organ} (AI 예측): 함께 먹으면 '{ev}' 위험이 한 가지만 먹을 때보다 크게 늘 수 있습니다."
            else:
                text = f"{organ} (AI 예측): 이 환자의 몸 상태 때문에 '{ev}' 위험이 평균보다 커질 수 있습니다."
            reasons.append({"sev": "mid", "icon": "📈", "text": text})
        top = [e for e in events if e["serious"] and e["lift"] >= 2 and e["prob"] >= MIN_P
               and not e.get("drives_verdict")][:max(0, 2 - len(drivers))]
        for e in top:
            organ, ev = ORGANS[e["organ"]], e["ko"]
            rare = e["prob"] < SIGNAL_P
            if (_combo(e) >= 1.5 or e["patient_factor"] >= 1.5) and rare:
                text, sev = (f"{organ} (AI 참고): '{ev}' 보고가 함께 쓸 때 조금 더 많지만 드문 신호입니다"
                             " (비슷한 보고의 1% 미만) — 보통 용량에서는 걱정할 수준이 아닙니다.", "low")
            elif 1.5 <= _combo(e) < COMBO_X and e["patient_factor"] < 1.5 and documented:
                text, sev = (f"{organ} (AI 참고): 실제 보고에서도 함께 쓸 때 '{ev}' 보고가 더 많습니다 — "
                             "위의 문서화된 상호작용과 같은 방향입니다.", "low")
            elif 1.5 <= _combo(e) < COMBO_X and e["patient_factor"] < 1.5:
                text, sev = (f"{organ} (AI 참고): 함께 쓰는 환자에게서 '{ev}' 보고가 다소 많습니다 — 약을 함께 쓰는 환자가 "
                             "원래 더 아픈 경우가 많아 생기는 차이일 수 있어 이것만으로 상호작용이라고 보지는 않았습니다.", "low")
            elif _combo(e) >= 1.5:
                text, sev = f"{organ} (AI 참고): 함께 먹을 때 '{ev}' 보고가 다소 많습니다.", "low"
            elif e["patient_factor"] >= 1.5:
                text, sev = f"{organ} (AI 참고): 이 환자의 몸 상태에서 '{ev}' 보고가 평균보다 다소 많습니다.", "low"
            else:
                drv = f"{e['driver']} " if e.get("driver") else ""
                # a drug's own known side effect: information, not an interaction warning
                text, sev = f"{organ}: '{ev}'은(는) {drv}약 자체에 따르는 위험입니다 — 복용 중 증상을 살펴보세요.", "low"
            reasons.append({"sev": sev, "icon": "📈", "text": text})
        cap = pa.get("capacity")
        if cap and cap["level"] in ("저하", "매우 저하"):
            reasons.append({"sev": "mid", "icon": "🫀",
                            "text": f"몸의 약물 처리 능력이 '{cap['level']}' 상태라 약이 몸에 쌓이기 쉽습니다."})
            advice.append("용량을 줄이거나 복용 간격을 늘려야 할 수 있습니다 — 의사·약사와 확인하세요.")
        order = {"high": 0, "mid": 1, "low": 2}
        reasons = sorted(reasons, key=lambda r: order[r["sev"]])[:6]
        verdict = {"높음": "위험", "주의": "주의", "낮음": "비교적 안전"}[overall]
        headline = {"위험": "이 약 조합은 이 환자에게 위험할 수 있습니다.",
                    "주의": "주의가 필요한 조합입니다. 아래 이유를 확인하세요.",
                    "비교적 안전": "뚜렷한 위험 신호는 없습니다. 복약 안내는 그대로 지켜 주세요."}[verdict]
        if not advice:
            advice.append("새로운 증상이 생기면 복용을 멈추고 의사·약사와 상의하세요." if verdict != "비교적 안전"
                          else "정해진 용법·용량을 지키세요.")
        return {"verdict": verdict, "headline": headline, "reasons": reasons, "advice": list(dict.fromkeys(advice))[:4]}

    # ------------------------------------------------------------------ catalog
    def catalog(self) -> dict:
        supported = set(self.name_idx)
        ok = lambda v: (DRUG_SYNONYMS.get(v, v) in supported or f"ing:{v}" in self.ext_ing  # noqa: E731
                        or v in BIOLOGICS)
        ko_ing = sorted(k for k, v in KR_INGREDIENTS.items() if ok(v))
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
