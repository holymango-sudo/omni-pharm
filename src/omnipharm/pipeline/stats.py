"""Statistics for the report / thesis: confidence intervals, significance tests and simple baselines.

Every number is measured on exactly the test reports of train_patient / train_pair (same deterministic splits).
  - 95% CI of every model's macro AUROC: paired bootstrap over reports (Poisson weights, 1,000 resamples; the same
    resamples for every model, so differences are paired)
  - does patient information help? full model vs the same architecture trained without patient features:
    bootstrap CI + two-sided p of the macro-AUROC difference, and a DeLong test per adverse event (Holm-corrected)
  - baselines without molecular structure on the same splits:
      drug frequency  each drug's event rate in the training reports (smoothed), max over the regimen's drugs
      logistic        multi-hot drugs + the same patient features, one linear layer (an unseen drug adds nothing)
  - pair model: GNN vs Morgan fingerprint vs ensemble on the warm and unseen-drug tests (CIs + paired differences)
  - Wilson 95% CIs of the clinical audit sets and the MFDS DUR external validation
Writes artifacts/v2/stats_metrics.json. Stage "stats" (GPU, about 20-30 minutes).
"""

from __future__ import annotations

import json
import logging
import math
import time

import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F
from scipy.stats import norm, rankdata

from omnipharm.config import settings
from omnipharm.models.gnn import FingerprintEncoder, OmniPharmModel, RegimenPatientModel
from omnipharm.models.patient import PATIENT_DIM
from omnipharm.pipeline.common import ART, PROC
from omnipharm.pipeline.labels import cold_drugs
from omnipharm.pipeline.train_pair import pair_splits
from omnipharm.pipeline.train_patient import report_features, report_splits
from omnipharm.pipeline.trainutil import all_graphs, evaluate, morgan_matrix

log = logging.getLogger("pipeline.stats")
OUT = ART / "v2"
B = 1000  # bootstrap resamples
TESTS = ("test_time", "test_unseen", "test_canada")


# --------------------------------------------------------------------------- bootstrap / DeLong
def _bits(lab: np.ndarray, C: int) -> np.ndarray:
    return ((lab[:, None] >> np.arange(C)) & 1).astype(np.uint8)


def bootstrap_auroc(y: np.ndarray, p: np.ndarray, dev: torch.device, n_boot: int = B, seed: int = 0,
                    clusters: np.ndarray | None = None) -> np.ndarray:
    """AUROC of every class in n_boot bootstrap resamples -> (n_boot, C) array (nan: class not evaluable).
    clusters = None: Poisson weights per case. clusters = cluster id (0..K-1) of every case: a cluster bootstrap -
    K clusters drawn with replacement, every case weighted by how often its cluster was drawn (the unseen-drug tests:
    their cases share a few dozen held-out drugs, so the drug, not the case, is the independent unit).
    Resample b uses generator seed (seed + b) for every class and every model, so models are compared on the
    same resamples. Ties are handled exactly (score groups)."""
    n, C = y.shape
    out = np.full((n_boot, C), np.nan)
    ones = torch.ones(n, device=dev, dtype=torch.float32)
    cl = None if clusters is None else torch.as_tensor(clusters, device=dev, dtype=torch.long)
    K = 0 if clusters is None else int(clusters.max()) + 1

    def weights(b: int) -> torch.Tensor:
        gen = torch.Generator(device=dev).manual_seed(seed + b)
        if cl is None:
            return torch.poisson(ones, generator=gen).double()
        drawn = torch.randint(K, (K,), generator=gen, device=dev)
        return torch.bincount(drawn, minlength=K).double()[cl]

    for c in range(C):
        yc = torch.as_tensor(y[:, c], device=dev, dtype=torch.float64)
        if not 0 < float(yc.sum()) < n:
            continue
        _, g = torch.unique(torch.as_tensor(p[:, c], device=dev), return_inverse=True)
        for b in range(n_boot):
            out[b, c] = weighted_auroc(g, yc, weights(b))
    return out


def first_cold(drugs: np.ndarray, cold: np.ndarray) -> np.ndarray:
    """Cluster id (0..K-1) of every row = its first held-out drug (rows: drug indices, -1 = padding)."""
    first = drugs[np.arange(len(drugs)), np.isin(drugs, cold).argmax(1)]
    _, ids = np.unique(first, return_inverse=True)  # only the drugs that occur: 0..K-1
    return ids.ravel()


def weighted_auroc(g: torch.Tensor, y: torch.Tensor, w: torch.Tensor) -> float:
    """Mann-Whitney AUROC with case weights; g = rank group of each score (equal scores share a group)."""
    G = int(g.max()) + 1
    pos = torch.bincount(g, weights=w * y, minlength=G)
    neg = torch.bincount(g, weights=w * (1 - y), minlength=G)
    below = torch.cumsum(neg, 0) - neg
    den = pos.sum() * neg.sum()
    return float((pos * (below + 0.5 * neg)).sum() / den) if den > 0 else float("nan")


def ci(values: np.ndarray) -> list[float]:
    return [float(np.nanpercentile(values, 2.5)), float(np.nanpercentile(values, 97.5))]


def macro_boot(aucs: np.ndarray, classes: np.ndarray) -> np.ndarray:
    """Macro AUROC of each resample over the classes the point estimate uses."""
    return np.nanmean(aucs[:, classes], axis=1)


def paired_difference(a: np.ndarray, b: np.ndarray, point: float) -> dict:
    """Bootstrap distribution of macro(a) - macro(b) -> point, 95% CI and two-sided p (percentile method)."""
    d = a - b
    p = float(min(1.0, 2 * min((d <= 0).mean(), (d >= 0).mean())))
    # p = 0 means no resample crossed zero: report p < 1/n_boot
    return {"difference": round(point, 4), "ci95": [round(x, 4) for x in ci(d)],
            "p_value": p if p > 0 else 1 / len(d), "p_is_upper_bound": p == 0, "n_boot": len(d)}


def delong(y: np.ndarray, p1: np.ndarray, p2: np.ndarray) -> tuple[float, float, float]:
    """DeLong test of two correlated AUROCs on the same cases (Sun & Xu 2014 fast algorithm) -> (auc1, auc2, p)."""
    pos, neg = y == 1, y == 0
    m, n = int(pos.sum()), int(neg.sum())
    aucs, v10, v01 = [], [], []
    for p in (p1, p2):
        x, z = p[pos], p[neg]
        tx, tz = rankdata(x), rankdata(z)
        t = rankdata(np.concatenate([x, z]))
        aucs.append((t[:m].sum() - m * (m + 1) / 2) / (m * n))
        v10.append((t[:m] - tx) / n)
        v01.append(1 - (t[m:] - tz) / m)
    s10, s01 = np.cov(np.vstack(v10)), np.cov(np.vstack(v01))
    var = (s10[0, 0] + s10[1, 1] - 2 * s10[0, 1]) / m + (s01[0, 0] + s01[1, 1] - 2 * s01[0, 1]) / n
    zscore = (aucs[0] - aucs[1]) / math.sqrt(var) if var > 0 else 0.0
    return float(aucs[0]), float(aucs[1]), float(2 * norm.sf(abs(zscore)))


def holm(pvals: list[float]) -> list[float]:
    order = np.argsort(pvals)
    adj, run = np.empty(len(pvals)), 0.0
    for k, i in enumerate(order):
        run = max(run, min(1.0, (len(pvals) - k) * pvals[i]))
        adj[i] = run
    return adj.tolist()


def wilson(k: int, n: int) -> list[float]:
    if n == 0:
        return [float("nan"), float("nan")]
    z, ph = 1.959964, k / n
    den = 1 + z * z / n
    mid = (ph + z * z / (2 * n)) / den
    half = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / den
    return [round(mid - half, 4), round(mid + half, 4)]


# --------------------------------------------------------------------------- baselines
class LinearRegimen(torch.nn.Module):
    """Logistic regression: one weight vector per drug (summed over the regimen) + patient features."""

    def __init__(self, n_drugs: int, C: int):
        super().__init__()
        self.pad = n_drugs
        self.drug = torch.nn.EmbeddingBag(n_drugs + 1, C, mode="sum", padding_idx=n_drugs)
        torch.nn.init.zeros_(self.drug.weight)  # a drug never seen in training contributes nothing
        self.pat = torch.nn.Linear(PATIENT_DIM, C)

    def forward(self, drugs: torch.Tensor, x: torch.Tensor) -> torch.Tensor:
        return self.drug(torch.where(drugs < 0, self.pad, drugs)) + self.pat(x)


def _batches(n: int, bs: int):
    for s in range(0, n, bs):
        yield slice(s, min(n, s + bs))


def train_logistic(A, X, rows, C, n_drugs, dev, epochs: int = 15) -> LinearRegimen:
    """Trained to convergence (early stopping on validation AUPRC, patience 2) so the baseline is not a straw man."""
    torch.manual_seed(0)
    model = LinearRegimen(n_drugs, C).to(dev)
    tr, va = rows["train"], rows["val"]
    prior = _bits(A["labels"][tr[:: max(1, len(tr) // 2_000_000)]], C).mean(0).clip(1e-5, 1 - 1e-5)
    with torch.no_grad():  # start from each event's base rate instead of 50%
        model.pat.bias.copy_(torch.as_tensor(np.log(prior / (1 - prior)), device=dev))
    opt = torch.optim.Adam(model.parameters(), lr=0.03)
    yv = _bits(A["labels"][va], C)
    best, best_state, bad = -1.0, None, 0
    for ep in range(epochs):
        model.train()
        perm = np.random.default_rng(ep).permutation(tr)
        for sl in _batches(len(perm), 65536):
            r = np.sort(perm[sl])
            d = torch.as_tensor(A["drugs"][r].astype(np.int64), device=dev)
            x = torch.as_tensor(X[r], device=dev)
            yb = torch.as_tensor(_bits(A["labels"][r], C), device=dev, dtype=torch.float32)
            loss = F.binary_cross_entropy_with_logits(model(d, x), yb)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
        m = evaluate(yv, predict_logistic(model, A, X, va, dev), [str(c) for c in range(C)])
        log.info(f"[logistic] ep {ep + 1} val macro-AUROC {m['macro_auroc']:.3f} AUPRC {m['macro_auprc']:.3f}")
        if m["macro_auprc"] > best + 1e-4:
            best, bad = m["macro_auprc"], 0
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
            if bad >= 2:
                break
    model.load_state_dict(best_state)
    return model


@torch.no_grad()
def predict_logistic(model, A, X, r, dev) -> np.ndarray:
    model.eval()
    out = []
    for sl in _batches(len(r), 131072):
        rr = r[sl]
        out.append(torch.sigmoid(model(torch.as_tensor(A["drugs"][rr].astype(np.int64), device=dev),
                                       torch.as_tensor(X[rr], device=dev))).cpu().numpy())
    return np.vstack(out)


def drug_rates(A, tr, C, n_drugs, alpha: float = 10.0) -> np.ndarray:
    """Smoothed event rate of each drug in the training reports: (pos + alpha * prior) / (n + alpha)."""
    d = A["drugs"][tr].astype(np.int64)
    lab = A["labels"][tr]
    mask = d >= 0
    flat = d[mask]
    rep = np.repeat(np.arange(len(tr)), mask.sum(1))
    n_d = np.bincount(flat, minlength=n_drugs).astype(np.float64)
    prior = _bits(lab, C).mean(0)
    lab_rep = lab[rep]
    rates = np.zeros((n_drugs + 1, C), dtype=np.float32)  # last row = padding (rate 0: never the max)
    for c in range(C):
        pos = np.bincount(flat, weights=((lab_rep >> c) & 1).astype(np.float64), minlength=n_drugs)
        rates[:n_drugs, c] = (pos + alpha * prior[c]) / (n_d + alpha)
    return rates


def predict_frequency(rates: np.ndarray, A, r) -> np.ndarray:
    pad = len(rates) - 1
    out = []
    for sl in _batches(len(r), 16384):
        d = A["drugs"][r[sl]].astype(np.int64)
        out.append(rates[np.where(d < 0, pad, d)].max(1).astype(np.float32))
    return np.vstack(out)


@torch.no_grad()
def predict_regimen(model, G, A, X, r, dev, zero_x: bool = False) -> np.ndarray:
    model.eval()
    emb = model.encoder.encode(G)
    out = []
    for sl in _batches(len(r), 32768):
        rr = r[sl]
        x = torch.as_tensor(X[rr], device=dev)
        if zero_x:
            x = torch.zeros_like(x)
        out.append(torch.sigmoid(model(emb, torch.as_tensor(A["drugs"][rr].astype(np.int64), device=dev), x))
                   .float().cpu().numpy())
    return np.vstack(out)


# --------------------------------------------------------------------------- stages
def _summary(y, p, names, boot) -> tuple[dict, np.ndarray]:
    m = evaluate(y, p, names)
    cls = np.array([names.index(k) for k in m["per_class"]])
    return {"n": m["n"], "macro_auroc": round(m["macro_auroc"], 4), "macro_auroc_ci95": [round(x, 4) for x in
            ci(macro_boot(boot, cls))], "macro_auprc": round(m["macro_auprc"], 4),
            "mean_prevalence": round(m["mean_prevalence"], 4)}, cls


def _cold_index(master: pd.DataFrame, names: set[str]) -> np.ndarray:
    return np.flatnonzero(master["name"].isin(names).to_numpy())


def patient_stats(dev) -> dict:
    master = pd.read_parquet(PROC / "drug_master.parquet").reset_index(drop=True)
    cm = pd.read_parquet(PROC / "condition_mapping.parquet")
    C, names = len(cm), cm["condition"].tolist()
    A = dict(np.load(PROC / "faers_reports.npz"))
    X = report_features(A)
    rows, _, _ = report_splits(A, master)
    G = all_graphs(master, dev)
    full = RegimenPatientModel(num_classes=C, patient_dim=PATIENT_DIM).to(dev)
    full.load_state_dict(torch.load(OUT / "patient_model.pt", map_location=dev, weights_only=True))
    nop = RegimenPatientModel(num_classes=C, patient_dim=PATIENT_DIM).to(dev)
    nop.load_state_dict(torch.load(OUT / "patient_model_nopatient.pt", map_location=dev, weights_only=True))
    t0 = time.time()
    rates = drug_rates(A, rows["train"], C, len(master))
    log.info(f"drug-frequency baseline fitted ({time.time() - t0:.0f}s)")
    logi = train_logistic(A, X, rows, C, len(master), dev)
    models = {
        "full": lambda r: predict_regimen(full, G, A, X, r, dev),
        "no_patient": lambda r: predict_regimen(nop, G, A, X, r, dev, zero_x=True),
        "logistic": lambda r: predict_logistic(logi, A, X, r, dev),
        "drug_frequency": lambda r: predict_frequency(rates, A, r),
    }
    res: dict = {}
    cold = _cold_index(master, set(cold_drugs(master)))
    for t in TESTS:
        if t not in rows:
            continue
        r = rows[t]
        y = _bits(A["labels"][r], C)
        # unseen-drug reports share a few dozen held-out drugs: resample drugs, not reports
        clusters = first_cold(A["drugs"][r].astype(np.int64), cold) if t == "test_unseen" else None
        res[t] = {"n": len(r), "models": {}, "bootstrap_unit": "held-out drug" if clusters is not None else "report"}
        if clusters is not None:
            res[t]["n_clusters"] = int(clusters.max()) + 1
        boots, keep = {}, {}
        for name, fn in models.items():
            t1 = time.time()
            p = fn(r)
            boots[name] = bootstrap_auroc(y, p, dev, clusters=clusters)
            res[t]["models"][name], cls = _summary(y, p, names, boots[name])
            if name in ("full", "no_patient"):
                keep[name] = p
            log.info(f"{t} [{name}] macro-AUROC {res[t]['models'][name]['macro_auroc']:.4f} "
                     f"CI {res[t]['models'][name]['macro_auroc_ci95']} ({time.time() - t1:.0f}s)")
        M = res[t]["models"]
        for a, b in (("full", "no_patient"), ("full", "logistic"), ("full", "drug_frequency"),
                     ("logistic", "drug_frequency")):
            res[t][f"{a}_vs_{b}"] = paired_difference(macro_boot(boots[a], cls), macro_boot(boots[b], cls),
                                                      M[a]["macro_auroc"] - M[b]["macro_auroc"])
        if clusters is not None:  # DeLong assumes independent cases - not valid for drug-clustered reports
            log.info(f"{t}: full vs no-patient {res[t]['full_vs_no_patient']}")
            continue
        # DeLong per adverse event: where does patient information change the ranking?
        per, pv = [], []
        for c in cls:
            a1, a2, pval = delong(y[:, c], keep["full"][:, c], keep["no_patient"][:, c])
            per.append({"event": names[c], "auroc_full": round(a1, 4), "auroc_no_patient": round(a2, 4)})
            pv.append(pval)
        for d, pa in zip(per, holm(pv), strict=True):
            d["p_holm"] = float(pa)
        res[t]["delong_full_vs_no_patient"] = {
            "events": len(per), "better": sum(d["p_holm"] < 0.05 and d["auroc_full"] > d["auroc_no_patient"] for d in per),
            "worse": sum(d["p_holm"] < 0.05 and d["auroc_full"] < d["auroc_no_patient"] for d in per),
            "per_event": per}
        log.info(f"{t}: full vs no-patient {res[t]['full_vs_no_patient']} | DeLong better "
                 f"{res[t]['delong_full_vs_no_patient']['better']}/{len(per)}")
        del keep
    return res


def pair_stats(dev) -> dict:
    master = pd.read_parquet(PROC / "drug_master.parquet").reset_index(drop=True)
    cm = pd.read_parquet(PROC / "condition_mapping.parquet")
    ds = pd.read_parquet(PROC / "twosides_pairs.parquet")
    C, names = len(cm), cm["condition"].tolist()
    i, j, y, S = pair_splits(master, ds, C)
    inputs = {"gnn": all_graphs(master, dev), "fingerprint": torch.as_tensor(morgan_matrix(master), device=dev)}
    preds: dict = {}
    for kind, inp in inputs.items():
        m = OmniPharmModel(num_classes=C, encoder=FingerprintEncoder() if kind == "fingerprint" else None).to(dev)
        m.load_state_dict(torch.load(OUT / f"pair_{kind}.pt", map_location=dev, weights_only=True))
        m.eval()
        with torch.no_grad():
            emb = m.encoder.encode(inp)
            preds[kind] = {}
            for t in ("test_random_split", "test_unseen_one_drug", "test_unseen_both_drugs"):
                s = S[t]
                ii, jj = torch.as_tensor(i[s], device=dev), torch.as_tensor(j[s], device=dev)
                preds[kind][t] = torch.cat([torch.sigmoid(m(emb, ii[k:k + 65536], jj[k:k + 65536]))
                                            for k in range(0, len(s), 65536)]).float().cpu().numpy()
    res: dict = {}
    cold = sorted(cold_drugs(master))
    cold_test = _cold_index(master, set(cold[1::2]))  # the pair model's held-out test drugs (train_pair)
    for t in ("test_random_split", "test_unseen_one_drug", "test_unseen_both_drugs"):
        yt = y[S[t]]
        clusters = (first_cold(np.stack([i[S[t]], j[S[t]]], 1), cold_test) if t != "test_random_split" else None)
        ps = {**{k: preds[k][t] for k in preds}, "ensemble": (preds["gnn"][t] + preds["fingerprint"][t]) / 2}
        boots = {}
        res[t] = {"n": len(yt), "models": {}, "bootstrap_unit": "held-out drug" if clusters is not None else "pair"}
        if clusters is not None:
            res[t]["n_clusters"] = int(clusters.max()) + 1
        for k, p in ps.items():
            boots[k] = bootstrap_auroc(yt, p, dev, clusters=clusters)
            res[t]["models"][k], cls = _summary(yt, p, names, boots[k])
        M = res[t]["models"]
        for a, b in (("gnn", "fingerprint"), ("ensemble", "gnn"), ("ensemble", "fingerprint")):
            res[t][f"{a}_vs_{b}"] = paired_difference(macro_boot(boots[a], cls), macro_boot(boots[b], cls),
                                                      M[a]["macro_auroc"] - M[b]["macro_auroc"])
        log.info(f"pair {t}: " + " | ".join(f"{k} {v['macro_auroc']:.3f} {v['macro_auroc_ci95']}" for k, v in M.items()))
    return res


def clinical_stats() -> dict:
    """Wilson 95% CIs of the rule-level audits (counts from README / logs/validation)."""
    out = {"audit": {
        "holdout_first_measurement": {"k": 57, "n": 60, "ci95": wilson(57, 60),
                                      "note": "처음 보는 세트 첫 측정(규칙 조정 전) — 독립 평가로 보고할 값"},
        "holdout_major_first": {"k": 20, "n": 20, "ci95": wilson(20, 20)},
        "development_set": {"k": 109, "n": 109, "ci95": wilson(109, 109), "note": "규칙 조정에 쓴 개발용 세트(회귀 검사)"},
        "patients": {"k": 18, "n": 18, "ci95": wilson(18, 18)},
        "foods": {"k": 22, "n": 22, "ci95": wilson(22, 22)},
        "controlled_substances": {"k": 15, "n": 15, "ci95": wilson(15, 15)},
    }}
    f = settings.logs_dir / "validation" / "dur_recall.json"
    if f.exists():
        d = json.loads(f.read_text(encoding="utf-8"))["all"]
        n = d["n"]
        out["dur_external"] = {k: {"rate": d[k], "ci95": wilson(round(d[k] * n), n)}
                               for k in ("위험", "위험_or_주의")} | {"n": n}
    return out


def build() -> dict:
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    t0 = time.time()
    res = {"computed_at": time.strftime("%Y-%m-%d %H:%M"), "bootstrap": f"paired Poisson bootstrap, {B} resamples",
           "clinical": clinical_stats(), "pair": pair_stats(dev)}
    (OUT / "stats_metrics.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    res["patient"] = patient_stats(dev)
    res["seconds"] = round(time.time() - t0)
    (OUT / "stats_metrics.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    log.info(f"stats done in {(time.time() - t0) / 60:.1f} min -> {OUT / 'stats_metrics.json'}")
    return res
