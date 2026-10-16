"""Documented evidence for a pair of ingredients (EvidenceMixin of Engine): FDA label text, class rules
(rules/pair_rules.json), FDA CYP index tables, Korean label 'do not take with' items, pharmacological mechanisms,
and the pair's documented level."""

from __future__ import annotations

import re

import numpy as np

from omnipharm.engine_base import (
    ALLERGY_CONTEXT,
    ASK_DOCTOR,
    CYP_INHIBITION_P,
    HERG_P,
    SEVERE_LABEL,
    Ingredient,
    _diluent_pair,
    _minor,
)
from omnipharm.engine_labels import (
    _about,
    _context,
    _is_contra,
    _is_strong,
    _own,
    _same_moiety,
)
from omnipharm.knowledge import (
    BIOLOGICS,
    CYP_FDA,
    EQUIACTIVE_METABOLITE,
    FOODS,
    PAIR_NOTES,
    PAIR_RULES,
    PRODRUGS_ACTIVATED_BY,
    RULE_DETAIL,
    RULE_PROVENANCE,
    WIDE_MARGIN_SUBSTRATES,
    class_phrases,
    drug_groups,
)

PATHOGEN_TARGET = re.compile(r"virus|viral|\bHIV|hepatitis|bacteri|penicillin-binding|plasmodium|mycobacter|fung|"
                             r"ergosterol|parasit", re.I)


def _serotonergic(moa: list[dict]) -> bool:
    """A mechanism that INCREASES serotonin activity (serotonin syndrome): transporter / MAO inhibition, full receptor
    agonism or release. Antagonists and partial agonists at 5-HT receptors do not."""
    for m in moa:
        t, act = str(m.get("text", "")).lower(), str(m.get("action", "")).upper()
        if ("serotonin transporter" in t or "monoamine oxidase" in t) and act == "INHIBITOR":
            return True
        if "serotonin" in t and act in ("AGONIST", "RELEASING AGENT"):
            return True
    return False


class EvidenceMixin:
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
            if _same_moiety(src.bare, other.bare):
                continue  # 'Testosterone can cause virilization' in a testosterone undecanoate label is about itself
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
                    if not any(t.lower() in excerpt.lower() for t in terms):
                        continue  # matched only through a word stem ('estrogens' ~ 'estrogen-containing'): no excerpt
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
            own_class = set(class_phrases(other.key, label_of=src.key)) if phrases else set()
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
                                "own_class": via in own_class,
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
                # venlafaxine -> O-desmethylvenlafaxine is as active: the total active moiety barely changes
                equiactive = EQUIACTIVE_METABOLITE.get(vn) == enz or vn in WIDE_MARGIN_SUBSTRATES
                if wn in t["strong_inh"] or wn in t["moderate_inh"]:
                    strong = wn in t["strong_inh"]
                    effect = (f"{self.ko(v)}의 활성화가 줄어 효과가 떨어질 수 있습니다" if prodrug else
                              f"{self.ko(v)}의 혈중 농도가 {'5배 이상' if strong and sens else '2~5배'} 오를 수 있습니다")
                    out.append({"kind": "CYP-FDA", "severe": strong and sens and not prodrug and not equiactive,
                                "text": f"{self.ko(w)}은(는) {enz} {'강한' if strong else '중간'} 억제제 → {effect} (FDA 지표 약물표)"})
                elif wn in t["strong_ind"] or wn in t["moderate_ind"]:
                    # a strong inducer lowers a sensitive substrate's exposure by >= 80% (FDA index definition): the
                    # medicine may stop working (rifampin + HIV / anticoagulant / cancer drugs) - as major as the
                    # inhibitor case; a prodrug is activated MORE, a wide-margin drug barely matters
                    strong = wn in t["strong_ind"]
                    out.append({"kind": "CYP-FDA", "severe": strong and sens and not prodrug and not equiactive,
                                "text": f"{self.ko(w)}은(는) {enz} {'강한 ' if strong else ''}유도제 → {self.ko(v)}의 농도가 "
                                        f"{'80% 넘게 ' if strong and sens else ''}떨어져 효과가 줄 수 있습니다 "
                                        "(FDA 지표 약물표)"})
        return out

    def _kr_label(self, a: Ingredient, b: Ingredient, kr_avoid: dict[str, list[tuple]]) -> list[dict]:
        """Korean OTC label of a product in this analysis: '이 약을 복용하는 동안 다음의 약을 복용하지 말 것: 진정제' and the
        other ingredient belongs to that class (knowledge.KR_AVOID_TERMS). The text names the product (the rule is
        the product's), and the summary shows it as it is."""
        out = []
        for x, y in ((a, b), (b, a)):
            for product, item, targets, level, own, hff in kr_avoid.get(x.key, []):
                if y.key in own:  # the same product: an intended combination
                    continue
                if targets & (drug_groups(y.key) | {y.bare}):
                    said = (f"제품 표시사항(식약처 건강기능식품정보)에 '의약품({item}) 복용 시 섭취에 주의'" if hff else
                            f"제품 설명서(식약처 허가사항)에 '이 약을 복용하는 동안 {item}을(를) 복용하지 말 것'")
                    out.append({"kind": "식약처표시" if hff else "식약처허가", "severe": level == "높음",
                                "text": f"{product} {said}로 되어 있고, {self.ko(y)}은(는) 여기에 해당합니다"})
        return list({m["text"]: m for m in out}.values())[:2]

    @staticmethod
    def _pair_rules(a: Ingredient, b: Ingredient) -> list[dict]:
        """Class-level interactions stated in labelling (knowledge.PAIR_RULES), e.g. GLP-1 + insulin."""
        ga, gb = drug_groups(a.key), drug_groups(b.key)
        return [{"kind": "규칙", "severe": sev == "높음", "text": text, "source": RULE_PROVENANCE[(x, y)]["source"],
                 "review": RULE_PROVENANCE[(x, y)]["review"]["status"],
                 **({"detail": RULE_DETAIL[(x, y)]} if (x, y) in RULE_DETAIL else {})}
                for x, y, sev, text in PAIR_RULES if (x in ga and y in gb) or (x in gb and y in ga)]

    def _pair_note(self, r: dict) -> str | None:
        """A documented 'no interaction' note for a pair people often ask about (knowledge.PAIR_NOTES)."""
        ga, gb = drug_groups(r["a_key"]), drug_groups(r["b_key"])
        return next((t for x, y, t in PAIR_NOTES if (x in ga and y in gb) or (x in gb and y in ga)), None)

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
        # a PATHOGEN's target shared by two anti-infectives (HIV reverse transcriptase: NRTI + NNRTI) is how
        # combination therapy works, not an overlap of effects in the patient
        ta = {(m["target"], m["action"]) for m in pa["moa"] if m.get("target") and not PATHOGEN_TARGET.search(
            str(m.get("text", "")))}
        tb = {(m["target"], m["action"]) for m in pb["moa"] if m.get("target")}
        same = ta & tb
        if same:
            txt = next((m["text"] for m in pa["moa"] if (m["target"], m["action"]) in same), "같은 작용점")
            out.append({"kind": "PD", "text": f"두 약의 작용 기전이 같습니다({txt}) → 효과·부작용이 겹칩니다"})
        # serotonin syndrome needs MORE serotonin activity on both sides (reuptake / MAO inhibition, receptor
        # agonism, release) - a 5-HT2A antagonist (aripiprazole, risperidone, mirtazapine) does not add to it
        if not same and _serotonergic(pa["moa"]) and _serotonergic(pb["moa"]):
            out.append({"kind": "PD", "text": "두 약 모두 세로토닌 작용을 늘림 → 세로토닌 증후군(고열·떨림·혼돈) 위험이 있습니다"})
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
        lev = [] if (_minor(A) and _minor(B)) or _diluent_pair(A, B) else self._label_evidence(A, B)
        for e in lev:
            e["context"] = _context(e)
        own = _own(lev)
        contra = any(_is_contra(e) for e in own)
        strong = any(_is_strong(e) for e in own)
        mech = self._mechanism(A, B)
        intended = frozenset((A.bare, B.bare)) in self.combo_pairs
        if intended:  # same rules as predict(): an approved combination's pharmacology is its purpose
            mech = [m for m in mech if m["kind"] not in ("CYP-FDA", "CYP", "QT", "PD")]
        dur = self.dur_pairs.get(frozenset((A.bare, B.bare)))
        if dur:
            return "높음", "식약처 DUR 병용금기" + (f" — {dur['content']}" if dur.get("content") else "")
        severe = next((m["text"] for m in mech if m.get("severe")), None)
        if (contra or strong or severe) and not (intended and not severe):
            return "높음", severe or "허가 문서에 피해야 하는 조합으로 기재"
        if own or mech:
            return "주의", mech[0]["text"] if mech else "허가 문서에 상호작용으로 기재"
        return "낮음", None
