"""Statistics stage (pipeline/stats.py): the AUROC, bootstrap, DeLong and Wilson code agree with references."""

from __future__ import annotations

import numpy as np
import torch
from sklearn.metrics import roc_auc_score

from omnipharm.pipeline import stats


def _groups(p):
    return torch.unique(torch.as_tensor(p), return_inverse=True)[1]


def test_weighted_auroc_equals_sklearn_with_ties_and_weights():
    rng = np.random.default_rng(1)
    y = rng.integers(0, 2, 3000).astype(float)
    p = np.round(rng.random(3000) + 0.3 * y, 1)  # many ties
    g = _groups(p)
    assert abs(stats.weighted_auroc(g, torch.as_tensor(y), torch.ones(3000, dtype=torch.float64))
               - roc_auc_score(y, p)) < 1e-9
    w = rng.integers(0, 4, 3000)  # integer weights = repeating rows
    rep = np.repeat(np.arange(3000), w)
    assert abs(stats.weighted_auroc(g, torch.as_tensor(y), torch.as_tensor(w, dtype=torch.float64))
               - roc_auc_score(y[rep], p[rep])) < 1e-9


def test_bootstrap_is_centred_and_paired():
    rng = np.random.default_rng(2)
    y = rng.integers(0, 2, (4000, 2)).astype(np.uint8)
    good = rng.random((4000, 2)) + 0.8 * y
    weak = rng.random((4000, 2)) + 0.2 * y
    dev = torch.device("cpu")
    a, b = stats.bootstrap_auroc(y, good, dev, n_boot=200), stats.bootstrap_auroc(y, weak, dev, n_boot=200)
    point = np.mean([roc_auc_score(y[:, c], good[:, c]) for c in range(2)])
    lo, hi = stats.ci(stats.macro_boot(a, np.arange(2)))
    assert lo < point < hi and hi - lo < 0.05
    d = stats.paired_difference(stats.macro_boot(a, np.arange(2)), stats.macro_boot(b, np.arange(2)), 0.1)
    assert d["ci95"][0] > 0 and d["p_is_upper_bound"]  # clearly better in every resample
    same = stats.paired_difference(stats.macro_boot(a, np.arange(2)), stats.macro_boot(a, np.arange(2)), 0.0)
    assert same["ci95"] == [0.0, 0.0]


def test_cluster_bootstrap_is_wider_when_cases_share_a_cluster():
    rng = np.random.default_rng(4)
    K, per = 30, 200
    clusters = np.repeat(np.arange(K), per)
    shift = np.repeat(rng.normal(0, 1, K), per)  # each held-out drug is easier or harder as a whole
    y = rng.integers(0, 2, (K * per, 1)).astype(np.uint8)
    p = rng.random((K * per, 1)) + (0.5 + 0.4 * shift[:, None]) * y
    dev = torch.device("cpu")
    case = stats.ci(stats.bootstrap_auroc(y, p, dev, n_boot=200)[:, 0])
    clus = stats.ci(stats.bootstrap_auroc(y, p, dev, n_boot=200, clusters=clusters)[:, 0])
    assert clus[1] - clus[0] > 1.5 * (case[1] - case[0])
    drugs = np.array([[5, 9, -1], [9, 2, -1], [7, -1, -1]])
    assert stats.first_cold(drugs, np.array([9, 7])).tolist() == [1, 1, 0]


def test_delong_matches_auroc_and_detects_a_difference():
    rng = np.random.default_rng(3)
    y = rng.integers(0, 2, 5000)
    p1, p2 = rng.random(5000) + 0.6 * y, rng.random(5000) + 0.2 * y
    a1, a2, p = stats.delong(y, p1, p2)
    assert abs(a1 - roc_auc_score(y, p1)) < 1e-9 and abs(a2 - roc_auc_score(y, p2)) < 1e-9
    assert p < 1e-6
    assert stats.delong(y, p1, p1 + 1e-12 * rng.random(5000))[2] > 0.05  # same ranking: no difference


def test_holm_and_wilson():
    assert stats.holm([0.01, 0.04, 0.03]) == [0.03, 0.06, 0.06]
    lo, hi = stats.wilson(57, 60)
    assert abs(lo - 0.863) < 0.002 and abs(hi - 0.983) < 0.002
    assert stats.wilson(20, 20)[1] == 1.0 and stats.wilson(20, 20)[0] > 0.83
