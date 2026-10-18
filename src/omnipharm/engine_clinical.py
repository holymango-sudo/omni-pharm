"""Patient-level clinical output (ClinicalMixin of Engine): drug-disease / age / pregnancy alerts and
the plain-language summary (verdict, action, reasons, advice)."""

from __future__ import annotations

import re

from omnipharm.clinical import Patient
from omnipharm.engine_base import (
    ACTIONS,
    COMBO_X,
    MIN_P,
    SIGNAL_P,
    Ingredient,
    _age_hit,
    _combo,
)
from omnipharm.engine_labels import (
    _is_contra,
    _own,
)
from omnipharm.knowledge import (
    CONDITIONAL_RULE_SOURCE,
    CONDITIONAL_RULES,
    DISEASE_LABEL_TERMS,
    DISEASE_RULE_SOURCE,
    DISEASES,
    DRUG_DISEASE_RULES,
    KR_LABEL_KINDS,
    ORGANS,
    drug_groups,
)

# DUR age rules name the dosage forms they cover. When only the ingredient was entered (no product, so no form), a
# rule that covers ordinary tablets / capsules applies (that is what an ingredient name usually means); one that
# covers only other routes (nasal spray, injection, cream) does not concern a medicine taken by mouth; the rest
# (extended-release forms, syrups) stays a caution to check the form.
_PLAIN_ORAL = re.compile(r"^(정제|정|캡슐|캡슐제|경질캡슐|연질캡슐|필름코팅정|당의정|나정|저작정|구강붕해정|발포정)$")
_NOT_ORAL = re.compile(r"주사|분무|크림|연고|겔|로션|외용|점안|점이|점비|질|좌제|좌약|패취|패치|흡입|피부|직장|이식|세정|틴크|"
                       r"카타플라스마|플라스타")


def _age_form_unknown(form: str) -> str:
    """-> 'applies' | 'skip' | 'check' for a DUR age rule limited to `form` when the product's form is unknown."""
    toks = [t.strip() for t in re.split(r"[/,]", form) if t.strip()]
    if any(_PLAIN_ORAL.match(t) for t in toks):
        return "applies"
    if toks and all(_NOT_ORAL.search(t) for t in toks):
        return "skip"
    return "check"


class ClinicalMixin:
    # ------------------------------------------------------------------ drug-disease + plain summary
    def _drug_disease(self, U: list[Ingredient], pa: dict, patient: Patient,
                      forms: dict[str, list[tuple[str | None, str]]] | None = None) -> list[dict]:
        """Which of these drugs are especially dangerous for THIS patient's diseases / age / kidneys.
        Curated rules (Beers 2023 drug-disease table, labelling) + FDA label contraindication / boxed text.
        forms = ingredient key -> [(product name or None, release form)] of the products in this analysis."""
        forms = forms or {}
        dis = set(pa["diseases"])
        # patient groups some labels contraindicate (not diseases: never shown as one)
        if patient.age is not None and patient.age < 12:
            dis.add("child")
        if (patient.sex or "").upper().startswith("F"):
            dis.add("female")
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
                    add(dkey, g, sev, reason, DISEASE_RULE_SOURCE[(dkey, grp)])
            # a partner drug that makes this one dangerous only in this patient (colchicine + verapamil, kidneys)
            for a_grp, b_grp, when, sev, reason in CONDITIONAL_RULES:
                if when in dis and a_grp in groups:
                    partners = [self.ko(h) for h in U if h.key != g.key and b_grp in drug_groups(h.key)]
                    if partners:
                        add(when, g, sev, reason.format(b=", ".join(partners)), CONDITIONAL_RULE_SOURCE[(a_grp, b_grp, when)])
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
                if patient.age is None or not _age_hit(r.get("age_base"), patient.age):
                    continue
                why = f"식약처 DUR 특정연령대금기 — {r.get('content') or '이 연령대에서 쓰지 않습니다'}"
                form = (r.get("form") or "").strip()
                if form:  # the rule covers some dosage forms only (acetaminophen: 서방정 — not a children's syrup)
                    toks = [t.strip() for t in form.split("/") if t.strip()]
                    mine = forms.get(g.key, [])
                    hit = any(n and any(t in n.replace(" ", "") for t in toks) for n, _ in mine) or (
                        "서방" in form and any(rel == "ER" for _, rel in mine))
                    if not hit:
                        if mine and all(n for n, _ in mine):  # known products, none of that form: not concerned
                            continue
                        unknown = _age_form_unknown(form)  # ingredient only: which form is meant is not known
                        if unknown == "skip":
                            continue
                        if unknown == "check":
                            add(f"연령({r['age_base']})", g, "주의", f"{why} ({form}에만 해당 — 제품의 제형을 확인하세요)",
                                "식약처 DUR")
                            continue
                add(f"연령({r['age_base']})", g, "금기", why, "식약처 DUR")
            if "pregnancy" in dis:
                for r in self.dur_by.get("pregnancy", {}).get(b, [])[:1]:
                    one = "1" in (r.get("grade") or "")
                    add("pregnancy", g, "금기" if one else "주의",
                        f"식약처 DUR 임부금기 {r.get('grade') or ''} — {r.get('content') or '임부에 대한 안전성 미확립'}"
                        + (" (사용 금지)" if one else " (치료상 유익성이 클 때만)"), "식약처 DUR")
            if patient.age is not None and patient.age >= 65:
                for r in self.dur_by.get("elderly", {}).get(b, [])[:1]:
                    why = (r.get("content") or "").strip()[:160] or "고령자는 부작용이 더 잘 나타날 수 있어 신중히 사용"
                    add("고령(65세 이상)", g, "주의", f"식약처 DUR 노인주의 — {why}", "식약처 DUR")
        return sorted(found.values(), key=lambda d: -rank[d["severity"]])

    def _summary(self, overall, duplicates, class_dups, disease_alerts, pair_rows, events, pa,
                 dosing=None, prn_keys=frozenset(), ctl_keys=frozenset()) -> dict:
        """Plain-language verdict for people who are not clinicians: what is wrong and what to do.
        ctl_keys = ingredients of recreational / controlled substances: 'do not combine', never 'adjust the dose'."""
        reasons: list[dict] = []
        advice: list[str] = []

        def avoid(r: dict) -> str | None:
            if r["a_key"] in ctl_keys or r["b_key"] in ctl_keys:
                return f"{r['a']}과(와) {r['b']}을(를) 함께 쓰지 마세요 — 위험 신호가 보이면 바로 119."
            return None
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
        details: list[dict] = []  # plain-language explanations (knowledge.RULE_DETAIL) of the interactions found
        for r in pair_rows:
            own = _own(r["label_evidence"])
            if r["level"] == "낮음":
                note = self._pair_note(r)
                if any(e.get("context") == "no_interaction" for e in r["label_evidence"]):
                    reasons.append({"sev": "low", "icon": "✅",
                                    "text": f"{r['a']}과(와) {r['b']}은(는) 허가 문서에 '임상적으로 의미 있는 상호작용 없음'으로 "
                                            "적혀 있습니다."})
                elif note:  # 'vitamin K + apixaban': people ask - say it does not interact
                    reasons.append({"sev": "low", "icon": "✅", "text": f"{r['a']} + {r['b']}: {note}."})
                continue
            if r.get("dur"):
                why = f" (사유: {r['dur']['content']})" if r["dur"].get("content") else ""
                reasons.append({"sev": "high", "icon": "⛔",
                                "text": f"{r['a']}과(와) {r['b']}은(는) 식약처 DUR 병용금기 성분 조합입니다{why}."})
                advice.append(f"{r['a']}과(와) {r['b']}을(를) 함께 복용하지 마세요 (식약처 병용금기).")
            elif r["level"] == "높음" and any(e["section"] == "병용금기/금기" and _is_contra(e) for e in own):
                reasons.append({"sev": "high", "icon": "⛔",
                                "text": f"{r['a']}과(와) {r['b']}은(는) 허가 문서에 함께 쓰지 말라고 되어 있습니다."})
                advice.append(avoid(r) or f"{r['a']}과(와) {r['b']}을(를) 함께 복용하지 마세요.")
            elif r["level"] == "높음" and not own and any(m.get("severe") for m in r.get("mechanism", [])):
                m = next(m for m in r["mechanism"] if m.get("severe"))
                reasons.append({"sev": "high", "icon": "🧬", "text": f"{r['a']} + {r['b']}: {m['text']}."})
                advice.append(avoid(r) or f"{r['a']}과(와) {r['b']}은(는) 보통 함께 쓰지 않거나 용량을 크게 줄입니다. "
                                           "의사·약사와 상의하세요.")
            elif r["level"] == "높음" and own:
                reasons.append({"sev": "high", "icon": "📄",
                                "text": f"{r['a']}과(와) {r['b']}은(는) 허가 문서에 '피하거나 용량을 줄여야 하는' "
                                        f"중대한 상호작용으로 적혀 있습니다."})
                advice.append(avoid(r) or f"{r['a']}과(와) {r['b']}을(를) 함께 써야 한다면 용량 조절·검사 계획을 "
                                           "의사·약사와 정하세요.")
            elif not own and r.get("mechanism"):
                m = next((m for m in r["mechanism"] if m.get("detail")), r["mechanism"][0])
                src = {"규칙": "임상 규칙", "식약처허가": "식약처 허가사항"}.get(m["kind"], "약리 기전")
                if m["kind"] in KR_LABEL_KINDS:  # a product's own label: the text already names both sides
                    reasons.append({"sev": "mid", "icon": "📋", "text": f"{m['text']}."})
                    continue
                if m.get("detail"):  # how it works and what goes wrong, in plain words; its own advice
                    details.append(m["detail"])
                reasons.append({"sev": "mid", "icon": "📋" if m["kind"] == "규칙" else "🧬",
                                "text": f"{r['a']} + {r['b']} ({src}): "
                                        f"{m['detail']['why'] if m.get('detail') else m['text']}."})
            elif own:
                occasional = r["a_key"] in prn_keys or r["b_key"] in prn_keys
                rule_m = next((m for m in r.get("mechanism", []) if m["kind"] in ("규칙", *KR_LABEL_KINDS, "CYP-FDA")),
                              None)
                rule = rule_m and (rule_m["detail"]["why"] if rule_m.get("detail") else rule_m["text"])
                if rule_m and rule_m.get("detail"):
                    details.append(rule_m["detail"])
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
            # the model learned which events are REPORTED (FAERS), so it is said as a report statistic, not as a risk
            if _combo(e) >= COMBO_X:
                text = (f"{organ} (AI 예측·부작용 보고 통계): 비슷한 환자의 부작용 보고에서 함께 쓸 때 '{ev}' 보고가 한 가지만 "
                        "쓸 때보다 3배 넘게 많습니다 — 위험을 직접 잰 값은 아니지만, 문서에 없는 상호작용일 수 있는 신호입니다.")
            else:
                text = (f"{organ} (AI 예측·부작용 보고 통계): 이 환자와 비슷한 사람의 보고에서 '{ev}' 보고가 평균보다 "
                        "많습니다 — 위험을 직접 잰 값은 아닙니다.")
            reasons.append({"sev": "mid", "icon": "📈", "text": text})
        top = [e for e in events if e["serious"] and e["lift"] >= 2 and e["prob"] >= MIN_P
               and not e.get("drives_verdict") and not e.get("indication_any")][:max(0, 2 - len(drivers))]
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
                text, sev = (f"{organ} (AI 참고): 이 환자와 비슷한 나이·몸 상태의 보고에서 '{ev}' 보고가 평균보다 다소 "
                             "많습니다 — 약 조합 때문이 아니라 환자 특성에 따른 통계입니다(알아두기).", "low")
            else:
                # a drug's own risk: information, not an interaction warning - shown only when OFFSIDES (single-drug
                # analysis against matched reports) finds the same signal; otherwise it may be confounding
                # (acetaminophen 'seizure' / 'pancreatitis' came from the patients who take it, not from the drug)
                if self.offsides and not e.get("own_signal"):
                    continue
                drv = f"{e['driver']} " if e.get("driver") else ""
                text, sev = (f"{organ}: '{ev}'은(는) {drv}약 자체에 따르는 위험입니다 — 복용 중 증상을 살펴보세요"
                             + (" (단일 약 보고 분석 OFFSIDES에서도 확인)." if self.offsides else "."), "low")
            reasons.append({"sev": sev, "icon": "📈", "text": text})
        cap = pa.get("capacity")
        if cap and cap["level"] in ("저하", "매우 저하"):
            reasons.append({"sev": "mid", "icon": "🫀",
                            "text": f"몸의 약물 처리 능력이 '{cap['level']}' 상태라 약이 몸에 쌓이기 쉽습니다."})
            if ctl_keys:  # a recreational substance: say what it means for the body, never an amount for it
                advice.append("몸이 약을 처리하는 힘이 약해 같은 물질도 더 강하고 오래 작용할 수 있습니다 — 처방약의 용량은 "
                              "의사·약사와 확인하세요.")
            elif any("함께 복용하지 마세요" in a for a in advice):  # a contraindicated pair is not fixed by a lower dose
                advice.append("함께 먹지 말아야 할 조합과 별개로, 약을 처리하는 힘이 약해 계속 드시는 약도 용량 조절이 "
                              "필요할 수 있습니다 — 의사·약사와 확인하세요.")
            else:
                advice.append("용량을 줄이거나 복용 간격을 늘려야 할 수 있습니다 — 의사·약사와 확인하세요.")
        order = {"high": 0, "mid": 1, "low": 2}
        reasons = sorted(reasons, key=lambda r: order[r["sev"]])[:6]
        verdict = {"높음": "위험", "주의": "주의", "낮음": "비교적 안전"}[overall]
        headline = {"위험": "이 약 조합은 이 환자에게 위험할 수 있습니다.",
                    "주의": "주의가 필요한 조합입니다. 아래 이유를 확인하세요.",
                    "비교적 안전": "뚜렷한 위험 신호는 없습니다. 복약 안내는 그대로 지켜 주세요."}[verdict]
        # one documented interaction with a plain explanation and nothing more serious: its takeaway IS the headline
        # ('함께 먹어도 되지만 비타민K 음식은 매일 비슷한 양을…' instead of 'check the reasons below'). An AI signal of
        # the combination itself competes with it; one from this patient's age / body alone (falls at 65) does not
        combo_ai = any(_combo(e) >= COMBO_X for e in drivers)
        if (verdict == "주의" and len(details) == 1 and sum(r["level"] != "낮음" for r in pair_rows) == 1
                and not (duplicates or class_dups or disease_alerts or combo_ai or (dosing or {}).get("alerts"))):
            headline = details[0]["short"]
        if ctl_keys:  # harm reduction instead of 'keep to the labelled dose' (kept within the 4 lines shown)
            advice.insert(min(len(advice), 3), "혼자 쓰지 말고 다른 약·술과 섞지 마세요 — 위험 신호가 보이면 바로 119 "
                                               "(마약류 24시간 익명 상담 1342).")
        # the explained pairs' own steps, alternating between pairs (every pair's first step first), and their warning
        # signs once at the end - never cut off by the line limit
        steps = [d["do"][i] for i in range(max((len(d["do"]) for d in details), default=0))
                 for d in details if i < len(d["do"])]
        signs = list(dict.fromkeys(d["signs"] for d in details if d.get("signs")))
        if not advice and not steps:
            # never 'stop the medicine': stopping warfarin, an antiepileptic or a heart medicine on one's own is dangerous
            advice.append("새로운 증상이 생기면 의사·약사와 상의하세요 — 처방받은 약은 임의로 끊지 마세요." if verdict != "비교적 안전"
                          else "정해진 용법·용량을 지키세요.")
        cap = 5 if details else 4
        advice = list(dict.fromkeys(advice + steps))[:cap - len(signs)] + signs
        # what to DO, by the strength of the evidence behind the verdict: a documented interaction / rule / duplicate /
        # disease or dose alert needs checking; in-vitro pharmacology or a statistical signal alone is to know about
        documented_any = (bool(duplicates or class_dups or disease_alerts or (dosing or {}).get("alerts")) or any(
            r["level"] != "낮음" and (r.get("dur") or _own(r["label_evidence"]) or any(
                m["kind"] in ("규칙", "CYP-FDA", *KR_LABEL_KINDS) for m in r.get("mechanism", [])))
            for r in pair_rows))
        action = (ACTIONS["위험"] if verdict == "위험" else ACTIONS["safe"] if verdict == "비교적 안전"
                  else ACTIONS["check"] if documented_any else ACTIONS["know"])
        return {"verdict": verdict, "headline": headline, "reasons": reasons, "advice": advice, "action": action}
