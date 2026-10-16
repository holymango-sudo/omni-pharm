"""Reading FDA label text: is a sentence that names the other drug an interaction warning, a
contraindication, strong wording - or something else (allergy, the drug's own class, a cross-reference, an
intended combination)? Split from engine.py in v2.18."""

from __future__ import annotations

import re

from omnipharm.engine_base import (
    BOXED_COMBINATION,
    SEVERE_LABEL,
)
from omnipharm.knowledge import (
    BIOLOGICS,
    PGP_INHIBITORS,
    structureless_category,
)


def _about(e: dict, lead: int = 0) -> str:
    """The sentences of a label excerpt that name the other drug (an excerpt may also cover unrelated drugs);
    lead = also include that many sentences before each one (table rows: 'Clinical Impact: ...  Examples: X')."""
    terms = [t.lower() for t in (e.get("terms") or BIOLOGICS.get(e["mentions"], {}).get("terms", [e["mentions"]]))]
    # whole name only, and not part of another product's name ("sacubitril/valsartan" is not valsartan) nor this
    # drug's own salt ("rosuvastatin calcium" is not calcium)
    own = re.escape(e.get("label_of", "").lower())
    pats = [re.compile((rf"(?<!{own} )" if own else "") + r"(?<![\w/])" + re.escape(t) + r"(?![\w/])", re.I)
            for t in terms]
    sents = re.split(r"(?<=[.;!?])\s+|\s+[●•]\s*|\s+\(\s*\d", e["excerpt"])
    keep = set()
    for i, s in enumerate(sents):
        if any(p.search(s) for p in pats):
            keep.update(range(max(0, i - lead), i + 1))
    return " ".join(sents[i] for i in sorted(keep))


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
    r"(drug )?interactions?|(dose|dosage) adjustments? (is|are) not (recommended|needed|necessary|required)", re.I)
# an interaction-section mention must say SOMETHING about an effect (table headings / bare lists do not)
EFFECT_WORDS = re.compile(
    r"increas|decreas|reduc|elevat|lower|rais|risk|monitor|avoid|caution|concomitant|co-?administ|potentiat|"
    r"inhibit|induc|level|concentration|exposure|effect|adjust|toxicit|may |contraindicat|bleeding|"
    r"hypoglyc|hyperkal|serotonin|qt|sedation|interact|serious|fatal|life[- ]threatening|potential for", re.I)
AMOUNT_ONLY = re.compile(r"large (quantities|amounts|volumes)|excessive( \w+){0,2} (consumption|intake|amounts|use|drinking)|"
                         r"binge drinking|heavy (drinking|alcohol use)|risk factors (for [\w\s-]{0,60})?include|"
                         r"more than [\d.,]+ ?(l|liters?|litres?|quarts?|cups?|glasses|drinks)\b|"
                         r"(drastic|sudden|major) changes|consistent (intake|amount|diet)", re.I)
XREF = re.compile(r"for (more |additional |complete |further |specific )?information (about|on|regarding|concerning)",
                  re.I)
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


def _bare(key: str) -> str:
    return key.removeprefix("ing:").removeprefix("bio:").removeprefix("lab:").removeprefix("food:")


def _same_moiety(a: str, b: str) -> bool:
    """One name contains the other as whole words (estradiol / ethinyl estradiol, testosterone / testosterone
    undecanoate): each label's mentions of that name are about its own active moiety."""
    return a != b and bool(re.search(rf"(?<![\w-]){re.escape(a)}(?![\w-])", b) or
                           re.search(rf"(?<![\w-]){re.escape(b)}(?![\w-])", a))


# '[see Contraindications (4)]', 'See CONTRAINDICATIONS, WARNINGS': a cross-reference is not the label's wording
_SEC = r"(contraindications|warnings( and precautions)?|precautions|boxed warning|dosage and administration|" \
       r"drug interactions)"
_XREF_MARK = re.compile(r"\[\s*see [^\]]{0,120}\]|\(\s*see [^)]{0,120}\)|\bsee (the )?" + _SEC
                        + r"(\s*(,|and)\s*" + _SEC + r")*", re.I)
QT_CLASS_USE = re.compile(r"(drugs?|agents?|medications?|medicines?|products?|substances?|those)\b[^.;]{0,60}?"
                          r"\b(that|which|known to|shown to)\s+([\w-]+\s+){0,2}prolong|qtc?[- ]prolonging", re.I)
# a boxed warning about spinal / epidural hematoma during neuraxial anaesthesia (anticoagulants): conditional
BOXED_CONDITIONAL = re.compile(r"neuraxial|epidural|spinal (puncture|anesthesia|anaesthesia|hematoma)", re.I)


def _lead_in(e: dict, n: int = 450) -> str:
    """The section text right before the first mention of the other drug (the list's lead-in)."""
    terms = e.get("terms") or [e["mentions"]]
    m = min((m.start() for t in terms if (m := re.search(r"(?<![\w/])" + re.escape(t) + r"(?![\w/])", e["excerpt"],
                                                         re.I))), default=None)
    return e["excerpt"][max(0, m - n):m] if m is not None else ""


def _is_contra(e: dict) -> bool:
    """A contraindication / boxed-warning statement ABOUT taking the two together."""
    about = _about(e)
    if AMOUNT_ONLY.search(about):
        return False  # 'in the presence of LARGE AMOUNTS of CNS depressants'
    if e["section"] == "병용금기/금기":  # (a list item: the lead-in 'X is contraindicated in patients: • ...' counts)
        if ADDITIVE_HEDGE.search(about):
            return False
        return bool(CONTRA_STRICT.search(_about(e, lead=1)) or CONTRA_STRICT.search(_lead_in(e))
                    or CONTRA_ITEM.search(about))
    return (e["section"] == "박스 경고" and bool(BOXED_COMBINATION.search(about))
            and not BOXED_CONDITIONAL.search(e["excerpt"]))


def _is_strong(e: dict) -> bool:
    """Strong wording (contraindicated / avoid / reduce the dose ...) in the sentences naming the other drug - near
    the name (a table without full stops runs many rows into one 'sentence'), and not a sentence about the label's
    own drug ('... Corticosteroids Theophylline Epinephrine should not be used to counteract circulatory collapse')."""
    if e["section"] not in ("상호작용", "병용금기/금기", "박스 경고"):
        return False
    if e["section"] == "박스 경고" and BOXED_CONDITIONAL.search(e["excerpt"]):
        return False
    about = _XREF_MARK.sub(" ", _about(e))
    if AMOUNT_ONLY.search(about):
        return False
    terms = e.get("terms") or [e["mentions"]]
    pat = "|".join(r"(?<![\w/])" + re.escape(t) + r"(?![\w/])" for t in terms)
    pos = [m.start() for m in re.finditer(pat, about, re.I)]
    own = re.escape(e.get("label_of", "").lower())
    for m in SEVERE_LABEL.finditer(about):
        if pos and min(abs(m.start() - p) for p in pos) > 350:
            continue  # far from the name in a run-on table
        if re.search(pat, about[m.end():m.end() + 80], re.I):
            return True  # 'should avoid ergotamine', 'contraindicated with cyclosporine'
        before = about[max(0, m.start() - 40):m.start()].lower()
        # the label's own drug as the SUBJECT right before it ('... Theophylline Epinephrine should not be used to
        # counteract ...'), not as an object ('use of cyclosporine with pitavastatin is contraindicated'); a table
        # row's 'Intervention: Fosaprepitant is contraindicated' is about that row's drug
        if (own and not m.group(0).lower().startswith("contraindicat") and "intervention" not in before
                and re.search(rf"(?<!with )(?<!of )(?<!and ){own}[\w\s(),-]{{0,20}}$", before)
                and not re.search(pat, before, re.I)):
            continue
        return True
    return False


# "When using fluoxetine and olanzapine in combination, also refer to the Contraindications section of ... Symbyax":
# a pointer to the combination's own label (the combination is intended), not a contraindication of the pair
COMBINATION_USE = re.compile(
    r"when (using|used|administered|given|taking|coadministered) [^.;]{0,80}?\bin combination\b|"
    r"in combination,? (also )?refer to|also refer to the [^.;]{0,60}(contraindications section|package inserts?|"
    r"prescribing information)|"
    # a boxed warning describing the approved regimen: 'patients receiving ONIVYDE in combination with fluorouracil'
    r"patients (receiving|treated with) [\w-]+ in combination with|"
    # 'Due to the need for co-administration of darunavir with ritonavir, refer to ritonavir prescribing information'
    r"need for co-?administration|must be co-?administered with|"
    # a medication-error warning about NAMES ('avoid cisplatin overdose due to confusion with carboplatin')
    r"confusion (with|between)|mix-?ups?|look-?alike|sound-?alike", re.I)
# a contraindication-section sentence must itself forbid something (old labels file 'Synergistic effects: ... may
# produce additive CNS depressant effects' under contraindications)
CONTRA_STRICT = re.compile(r"contraindicat|do not|must not|should not|not (be )?(used|given|administered|"
                           r"co-?administered|taken|combined)", re.I)
# a contraindication sentence that names the other drug only as an ALLERGY ('hypersensitivity to aspirin or other
# NSAIDs', 'history of sensitivity to benzodiazepines') - not about taking the two together
SENSITIVITY = re.compile(r"sensitivity to|allerg|hypersensitiv|anaphyla|asthma|urticaria|bronchospasm", re.I)
TAKING_TOGETHER = re.compile(r"concomitant|co-?administ|in patients (who are )?(taking|receiving|treated with)|"
                             r"while (taking|receiving)|currently (taking|receiving)|patients (taking|receiving)|"
                             r"\b(are|is) (now |currently )?(taking|receiving|using)\b", re.I)
# an 'additive effect' remark filed under contraindications by an old label ('The concomitant use of alcohol or other
# CNS depressants may produce additive CNS depressant effects') - a caution, not a contraindication
ADDITIVE_HEDGE = re.compile(r"may (produce|potentiate|enhance|result in) (an )?additive|additive (cns )?depressant "
                            r"effects? (may|can)", re.I)
# a contraindication LIST item: '• Concomitant use of strong CYP3A inhibitors, such as ...' (the list's lead-in
# 'X is contraindicated in patients with:' may be far above)
CONTRA_ITEM = re.compile(r"\b(concomitant|concurrent)\s+(use|administration|therapy|treatment)\b|\bco-?administration "
                         r"(of|with)\b|\b(taking|receiving)\b", re.I)


def _contra_names_use(e: dict) -> bool:
    """True when a contraindication-section sentence naming the other drug is about USING it with this one."""
    about = _about(e)
    if SENSITIVITY.search(about) and not TAKING_TOGETHER.search(about):
        return False  # 'Known hypersensitivity to meloxicam ... reactions after taking aspirin or other NSAIDs'
    terms = e.get("terms") or [e["mentions"]]
    # the other drug's CLASS with its own contraindication: 'Benzodiazepines are contraindicated in patients with
    # acute narrow-angle glaucoma', 'Topical corticosteroids are contraindicated in ... hypersensitivity'
    return not any(re.search(r"(?<!\w)(topical |systemic |other |all )?" + re.escape(t) + r"\s+(are|is)\s+"
                             r"contraindicated\s+(in|for)\b", about, re.I) for t in terms)


def _context(e: dict) -> str | None:
    """'no_interaction' / 'co_therapy' when the sentences naming the other drug say so."""
    about = _about(e)
    if XREF.search(about) or COMBINATION_USE.search(about):
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
        if e["section"] == "병용금기/금기" and not _contra_names_use(e):
            continue  # an allergy / the other class's own contraindication, not taking the two together
        # 'combined P-gp and moderate CYP3A inhibitors (e.g., erythromycin)': only for a drug that inhibits both
        # (strong CYP3A inhibitors - ketoconazole, ritonavir, posaconazole - inhibit P-gp as well)
        if (e.get("via_class") and re.search(r"combined p-?gp and moderate", about, re.I)
                and _bare(e["mentions"]) not in PGP_INHIBITORS):
            continue
        # a class the label's own drug belongs to: only with 'other' ('MAOIs, including other MAO-B inhibitors');
        # QT labels name the class of the drug itself without 'other' - their own check is below
        if (e.get("own_class") and "qt" not in e["via_class"].lower()
                and not re.search(r"\b(other|any)\b", about, re.I)):
            continue
        # 'QT' phrases name a CLASS only as 'drugs ... that prolong the QT interval' / 'QT-prolonging drugs':
        # 'Pimozide and thioridazine prolong the QT interval. Fluoxetine can also prolong ...' is about those drugs
        if e.get("via_class") and "qt" in e["via_class"].lower() and not QT_CLASS_USE.search(about):
            continue
        if e["section"] == "상호작용" and not EFFECT_WORDS.search(_about(e, lead=2)):
            continue  # a table heading / bare list ("Oral Antidiabetic Agents Metformin") with no effect stated
        out.append(e)
    return out
