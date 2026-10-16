"""Is the patient model's probability a probability, and where does the AI add anything? (v2.18)

On the future-period test reports of train_patient (same deterministic split as stats.py):
  - calibration: Brier score (and its skill against predicting the training prevalence), a reliability table with
    probability bins suited to rare events (0.1% ... 30%), the expected calibration error over those bins, and per
    adverse event the mean predicted vs observed rate (calibration-in-the-large)
  - subgroups: macro AUROC of the full model, the same model without patient information and the drug-frequency
    baseline (no structure, no patient) for 2-4 vs 5+ medicines, under 65 vs 65+, and the unseen-drug test - where
    the patient information and the structure model help, and where they do not
Writes artifacts/v2/calibration.json. Stage "calibration" (GPU, a few minutes).

The model predicts which events are REPORTED with a medicine list (FAERS), not the risk of having them: even a
perfectly calibrated value is a report rate. The results page says so.
"""

from __future__ import annotations

import itertools
import json
import logging
import time

import numpy as np
import pandas as pd
import torch

from omnipharm.models.gnn import RegimenPatientModel
from omnipharm.models.patient import PATIENT_DIM
from omnipharm.pipeline.common import ART, PROC
from omnipharm.pipeline.stats import _bits, drug_rates, predict_frequency, predict_regimen
from omnipharm.pipeline.train_patient import report_features, report_splits
from omnipharm.pipeline.trainutil import all_graphs, evaluate

log = logging.getLogger("pipeline.calibration")
OUT = ART / "v2"
EDGES = [0, 0.001, 0.003, 0.01, 0.03, 0.1, 0.3, 1.0001]  # rare events: most cells are below 1%
MAX_REPORTS = 200_000


def reliability(y: np.ndarray, p: np.ndarray) -> dict:
    yf, pf = y.ravel().astype(np.float64), p.ravel().astype(np.float64)
    rows, ece = [], 0.0
    for lo, hi in itertools.pairwise(EDGES):
        m = (pf >= lo) & (pf < hi)
        if not m.any():
            continue
        mp, my = float(pf[m].mean()), float(yf[m].mean())
        ece += m.mean() * abs(mp - my)
        rows.append({"bin": f"{lo:g}-{min(hi, 1):g}", "cells": int(m.sum()), "mean_predicted": round(mp, 5),
                     "observed": round(my, 5)})
    return {"ece": round(float(ece), 5), "bins": rows}


def build() -> dict:
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    t0 = time.time()
    master = pd.read_parquet(PROC / "drug_master.parquet").reset_index(drop=True)
    cm = pd.read_parquet(PROC / "condition_mapping.parquet")
    C, names, ko = len(cm), cm["condition"].tolist(), cm["condition_ko"].tolist()
    A = dict(np.load(PROC / "faers_reports.npz"))
    X = report_features(A)
    rows, _, _ = report_splits(A, master)
    G = all_graphs(master, dev)
    full = RegimenPatientModel(num_classes=C, patient_dim=PATIENT_DIM).to(dev)
    full.load_state_dict(torch.load(OUT / "patient_model.pt", map_location=dev, weights_only=True))
    nop = RegimenPatientModel(num_classes=C, patient_dim=PATIENT_DIM).to(dev)
    nop.load_state_dict(torch.load(OUT / "patient_model_nopatient.pt", map_location=dev, weights_only=True))
    rates = drug_rates(A, rows["train"], C, len(master))
    prior = _bits(A["labels"][rows["train"][:500_000]], C).mean(0)

    rng = np.random.default_rng(0)
    r = rows["test_time"]
    if len(r) > MAX_REPORTS:
        r = np.sort(rng.choice(r, MAX_REPORTS, replace=False))
    y = _bits(A["labels"][r], C)
    p_full = predict_regimen(full, G, A, X, r, dev)
    p_nop = predict_regimen(nop, G, A, X, r, dev, zero_x=True)
    p_freq = predict_frequency(rates, A, r)

    brier = float(((p_full - y) ** 2).mean())
    brier_prior = float(((prior[None, :] - y) ** 2).mean())
    res: dict = {"computed_at": time.strftime("%Y-%m-%d %H:%M"), "test": "test_time (future quarters)", "n": len(r),
                 "calibration": {"brier": round(brier, 6), "brier_training_prevalence": round(brier_prior, 6),
                                 "brier_skill": round(1 - brier / brier_prior, 4),
                                 **reliability(y, p_full)}}
    per = [{"event": names[c], "ko": ko[c], "mean_predicted": round(float(p_full[:, c].mean()), 5),
            "observed": round(float(y[:, c].mean()), 5)} for c in range(C)]
    res["calibration"]["per_event"] = sorted(per, key=lambda d: -d["observed"])

    nd = (A["drugs"][r] >= 0).sum(1)
    age = A["age"][r]
    groups = {"2~4제": (nd >= 2) & (nd <= 4), "5제 이상": nd >= 5, "65세 미만": age < 65, "65세 이상": age >= 65,
              "1제": nd == 1}
    sub = {}
    for g, m in groups.items():
        if m.sum() < 2000:
            continue
        sub[g] = {"n": int(m.sum())} | {
            k: round(evaluate(y[m], p[m], names)["macro_auroc"], 4)
            for k, p in (("full", p_full), ("no_patient", p_nop), ("drug_frequency", p_freq))}
    ru = rows.get("test_unseen")
    if ru is not None and len(ru):
        yu = _bits(A["labels"][ru], C)
        sub["처음 보는 약(구조만 아는 약)"] = {"n": len(ru)} | {
            k: round(evaluate(yu, p, names)["macro_auroc"], 4)
            for k, p in (("full", predict_regimen(full, G, A, X, ru, dev)),
                         ("no_patient", predict_regimen(nop, G, A, X, ru, dev, zero_x=True)),
                         ("drug_frequency", predict_frequency(rates, A, ru)))}
    for d in sub.values():
        d["gain_patient"] = round(d["full"] - d["no_patient"], 4)
        d["gain_over_frequency"] = round(d["full"] - d["drug_frequency"], 4)
    res["subgroups"] = sub
    res["seconds"] = round(time.time() - t0)
    (OUT / "calibration.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    log.info(f"calibration: Brier {brier:.5f} (skill {res['calibration']['brier_skill']}), ECE "
             f"{res['calibration']['ece']} | subgroups {json.dumps(sub, ensure_ascii=False)}")
    return res
