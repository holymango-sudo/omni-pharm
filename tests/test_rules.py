"""Clinical rules as data (src/omnipharm/rules/*.json): every rule has a source, a known level, known names and a
review record; a reviewer's request blocks the build until a developer acts on it."""

import re

import pytest

from omnipharm import knowledge as K
from omnipharm import recreational as R
from omnipharm import rules

STOP = re.compile(r"(복용을|약을)\s*(멈추|중단)|중단하세요|끊으세요|그만\s*드세요")


def _known_names() -> set[str]:
    known = set(K.DRUG_GROUPS) | set(K.DRUG_CLASSES) | set(K.BIOLOGICS) | set(K.FOODS) | set(R.SUBSTANCES)
    for _, groups in K.NAME_GROUPS:
        known |= set(groups)
    for table in (K.BIOLOGICS, K.FOODS, R.SUBSTANCES):
        for v in table.values():
            known |= set(v.get("groups", ())) | set(v.get("parts", ()))
            db = v.get("db")
            known |= set(db) if isinstance(db, list) else {db} if db else set()
    for members in K.DRUG_GROUPS.values():
        known |= set(members)
    for _, members in K.DRUG_CLASSES.values():
        known |= set(members)
    return known | set(K.KR_INGREDIENTS.values()) | set(K.DRUG_SYNONYMS.values())


KNOWN = _known_names()


@pytest.mark.parametrize("name", rules.FILES)
def test_every_rule_has_source_and_review_record(name):
    doc = rules.load(name)
    assert doc["rules"], name
    for r in doc["rules"]:
        if name != "rule_details":  # explanations inherit the source of their pair rule
            assert r.get("source", "").strip(), (name, r)
        if "evidence" in r:
            assert r["evidence"] in rules.EVIDENCE, (name, r["evidence"])
        assert r.get("since", "").startswith("v2."), (name, r)
        rv = r["review"]
        assert rv["status"] in rules.REVIEW_STATUS, (name, rv)
        assert rv["date"] is None or re.fullmatch(r"\d{4}-\d{2}-\d{2}", rv["date"]), (name, rv)
        if rv["status"] != "미검토":
            assert rv["reviewer"] and rv["date"], f"{name}: a review decision needs the reviewer and date: {r}"


@pytest.mark.parametrize("name", rules.FILES)
def test_no_unhandled_review_request(name):
    """A reviewer marked a rule '수정 필요' / '삭제 필요': change or delete it, then set the status back (see
    docs/CONTRIBUTING.md). Until then the build fails, so a review result cannot be silently ignored."""
    pending = [(r.get("a") or r.get("disease"), r.get("b") or r.get("group"), r["review"]["note"])
               for r in rules.rules(name) if r["review"]["status"] in ("수정 필요", "삭제 필요")]
    assert not pending, pending


def test_pair_rules_levels_names_unique():
    seen = set()
    for r in rules.rules("pair_rules"):
        assert r["level"] in rules.LEVELS["pair_rules"], r
        assert r["a"] in KNOWN and r["b"] in KNOWN, (r["a"], r["b"])
        key = (r["a"], r["b"])
        assert key not in seen and key[::-1] not in seen, f"duplicate rule {key}"
        seen.add(key)
        assert not STOP.search(r["reason"]), r["reason"]


def test_danger_rules_are_documented():
    """'높음' (-> verdict 위험) only from a documented source - never from 'source unknown'."""
    for r in rules.rules("pair_rules"):
        if r["level"] == "높음":
            assert r["evidence"] != rules.UNKNOWN_SOURCE and r["source"] != rules.UNKNOWN_SOURCE, r


def test_disease_rules():
    seen = set()
    for r in rules.rules("drug_disease_rules"):
        assert r["level"] in rules.LEVELS["drug_disease_rules"], r
        assert r["disease"] in K.DISEASES, r["disease"]
        assert r["group"] in KNOWN, r["group"]
        assert (r["disease"], r["group"]) not in seen
        seen.add((r["disease"], r["group"]))


def test_conditional_rules():
    for r in rules.rules("conditional_rules"):
        assert r["level"] in rules.LEVELS["conditional_rules"], r
        assert r["when"] in K.DISEASES and r["a"] in KNOWN and r["b"] in KNOWN, r
        assert r["reason"].count("{b}") == 1 and r["reason"].format(b="X"), r["reason"]
        assert not STOP.search(r["reason"]), r["reason"]
    assert len(K.CONDITIONAL_RULES) == len(rules.rules("conditional_rules"))


def test_details_belong_to_rules_and_never_say_stop():
    pairs = {(r["a"], r["b"]) for r in rules.rules("pair_rules")}
    shared = rules.load("rule_details")["shared"]
    for r in rules.rules("rule_details"):
        assert (r["a"], r["b"]) in pairs, f"explanation without a rule: {r['a']} + {r['b']}"
        assert r["short"] and r["why"] and r["do"], r
        assert not r["signs"].startswith("$") or r["signs"][1:] in shared, r["signs"]
        for t in [r["short"], *r["do"]]:
            assert not STOP.search(t), t


def test_notes_do_not_contradict_rules():
    pairs = {frozenset((r["a"], r["b"])) for r in rules.rules("pair_rules")}
    for r in rules.rules("pair_notes"):
        assert frozenset((r["a"], r["b"])) not in pairs, f"'no interaction' note for a pair with a rule: {r}"


def test_review_workbook_round_trip(tmp_path, monkeypatch):
    """export -> a pharmacist fills '판정' -> import writes the review record into a copy of the rule files."""
    import importlib.util
    import shutil
    from pathlib import Path

    from openpyxl import load_workbook

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("rrs", root / "scripts" / "rules_review_sheet.py")
    rrs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rrs)
    monkeypatch.setattr(rrs, "OUT", tmp_path / "sheet.xlsx")
    path = rrs.export()
    wb = load_workbook(path)
    ws = wb["상호작용 규칙"]
    head = [c.value for c in ws[1]]
    ws.cell(row=2, column=head.index("판정") + 1, value="수정 필요")
    ws.cell(row=2, column=head.index("의견") + 1, value="문구를 더 구체적으로")
    ws.cell(row=3, column=head.index("판정") + 1, value="동의")
    wb.save(path)

    copy = tmp_path / "rules"
    shutil.copytree(rules.DIR, copy, ignore=shutil.ignore_patterns("*.py", "__pycache__"))
    monkeypatch.setattr(rules, "DIR", copy)
    rules.load.cache_clear()
    try:
        assert rrs.import_(path, "검토 약사", "2026-10-17") == {"수정 필요": 1, "동의": 1}
        first, second = rules.rules("pair_rules")[:2]
        assert first["review"] == {"status": "수정 필요", "reviewer": "검토 약사", "date": "2026-10-17",
                                   "note": "문구를 더 구체적으로"}
        assert second["review"]["status"] == "동의"
        with pytest.raises(AssertionError):  # the request blocks the build until handled
            test_no_unhandled_review_request("pair_rules")
    finally:
        rules.load.cache_clear()


def test_loader_shapes_match_engine_use():
    assert K.PAIR_RULES and all(len(t) == 4 for t in K.PAIR_RULES)
    assert all(isinstance(k, tuple) and {"short", "why", "do", "signs"} <= set(v) for k, v in K.RULE_DETAIL.items())
    assert not any(v["signs"].startswith("$") for v in K.RULE_DETAIL.values())
    assert all(len(t) == 3 for t in K.PAIR_NOTES + K.INTENDED_COMBINATIONS)
    assert len(K.RULE_PROVENANCE) == len(K.PAIR_RULES)
    assert rules.provenance("pair_rules", "warfarin", "vitamin_k_food")["evidence"] == "허가사항 경고·주의"
