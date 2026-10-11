"""Time course of each drug from its FDA label: time to peak concentration (Tmax) and elimination half-life (t1/2).

The body view labels each step with the time after the dose ("복용 후 1시간 30분: 최고 혈중 농도"). The numbers come
from the CLINICAL PHARMACOLOGY / PHARMACOKINETICS sections of all single-ingredient openFDA labels; the median over
labels is kept, with the number of labels it came from.

Output: processed/pk.json  {ingredient key: {"tmax_h", "t_half_h", "n_tmax", "n_thalf"}}
"""

from __future__ import annotations

import json
import logging
import re
import statistics
import zipfile

import pandas as pd

from omnipharm.knowledge import BIOLOGIC_LABEL_NAMES, BIOLOGICS, lab_name, normalize_ingredient
from omnipharm.pipeline.common import PROC, RAW

log = logging.getLogger("pipeline.pk")
OUT = PROC / "pk.json"
FIELDS = ("pharmacokinetics", "clinical_pharmacology")

_NUM = r"(\d+(?:\.\d+)?)"
_PM = r"(?:\s*(?:±|\+/-)\s*\d+(?:\.\d+)?)?"
_RANGE = _NUM + _PM + r"(?:\s*(?:to|-|–|and)\s*" + _NUM + _PM + r")?"
_UNIT = r"\s*(hours?|hrs?|h\b|minutes?|mins?\b|days?|weeks?)"
_GAP = r"(?:[^.;]|(?<=\d)\.(?=\d)|(?<=\bvs)\.|(?<=approx)\.)"  # within one sentence; decimals don't end it
HALF = re.compile(r"(?:half-?li(?:fe|ves)|t\s?½|t\s?1/2)\b" + _GAP + r"{0,90}?" + _RANGE + _UNIT, re.I)
TMAX = re.compile(
    r"(?:\bT\s?max\b|time to (?:reach )?(?:the )?(?:peak|maximum)|peak (?:plasma |serum |blood )?(?:drug )?"
    r"(?:concentrations?|levels?)|maximum (?:plasma |serum |blood )?(?:concentrations?|levels?))" + _GAP + r"{0,100}?"
    r"(?:within|at|in|after|of|by|approximately|about|is|was|were|occurred|occur|occurs|reached|achieved|attained|between)\s+"
    r"(?:approximately |about |~)?" + _RANGE + _UNIT, re.I)
NOT_ELIMINATION = re.compile(r"absorption|distribution|alpha|initial|α", re.I)
TO_H = {"m": 1 / 60, "h": 1.0, "d": 24.0, "w": 168.0}


def _hours(m: re.Match) -> float | None:
    a, b, unit = float(m.group(1)), m.group(2), m.group(3).lower()
    v = (a + float(b)) / 2 if b else a
    return v * TO_H.get(unit[0], 1.0)


def extract(text: str) -> tuple[list[float], list[float]]:
    """-> (tmax hours, elimination half-life hours) mentioned in one label's PK text (first few of each)."""
    tmax, half = [], []
    for m in TMAX.finditer(text):
        h = _hours(m)
        if h and 0.08 <= h <= 96:
            tmax.append(h)
        if len(tmax) >= 2:
            break
    for m in HALF.finditer(text):
        if NOT_ELIMINATION.search(text[max(0, m.start() - 40): m.start() + 12]):
            continue
        h = _hours(m)
        if h and 0.05 <= h <= 5000:
            half.append(h)
        if len(half) >= 2:
            break
    return tmax, half


def build() -> None:
    master = pd.read_parquet(PROC / "drug_master.parquet")
    ext = PROC / "ext_ingredients.parquet"
    ext_names = {k.removeprefix("ing:") for k in pd.read_parquet(ext)["key"]} if ext.exists() else set()
    known = frozenset(master["name"]) | ext_names | set(BIOLOGICS) | set(BIOLOGIC_LABEL_NAMES)

    def key_of(s: str) -> str | None:
        k = normalize_ingredient(s, known)
        return BIOLOGIC_LABEL_NAMES.get(k, k) if k else lab_name(s)

    tm: dict[str, list[float]] = {}
    hl: dict[str, list[float]] = {}
    n_lab = 0
    for z in sorted((RAW / "openfda_label").glob("*.zip")):
        with zipfile.ZipFile(z) as zf, zf.open(zf.namelist()[0]) as f:
            data = json.load(f)
        for res in data.get("results", []):
            text = " ".join(" ".join(res.get(f_) or []) for f_ in FIELDS)
            if len(text) < 80:
                continue
            o = res.get("openfda", {}) or {}
            subs = {s.lower() for s in o.get("substance_name", []) or []}
            gens = [p.strip() for g in o.get("generic_name", []) or [] for p in re.split(r"\s+and\s+|,|/", g.lower()) if p.strip()]
            keys = {k for k in (key_of(s) for s in list(subs) + gens) if k}
            if len(keys) != 1 or len(subs) > 1:  # combination products describe several drugs in one text
                continue
            key = keys.pop()
            t, h = extract(text)
            if t or h:
                n_lab += 1
                tm.setdefault(key, []).extend(t)
                hl.setdefault(key, []).extend(h)
        del data
        log.info(f"{z.name}: {n_lab:,} labels with PK numbers, {len(set(tm) | set(hl)):,} ingredients")
    out = {}
    for k in sorted(set(tm) | set(hl)):
        t, h = tm.get(k, []), hl.get(k, [])
        out[k] = {"tmax_h": round(statistics.median(t), 2) if t else None, "n_tmax": len(t),
                  "t_half_h": round(statistics.median(h), 2) if h else None, "n_thalf": len(h)}
    OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    log.info(f"pk.json: {len(out):,} ingredients (Tmax {sum(1 for v in out.values() if v['tmax_h']):,}, "
             f"t1/2 {sum(1 for v in out.values() if v['t_half_h']):,})")
