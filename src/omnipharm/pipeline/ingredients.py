"""Shared ingredient-string -> molecule mapping for every national product database (KR/US/CA/EU/FR).

A raw ingredient string ("DL-Methylephedrine Hydrochloride", "NORTRIPTYLINE (NORTRIPTYLINE HYDROCHLORIDE)",
"DIPROPIONATE DE BÉCLOMÉTASONE") becomes
  * a drug_master name (trained drug)  - by name normalisation, or identical structure (InChIKey), or
  * "ing:<name>"                         - a real small molecule from PubChem without training data
                                           (scored by the GNN as an unseen structure), or
  * None                                 - not analysable (herbal, homeopathic, biologic, mixture, ...).
Structures of "ing:" molecules accumulate in processed/ext_ingredients.parquet, shared by all countries.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections.abc import Iterable

import pandas as pd

from omnipharm.chem.featurize import MoleculeError, standardize
from omnipharm.knowledge import DRUG_SYNONYMS, SALT_WORDS, normalize_ingredient
from omnipharm.pipeline.common import CACHE, PROC
from omnipharm.pipeline.extend import EXCLUDE
from omnipharm.pipeline.structures import pubchem

log = logging.getLogger("pipeline.ingredients")
EXT = PROC / "ext_ingredients.parquet"
PREFIX = "ing:"
MAX_HEAVY = 100

FORM_WORDS = frozenset("coated enteric granules granule fine particles sustained release powder dried "
                       "micronized microencapsulated triturate dilute diluted precipitated purified concentrate "
                       "concentrated sesquihydrate pentahydrate hexahydrate tetrahydrate heptahydrate spray-dried "
                       "anhydride solution suspension beadlets beadlet cws dispersion".split())
NOT_A_MOLECULE = re.compile(
    r"extract|tincture|\bjuice|ferment|\broot|\bleaf|herb|homeopath|homéopath|\b\d+\s*(ch|dh|c|x|k)\b|"
    r"pollen|venom|allergen|bacteria|lactobacill|bifidobact|yeast|cell|tissue|plasma|blood|serum|"
    r"preparation|mixture|complex\b|oil\b|wax|honey|flower|seed|fruit|bark|dye|fragrance|flavou?r"
)

# French INN forms
FR_SALT_PREFIX = re.compile(
    r"^(chlorhydrate|dichlorhydrate|bromhydrate|sulfate|maleate|tartrate|hydrogenotartrate|citrate|phosphate|"
    r"acetate|fumarate|hemifumarate|mesilate|besilate|succinate|dipropionate|propionate|valerate|furoate|"
    r"nitrate|lactate|gluconate|hyclate|tosilate|oxalate|benzoate|pamoate|chlorure|bromure|iodure|"
    r"hydroxyde|carbonate|monohydrate|trihydrate|dihydrate)\s+(de\s+|d')")
FR_SALT_SUFFIX = re.compile(r"\s+(sodique|disodique|potassique|calcique|magnesique|monohydrate|dihydrate|trihydrate|"
                            r"hemihydrate|anhydre|base)\b")


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def core(s: str) -> str:
    """English core name: lowercase, no salt / hydrate / formulation words, no stereo prefixes."""
    s = re.sub(r"\(.*?\)", " ", s.lower()).replace(",", " ")
    s = re.sub(r"\b(dl|d|l|s|r|rac)-", "", s)  # stereo prefixes: S-amlodipine -> amlodipine
    s = re.sub(r"\d+(\.\d+)?\s*(%|％|w/w|mg/g)", " ", s)  # "powder 0.1%", "granules 97%"
    s = re.sub(r"(?<![a-z])\d+(\.\d+)?(?![a-z\d])", " ", s)  # "2.5 hydrate", "1.5 hydrate"
    words = [w for w in s.split() if w not in FORM_WORDS]
    rest = [w for w in words if w not in SALT_WORDS]
    if words and words[0] in MINERALS and all(w in ANIONS or w in MINERALS for w in rest):  # "calcium carbonate" is the drug
        return " ".join(w for w in words if w not in {"hydrate", "monohydrate", "dihydrate", "trihydrate", "anhydrous"})
    return " ".join(rest)


MINERALS = frozenset("calcium magnesium sodium potassium zinc ferrous ferric iron aluminum aluminium lithium "
                     "manganese copper chromium selenium strontium barium bismuth silver".split())
ANIONS = frozenset("carbonate oxide hydroxide chloride sulfate sulphate citrate gluconate lactate phosphate acetate "
                   "bicarbonate hydrogen fumarate aspartate glycinate bisglycinate orotate pidolate trisilicate "
                   "silicate picolinate polymaltose succinate".split())


def variants(raw: str, lang: str = "en") -> list[str]:
    """Candidate English names for a raw ingredient string (most specific first)."""
    s = raw.strip()
    out = [s, re.sub(r"\(.*?\)", "", s).strip(), core(s)]
    c = core(s).split()
    if len(c) == 2 and c[1] != "acid":
        out.append(c[0])  # "gemigliptin tartrate" -> "gemigliptin" (counter-ion word not in SALT_WORDS); tried last
    m = re.search(r"\((.*?)\)", s)  # Canada: "NORTRIPTYLINE (NORTRIPTYLINE HYDROCHLORIDE)"
    if m:
        out.append(core(m.group(1)))
    if lang == "fr":
        f = strip_accents(s.lower())
        f = re.sub(r"\(.*?\)|sous forme de .*$|equivalant a .*$", "", f).strip()
        f = FR_SALT_PREFIX.sub("", f)
        f = FR_SALT_SUFFIX.sub("", f).strip()
        f = re.sub(r"^acide (\w+?)ique$", r"\1ic acid", f)  # acide valproique -> valproic acid
        out += [f, f[:-1] if f.endswith("e") else f]  # amoxicilline -> amoxicillin, ibuprofene -> ibuprofen
    seen, res = set(), []
    for v in out:
        v = " ".join(v.lower().split())
        v = DRUG_SYNONYMS.get(v, v)
        if v and v not in seen:
            seen.add(v)
            res.append(v)
    return res


class Mapper:
    """Maps ingredient strings, querying PubChem only for names nothing else can resolve (cached)."""

    def __init__(self) -> None:
        self.master = pd.read_parquet(PROC / "drug_master.parquet")
        self.known = frozenset(self.master["name"])
        self.by_key = dict(zip(self.master["std_inchikey"], self.master["name"], strict=False))
        self.ext: dict[str, dict] = ({r["key"]: r for r in pd.read_parquet(EXT).to_dict("records")}
                                     if EXT.exists() else {})
        self.ext_by_key = {r["std_inchikey"]: k for k, r in self.ext.items()}
        self.cache_f = CACHE / "pubchem_ext.json"
        self.cache: dict = json.loads(self.cache_f.read_text(encoding="utf-8")) if self.cache_f.exists() else {}
        for old in ("pubchem_mfds.json", "pubchem_extend.json"):  # reuse earlier lookups
            f = CACHE / old
            if f.exists():
                for k, v in json.loads(f.read_text(encoding="utf-8")).items():
                    self.cache.setdefault(k, v)
        self.n_queries = 0

    def by_name(self, raw: str, lang: str = "en") -> str | None:
        for v in variants(raw, lang):
            n = normalize_ingredient(v, self.known)
            if n:
                return n
            if PREFIX + v in self.ext:
                return PREFIX + v
        return None

    def map_all(self, strings: Iterable[str], lang: str = "en") -> dict[str, str | None]:
        strings = list(dict.fromkeys(s for s in strings if s and s.strip()))
        mapping: dict[str, str | None] = {}
        todo = []
        for s in strings:
            n = self.by_name(s, lang)
            if n:
                mapping[s] = n
            else:
                todo.append(s)
        log.info(f"{len(strings):,} ingredient strings ({lang}): {len(mapping):,} by name, {len(todo):,} -> PubChem")
        for k, s in enumerate(todo, 1):
            mapping[s] = self._pubchem(s, lang)
            if k % 250 == 0:
                self.save()
                log.info(f"PubChem {k}/{len(todo)} ({self.n_queries} live queries)")
        self.save()
        return mapping

    def _pubchem(self, raw: str, lang: str) -> str | None:
        vs = variants(raw, lang)
        name = vs[-1] if lang == "fr" else core(raw) or vs[0]
        if (not name or not name.isascii() or EXCLUDE.search(name) or NOT_A_MOLECULE.search(name)
                or len(name.split()) > 4):
            return None
        if name not in self.cache or (self.cache[name] or {}).get("error"):
            self.cache[name] = pubchem(name)
            self.n_queries += 1
        info = self.cache[name]
        try:
            std = standardize(info["smiles"]) if info and not info.get("error") else None
        except MoleculeError:
            std = None
        if std is None or std.mol.GetNumHeavyAtoms() > MAX_HEAVY or std.mol.GetNumHeavyAtoms() < 3:
            return None
        if std.inchikey in self.by_key:  # same molecule as a trained drug under another name
            return self.by_key[std.inchikey]
        if std.inchikey in self.ext_by_key:
            return self.ext_by_key[std.inchikey]
        key = PREFIX + name
        self.ext[key] = {"key": key, "name": name, "std_smiles": std.canonical_smiles, "std_inchikey": std.inchikey}
        self.ext_by_key[std.inchikey] = key
        return key

    def save(self) -> None:
        self.cache_f.write_text(json.dumps(self.cache), encoding="utf-8")
        pd.DataFrame(list(self.ext.values()), columns=["key", "name", "std_smiles", "std_inchikey"]).to_parquet(EXT)


def resolve_list(raw_list: Iterable[str], mapping: dict[str, str | None]) -> tuple[list[str], list[str]]:
    ok, bad = [], []
    for p in raw_list:
        p = p.strip()
        if p:
            m = mapping.get(p)
            (ok if m else bad).append(m or p)
    return list(dict.fromkeys(ok)), list(dict.fromkeys(bad))
