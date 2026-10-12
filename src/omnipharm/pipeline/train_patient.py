"""Train the REGIMEN + PATIENT model on FAERS reports.

Input  : the set of drugs in a report (2..10, order free, molecule graphs) + patient vector
         (age, sex, weight, comorbidities from the indication field, number of drugs)
Target : which of the modeled adverse events were reported.

Splits (no leakage of time or molecules)
  test_time     reports from the last 4 published quarters (future data)
  test_unseen   reports containing a held-out "cold" drug (molecule never seen in training)
  val           3% random sample of the remaining (earlier) reports
Also reports a patient-sensitivity analysis: how predicted organ risk moves when one patient
attribute is changed with drugs fixed - this is what makes the prediction "personalized".
"""

from __future__ import annotations

import json
import logging
import time

import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F

from omnipharm.knowledge import COMORBIDITIES
from omnipharm.models.gnn import RegimenPatientModel
from omnipharm.models.patient import FEATURES, MAX_DRUGS, PATIENT_DIM, patient_matrix
from omnipharm.pipeline.common import ART, PROC
from omnipharm.pipeline.labels import cold_drugs
from omnipharm.pipeline.trainutil import all_graphs, evaluate, f1_thresholds, summary

log = logging.getLogger("pipeline.train_patient")
OUT = ART / "v2"
BS = 8192


class _Data:
    """All report arrays resident on the GPU; labels are expanded from bit masks per batch."""

    def __init__(self, A: dict, rows: np.ndarray, X: np.ndarray, dev: torch.device):
        self.drugs = torch.as_tensor(A["drugs"][rows], device=dev)
        self.x = torch.as_tensor(X[rows], device=dev)
        self.lab = torch.as_tensor(A["labels"][rows], device=dev)
        self.n = len(rows)

    def y(self, b: torch.Tensor, C: int) -> torch.Tensor:
        bits = torch.arange(C, device=self.lab.device)
        return ((self.lab[b, None] >> bits) & 1).float()


def _predict(model, G, d: _Data, C: int, x_override: torch.Tensor | None = None) -> np.ndarray:
    model.eval()
    out = []
    with torch.no_grad():
        emb = model.encoder.encode(G)
        for s in range(0, d.n, 32768):
            sl = slice(s, s + 32768)
            x = d.x[sl] if x_override is None else x_override[sl]
            out.append(torch.sigmoid(model(emb, d.drugs[sl].long(), x)).float().cpu().numpy())
    return np.vstack(out)


def _labels_np(d: _Data, C: int) -> np.ndarray:
    return d.y(torch.arange(d.n, device=d.lab.device), C).cpu().numpy()


def _sensitivity(model, G, d: _Data, C: int, organs: list[str], cap: int = 200_000) -> dict:
    """Mean predicted organ risk ratio when one patient attribute is switched on (drugs fixed)."""
    sub = _Data.__new__(_Data)
    sub.drugs, sub.x, sub.lab, sub.n = d.drugs[:cap], d.x[:cap].clone(), d.lab[:cap], min(cap, d.n)
    organ_arr = np.array(organs)

    def organ_mean(p):
        return {o: float(p[:, organ_arr == o].sum(1).mean()) for o in sorted(set(organs))}

    base_x = sub.x.clone()
    for k in COMORBIDITIES:
        base_x[:, FEATURES.index(k)] = 0
    base = organ_mean(_predict(model, G, sub, C, base_x))
    out = {}
    for k in COMORBIDITIES:
        x = base_x.clone()
        x[:, FEATURES.index(k)] = 1
        on = organ_mean(_predict(model, G, sub, C, x))
        out[k] = {o: round(on[o] / max(base[o], 1e-9), 3) for o in base}
    for label, age in (("age_30", 30.0), ("age_80", 80.0)):
        x = base_x.clone()
        x[:, FEATURES.index("age")] = age / 100.0
        x[:, FEATURES.index("age_missing")] = 0
        out[label] = organ_mean(_predict(model, G, sub, C, x))
    out["age_80_vs_30"] = {o: round(out["age_80"][o] / max(out["age_30"][o], 1e-9), 3) for o in base}
    del out["age_30"], out["age_80"]
    return out


def report_features(A: dict, no_patient: bool = False) -> np.ndarray:
    """Patient vector of every report (all zeros for the no-patient ablation)."""
    nd = (A["drugs"] >= 0).sum(1)
    X = patient_matrix(A["age"], A["sex"], A["wt"], A["flags"], nd + A["n_unmapped"].astype(np.int64))
    if no_patient:
        X[:] = 0
    return X


def report_splits(A: dict, master: pd.DataFrame) -> tuple[dict[str, np.ndarray], int, np.ndarray]:
    """Report rows of every split (deterministic, seed 0) -> (rows, first test quarter, Canada mask).
    Shared with the statistics stage (stats.py), which must evaluate on exactly these reports."""
    cold = np.flatnonzero(master["name"].isin(cold_drugs(master)).to_numpy())
    has_cold = np.isin(A["drugs"], cold).any(1)
    q = A["quarter"].astype(int)
    src = A.get("src", np.zeros(len(q), dtype=np.int8))  # 0 = FAERS, 1 = Canada Vigilance
    fa, ca = src == 0, src == 1
    q_cut = np.sort(np.unique(q[fa]))[-4]  # test quarters = last 4 FAERS quarters (comparable across versions)
    ca_cut = np.sort(np.unique(q[ca]))[-4] if ca.any() else 10**6
    ca_test = ca & ~has_cold & (q >= ca_cut)  # recent Canadian reports = external (other country) validation
    rng = np.random.default_rng(0)
    early = np.flatnonzero(fa & ~has_cold & (q < q_cut))
    rng.shuffle(early)
    n_val = int(len(early) * 0.03)
    train = np.concatenate([early[n_val:], np.flatnonzero(ca & ~has_cold & (q < min(q_cut, ca_cut)))])
    rows = {"train": np.sort(train), "val": np.sort(early[:n_val]),
            "test_time": np.flatnonzero(fa & ~has_cold & (q >= q_cut)), "test_unseen": np.flatnonzero(fa & has_cold)}
    if ca_test.any():
        rows["test_canada"] = np.flatnonzero(ca_test)
    log.info(f"{len(q):,} reports | " + " | ".join(f"{k} {len(v):,}" for k, v in rows.items()))
    return rows, int(q_cut), ca


def train(epochs: int | None = None, no_patient: bool = False) -> dict:
    epochs = epochs or 12
    OUT.mkdir(parents=True, exist_ok=True)
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    torch.manual_seed(0)
    master = pd.read_parquet(PROC / "drug_master.parquet").reset_index(drop=True)
    cm = pd.read_parquet(PROC / "condition_mapping.parquet")
    C, names = len(cm), cm["condition"].tolist()
    A = dict(np.load(PROC / "faers_reports.npz"))
    X = report_features(A, no_patient)
    rows, q_cut, ca = report_splits(A, master)
    q = A["quarter"]
    D = {k: _Data(A, v, X, dev) for k, v in rows.items()}
    prevalence = _labels_np(D["val"], C).mean(0)

    G = all_graphs(master, dev)
    model = RegimenPatientModel(num_classes=C, patient_dim=PATIENT_DIM).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=7e-4, weight_decay=1e-4)
    steps = epochs * ((D["train"].n + BS - 1) // BS)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=7e-4, total_steps=steps, pct_start=0.05)
    yv = _labels_np(D["val"], C)
    best, best_state, bad = -1.0, None, 0
    tr = D["train"]
    for ep in range(1, epochs + 1):
        model.train()
        t0, tot = time.time(), 0.0
        perm = torch.randperm(tr.n, device=dev)
        for s in range(0, tr.n, BS):
            b = perm[s:s + BS]
            with torch.autocast("cuda", dtype=torch.bfloat16, enabled=dev.type == "cuda"):
                emb = model.encoder.encode(G)
                logits = model(emb, tr.drugs[b].long(), tr.x[b])
            loss = F.binary_cross_entropy_with_logits(logits.float(), tr.y(b, C))
            opt.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            opt.step()
            sched.step()
            tot += loss.item() * len(b)
        m = evaluate(yv, _predict(model, G, D["val"], C), names)
        log.info(f"ep {ep:02d} loss {tot / tr.n:.4f} val {summary(m)} ({time.time() - t0:.0f}s)")
        if m["macro_auprc"] > best + 1e-4:
            best, bad = m["macro_auprc"], 0
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
            if bad >= 3:
                break
    model.load_state_dict(best_state)

    thr = f1_thresholds(yv, _predict(model, G, D["val"], C))
    res = {}
    for k in [k for k in ("test_time", "test_unseen", "test_canada") if k in D]:
        d = D[k]
        p, y = _predict(model, G, d, C), _labels_np(d, C)
        res[k] = evaluate(y, p, names)
        multi = (d.drugs >= 0).sum(1).cpu().numpy() >= 2
        res[k + "_multi_drug"] = evaluate(y[multi], p[multi], names)
        log.info(f"{k}: {summary(res[k])}")
        log.info(f"{k} (2+ drugs): {summary(res[k + '_multi_drug'])}")
    tag = "patient_model_nopatient" if no_patient else "patient_model"
    metrics = {
        "trained_at": time.strftime("%Y-%m-%d %H:%M"), "n_reports": len(q), "n_canada": int(ca.sum()),
        "n_drugs": len(master),
        "n_classes": C, "classes": names, "features": FEATURES, "max_drugs": MAX_DRUGS,
        "split": {k: len(v) for k, v in rows.items()}, "test_quarters_from": int(q_cut),
        "thresholds": thr, "prevalence": prevalence.tolist(), **res,
    }
    if not no_patient:
        metrics["patient_sensitivity"] = _sensitivity(model, G, D["test_time"], C, cm["organ"].tolist())
        log.info(f"sensitivity: {json.dumps(metrics['patient_sensitivity'])}")
    torch.save(model.state_dict(), OUT / f"{tag}.pt")
    (OUT / f"{tag}_metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    return metrics
