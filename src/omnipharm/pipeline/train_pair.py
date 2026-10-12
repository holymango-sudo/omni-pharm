"""Train the drug-PAIR model on TWOSIDES signals (structure only, no patient).

Splits
  warm pairs        80 / 10 / 10 random (both drugs seen in training)
  unseen drugs      every pair containing a held-out "cold" drug (labels.cold_drugs) - these
                    molecules are never seen in training, which measures the real use case of a
                    new / rare compound.
The same loop also trains a Morgan-fingerprint baseline so the report can state how much the graph
network adds over classic cheminformatics.
"""

from __future__ import annotations

import json
import logging
import time

import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F

from omnipharm.models.gnn import FingerprintEncoder, OmniPharmModel
from omnipharm.pipeline.common import ART, PROC
from omnipharm.pipeline.labels import cold_drugs
from omnipharm.pipeline.trainutil import all_graphs, evaluate, f1_thresholds, morgan_matrix, summary

log = logging.getLogger("pipeline.train_pair")
OUT = ART / "v2"


def _y(labels: pd.Series, C: int) -> np.ndarray:
    y = np.zeros((len(labels), C), dtype=np.float32)
    for r, labs in enumerate(labels):
        if len(labs):
            y[r, list(labs)] = 1
    return y


def _fit(kind: str, enc_input, C: int, tr, va, vc, dev, epochs: int, log_prefix: str):
    """Model selection on the mean AUPRC of warm validation pairs and cold-validation pairs (pairs with a
    held-out validation drug), so the chosen epoch generalizes to new molecules instead of memorizing."""
    torch.manual_seed(0)
    np.random.seed(0)
    enc = FingerprintEncoder() if kind == "fingerprint" else None
    model = OmniPharmModel(num_classes=C, encoder=enc).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs, eta_min=5e-5)
    (ti, tj, ty), (vi, vj, vy), (ci, cj, cy) = tr, va, vc
    ti, tj, ty = (torch.as_tensor(a, device=dev) for a in (ti, tj, ty))
    bs = 2048

    @torch.no_grad()
    def predict(i, j):
        model.eval()
        emb = model.encoder.encode(enc_input)
        i, j = torch.as_tensor(i, device=dev), torch.as_tensor(j, device=dev)
        return torch.cat([torch.sigmoid(model(emb, i[s:s + 65536], j[s:s + 65536]))
                          for s in range(0, len(i), 65536)]).float().cpu().numpy()

    best, best_state, bad = -1.0, None, 0
    for ep in range(1, epochs + 1):
        model.train()
        t0, tot = time.time(), 0.0
        perm = torch.randperm(len(ti), device=dev)
        for s in range(0, len(perm), bs):
            b = perm[s:s + bs]
            emb = model.encoder.encode(enc_input)
            loss = F.binary_cross_entropy_with_logits(model(emb, ti[b], tj[b]), ty[b])
            opt.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            opt.step()
            tot += loss.item() * len(b)
        sched.step()
        m = evaluate(vy, predict(vi, vj), [str(c) for c in range(C)])
        mc = evaluate(cy, predict(ci, cj), [str(c) for c in range(C)])
        score = (m["macro_auprc"] + mc["macro_auprc"]) / 2
        log.info(f"{log_prefix} ep {ep:02d} loss {tot / len(ti):.4f} val {summary(m)} | cold-val AUROC "
                 f"{mc['macro_auroc']:.3f} AUPRC {mc['macro_auprc']:.3f} ({time.time() - t0:.1f}s)")
        if score > best + 1e-4:
            best, bad = score, 0
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
            if bad >= 8:
                break
    model.load_state_dict(best_state)
    return model, predict


def pair_splits(master: pd.DataFrame, ds: pd.DataFrame, C: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """(drug i, drug j, labels, split -> pair rows), deterministic (seed 0). Shared with stats.py."""
    idx = {str(r): k for k, r in enumerate(master["rxcui"].astype(str))}
    i = ds["drug_1"].astype(str).map(idx).to_numpy()
    j = ds["drug_2"].astype(str).map(idx).to_numpy()
    y = _y(ds["labels"], C)

    cold = sorted(cold_drugs(master))
    cold_val, cold_test = set(cold[0::2]), set(cold[1::2])  # same held-out set as the patient model
    is_cv = master["name"].isin(cold_val).to_numpy()
    is_ct = master["name"].isin(cold_test).to_numpy()
    nct = is_ct[i].astype(int) + is_ct[j].astype(int)
    ncv = is_cv[i] | is_cv[j]
    warm = np.flatnonzero((nct == 0) & ~ncv)
    rng = np.random.default_rng(0)
    rng.shuffle(warm)
    n = len(warm)
    tr, va, te = warm[: int(.85 * n)], warm[int(.85 * n): int(.92 * n)], warm[int(.92 * n):]
    vc = np.flatnonzero(ncv & (nct == 0))
    one, both = np.flatnonzero(nct == 1), np.flatnonzero(nct == 2)
    log.info(f"{len(master)} drugs (cold val {len(cold_val)}, cold test {len(cold_test)}), {C} events | "
             f"train {len(tr):,} val {len(va):,} cold-val {len(vc):,} test {len(te):,} | "
             f"unseen-drug test pairs: one {len(one):,}, both {len(both):,}")
    return i, j, y, {"train": tr, "val": va, "cold_val": vc, "test_random_split": te, "test_unseen_one_drug": one,
                     "test_unseen_both_drugs": both, "n_cold": len(cold)}


def train(epochs: int | None = None) -> dict:
    epochs = epochs or 80
    OUT.mkdir(parents=True, exist_ok=True)
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    master = pd.read_parquet(PROC / "drug_master.parquet").reset_index(drop=True)
    cm = pd.read_parquet(PROC / "condition_mapping.parquet")
    ds = pd.read_parquet(PROC / "twosides_pairs.parquet")
    C, names = len(cm), cm["condition"].tolist()
    i, j, y, S = pair_splits(master, ds, C)
    tr, va, vc, te = S["train"], S["val"], S["cold_val"], S["test_random_split"]
    one, both = S["test_unseen_one_drug"], S["test_unseen_both_drugs"]
    cold_n = S["n_cold"]

    sel = lambda s: (i[s], j[s], y[s])  # noqa: E731
    G = all_graphs(master, dev)
    FP = torch.as_tensor(morgan_matrix(master), device=dev)
    tests = {"test_random_split": te, "test_unseen_one_drug": one, "test_unseen_both_drugs": both}
    results: dict = {}
    preds: dict = {}
    for kind, inp in (("gnn", G), ("fingerprint", FP)):
        model, predict = _fit(kind, inp, C, sel(tr), sel(va), sel(vc), dev, epochs, f"[{kind}]")
        preds[kind] = {k: predict(i[s], j[s]) for k, s in {**tests, "val": va, "cold_val": vc}.items()}
        results[kind] = {k: evaluate(y[s], preds[kind][k], names) for k, s in tests.items()}
        for k, v in results[kind].items():
            log.info(f"[{kind}] {k}: {summary(v)}")
        torch.save(model.state_dict(), OUT / f"pair_{kind}.pt")
    # ensemble = mean of the two encoders' probabilities (deployed model)
    ens = {k: (preds["gnn"][k] + preds["fingerprint"][k]) / 2 for k in preds["gnn"]}
    results["ensemble"] = {k: evaluate(y[s], ens[k], names) for k, s in tests.items()}
    for k, v in results["ensemble"].items():
        log.info(f"[ensemble] {k}: {summary(v)}")
    thresholds = f1_thresholds(y[va], ens["val"])
    # Deployment routing: pairs of drugs seen in training -> ensemble; a pair with an unseen molecule (custom
    # SMILES, held-out or FAERS-extension drug) -> GNN alone, which generalizes best to new structures. Its
    # thresholds are tuned on cold-validation pairs, i.e. on the same situation.
    thresholds_unseen = f1_thresholds(y[vc], preds["gnn"]["cold_val"])
    seen = sorted(set(master["name"].iloc[np.unique(np.concatenate([i[tr], j[tr]]))]))
    metrics = {
        "trained_at": time.strftime("%Y-%m-%d %H:%M"), "n_drugs": len(master), "n_cold_drugs": cold_n,
        "n_classes": C, "classes": names, "n_pairs": len(ds),
        "split": {"train": len(tr), "val": len(va), "cold_val": len(vc), "test": len(te), "unseen_one": len(one),
                  "unseen_both": len(both)},
        "thresholds": thresholds, "thresholds_unseen": thresholds_unseen, "seen_drugs": seen,
        "train_prevalence": y[tr].mean(0).tolist(),
        "config": {"hidden_dim": 256, "encoder": "GINE x4 + Morgan-FP MLP ensemble"},
        **results["ensemble"], "gnn_only": results["gnn"], "fingerprint_only": results["fingerprint"],
    }
    (OUT / "pair_metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    return metrics
