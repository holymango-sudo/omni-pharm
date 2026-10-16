"""Clinical rules as data (v2.18): every rule carries its source, the kind of evidence, the version it came in and a
review record. knowledge.py loads them into the shapes the engine has always used (PAIR_RULES tuples ...).

    pair_rules.json             interactions between two groups / ingredients / foods
    drug_disease_rules.json     medicines to avoid or watch in a disease / pregnancy
    rule_details.json           plain-language explanations attached to pair rules
    pair_notes.json             documented NON-interactions people often ask about
    intended_combinations.json  combinations used on purpose (boosters, standard regimens)

Edit the JSON, not code. tests/test_rules.py checks every file (sources, levels, known groups, review status);
scripts/rules_review_sheet.py writes the pharmacist review workbook and reads the decisions back.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

DIR = Path(__file__).resolve().parent
FILES = ("pair_rules", "drug_disease_rules", "rule_details", "pair_notes", "intended_combinations")
LEVELS = {"pair_rules": ("높음", "주의"), "drug_disease_rules": ("금기", "주의")}
EVIDENCE = ("병용금기(허가)", "박스경고", "허가사항 경고·주의", "규제기관 권고", "표시사항(일반약·건강기능식품)", "임상 연구",
            "증례·문헌", "위해 감소 문헌", "약리 원칙", "출처 확인 필요")
REVIEW_STATUS = ("미검토", "동의", "수정 필요", "삭제 필요")
UNKNOWN_SOURCE = "출처 확인 필요"


@cache
def load(name: str) -> dict:
    return json.loads((DIR / f"{name}.json").read_text(encoding="utf-8"))


def rules(name: str) -> list[dict]:
    return load(name)["rules"]


def pair_rules() -> list[tuple[str, str, str, str]]:
    """(group a, group b, '높음'|'주의', plain-language reason), in checking order."""
    return [(r["a"], r["b"], r["level"], r["reason"]) for r in rules("pair_rules")]


def drug_disease_rules() -> list[tuple[str, str, str, str]]:
    """(disease, drug group, '금기'|'주의', plain-language reason)."""
    return [(r["disease"], r["group"], r["level"], r["reason"]) for r in rules("drug_disease_rules")]


def rule_details() -> dict[tuple[str, str], dict]:
    """(group a, group b) of a pair rule -> {short, why, do, signs}."""
    shared = load("rule_details").get("shared", {})
    return {(r["a"], r["b"]): {"short": r["short"], "why": r["why"], "do": list(r["do"]),
                               "signs": shared[r["signs"][1:]] if r["signs"].startswith("$") else r["signs"]}
            for r in rules("rule_details")}


def pair_notes() -> list[tuple[str, str, str]]:
    return [(r["a"], r["b"], r["text"]) for r in rules("pair_notes")]


def intended_combinations() -> list[tuple[str, str, str]]:
    return [(r["a"], r["b"], r["why"]) for r in rules("intended_combinations")]


def provenance(name: str, a: str, b: str) -> dict | None:
    """Source / evidence / review record of one rule (either order of a, b; disease rules: a = disease, b = group)."""
    for r in rules(name):
        x, y = (r["disease"], r["group"]) if name == "drug_disease_rules" else (r["a"], r["b"])
        if (x, y) == (a, b) or (name != "drug_disease_rules" and (y, x) == (a, b)):
            return {k: r[k] for k in ("source", "evidence", "since", "review") if k in r}
    return None
