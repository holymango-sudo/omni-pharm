"""Helpers shared by the two training stages."""

from __future__ import annotations

import numpy as np
import pandas as pd
import torch
from sklearn.metrics import average_precision_score, roc_auc_score
from torch_geometric.data import Batch

from omnipharm.chem.featurize import smiles_to_graph


def all_graphs(master: pd.DataFrame, device: torch.device) -> Batch:
    """One PyG batch holding every drug in drug_master order (row i == drug i)."""
    return Batch.from_data_list([smiles_to_graph(s) for s in master["std_smiles"]]).to(device)


def morgan_matrix(master: pd.DataFrame, n_bits: int = 2048) -> np.ndarray:
    from rdkit import Chem
    from rdkit.Chem import rdFingerprintGenerator

    gen = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=n_bits)
    out = np.zeros((len(master), n_bits), dtype=np.float32)
    for i, s in enumerate(master["std_smiles"]):
        out[i] = gen.GetFingerprintAsNumPy(Chem.MolFromSmiles(s))
    return out


def evaluate(y: np.ndarray, p: np.ndarray, names: list[str]) -> dict:
    """Per-class and macro AUROC/AUPRC (classes with no positives or no negatives are skipped),
    plus expected calibration error over all (row, class) cells."""
    per = {}
    for c, name in enumerate(names):
        pos = y[:, c].sum()
        if 0 < pos < len(y):
            per[name] = {"auroc": float(roc_auc_score(y[:, c], p[:, c])),
                         "auprc": float(average_precision_score(y[:, c], p[:, c])),
                         "prevalence": float(y[:, c].mean()), "n_pos": int(pos)}
    yf, pf = y.ravel(), p.ravel()
    bins = np.clip((pf * 15).astype(int), 0, 14)
    ece = sum(abs(yf[bins == b].mean() - pf[bins == b].mean()) * (bins == b).mean()
              for b in range(15) if (bins == b).any())
    return {
        "n": len(y),
        "macro_auroc": float(np.mean([v["auroc"] for v in per.values()])) if per else float("nan"),
        "macro_auprc": float(np.mean([v["auprc"] for v in per.values()])) if per else float("nan"),
        "mean_prevalence": float(np.mean([v["prevalence"] for v in per.values()])) if per else float("nan"),
        "micro_auroc": float(roc_auc_score(yf, pf)) if 0 < yf.sum() < len(yf) else float("nan"),
        "ece": float(ece),
        "per_class": per,
    }


def f1_thresholds(y: np.ndarray, p: np.ndarray) -> list[float]:
    """Per-class decision threshold maximizing F1 on validation data."""
    out = []
    grid = np.concatenate([np.linspace(0.01, 0.1, 10), np.linspace(0.12, 0.9, 40)])
    for c in range(y.shape[1]):
        yc = y[:, c].astype(bool)
        if not yc.any():
            out.append(0.5)
            continue
        best_t, best_f = 0.5, -1.0
        for t in grid:
            pr = p[:, c] >= t
            tp = (pr & yc).sum()
            f = 2 * tp / max(pr.sum() + yc.sum(), 1)
            if f > best_f:
                best_f, best_t = f, float(t)
        out.append(best_t)
    return out


def summary(m: dict) -> str:
    return f"macro-AUROC {m['macro_auroc']:.3f} | macro-AUPRC {m['macro_auprc']:.3f} " \
           f"(chance {m['mean_prevalence']:.3f}) | ECE {m['ece']:.3f} | n={m['n']:,}"
