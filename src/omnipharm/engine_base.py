"""Shared types, constants and small helpers of the engine modules (split from engine.py in v2.18)."""

from __future__ import annotations

import functools
import json
import logging
import re
import unicodedata
from dataclasses import dataclass

from omnipharm import recreational
from omnipharm.chem.featurize import standardize
from omnipharm.knowledge import (
    DRUG_SYNONYMS,
    KR_INGREDIENTS,
    bio_key,
    lab_name,
    structureless_category,
)

log = logging.getLogger("omnipharm.engine")  # same logger name as before the split
KO_NAME: dict[str, str] = {}
for _k, _v in KR_INGREDIENTS.items():
    KO_NAME.setdefault(DRUG_SYNONYMS.get(_v, _v), _k)  # first (most common) Korean name wins
for _k, _v in recreational.ingredient_names().items():
    KO_NAME.setdefault(_k, _v)
REFERENCE_PATIENT = {"age": 45.0, "wt": 70.0}


@functools.lru_cache(maxsize=4096)
def _ko_prefix(bare: str) -> str | None:
    """Korean name of a shortened herb key: 'ginkgo' -> 은행잎 (KR_INGREDIENTS says 'ginkgo biloba leaf extract')."""
    if len(bare) < 4 or not bare.isascii():
        return None
    return next((k for k, v in KR_INGREDIENTS.items() if v.startswith(bare + " ")), None)
_LABEL_PREFIX = {"의약외품": "Q", "건강기능식품": "H"}  # Korean label keys; other Korean products are medicines ("D")
MIN_P = 0.003  # absolute probability floor for calling an event "elevated"
SIGNAL_P = 0.01  # an AI signal must reach this (1% of comparable reports) to change the verdict
COMBO_X = 3.0  # ... and be this many times above the riskier single drug (unless the patient explains it)


def _combo(e: dict) -> float:
    return e["prob"] / max(e["best_single"], 1e-6)


@functools.lru_cache(maxsize=128)
def _curated_smiles(smiles: str) -> tuple[str, str]:
    """Curated structure (recreational.SUBSTANCES) -> (canonical SMILES, InChIKey)."""
    std = standardize(smiles)
    return std.canonical_smiles, std.inchikey
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


# action level shown with the verdict (summary["action"]): '주의' alone said too little
ACTIONS = {
    "위험": {"level": "즉시 상담", "text": "함께 쓰기 전에(이미 쓰고 있다면 바로) 의사·약사와 상의하세요."},
    "check": {"level": "확인 필요", "text": "문서로 확인된 주의점입니다 — 의사·약사에게 함께 쓰는 약을 알리고 아래 할 일을 지키세요."},
    "know": {"level": "알아두기", "text": "근거가 약한 신호(시험관 자료·통계)입니다 — 따로 조치할 필요는 없고, 이상 증상이 생기면 "
                                     "상담하세요."},
    "safe": {"level": "조치 없음", "text": "정해진 용법·용량을 지키세요."},
}


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


def _age_hit(base: str | None, age: float) -> bool:
    """DUR age bases: '18세 이하', '만 12세 미만', '6개월 미만', '65세 이상'."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(세|개월)\s*(이하|미만|이상|초과)", base or "")
    if not m:
        return False
    v = float(m.group(1)) / (12 if m.group(2) == "개월" else 1)
    return {"이하": age <= v, "미만": age < v, "이상": age >= v, "초과": age > v}[m.group(3)]


DILUENTS = frozenset({"sodium chloride", "dextrose", "glucose", "water", "sterile water", "lactated ringer's"})


def _diluent_pair(a: Ingredient, b: Ingredient) -> bool:
    """IV saline / dextrose (the vehicle of an injection: 'Midazolam in 0.9% Sodium Chloride') with another drug -
    except lithium, whose clearance does depend on sodium."""
    return (a.bare in DILUENTS or b.bare in DILUENTS) and "lithium" not in (a.bare, b.bare)


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
