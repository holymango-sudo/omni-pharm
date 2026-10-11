"""Patient feature vector - the single definition shared by training and inference."""

from __future__ import annotations

import numpy as np

from omnipharm.knowledge import COMORBIDITIES

MAX_DRUGS = 10  # regimen size the patient model is trained on
FEATURES = ["age", "age_missing", "sex_f", "sex_m", "wt", "wt_missing", *COMORBIDITIES, "n_drugs"]
PATIENT_DIM = len(FEATURES)
SEX_CODE = {"": 0, "F": 1, "M": 2}


def patient_matrix(age: np.ndarray, sex: np.ndarray, wt: np.ndarray, flags: np.ndarray,
                   n_drugs: np.ndarray) -> np.ndarray:
    """age (years, NaN = unknown), sex (0 unknown / 1 F / 2 M), wt (kg, NaN = unknown),
    flags [N, len(COMORBIDITIES)] 0/1, n_drugs -> float32 [N, PATIENT_DIM]."""
    n = len(age)
    x = np.zeros((n, PATIENT_DIM), dtype=np.float32)
    am, wm = np.isnan(age), np.isnan(wt)
    x[:, 0] = np.where(am, 0.0, age / 100.0)
    x[:, 1] = am
    x[:, 2] = sex == 1
    x[:, 3] = sex == 2
    x[:, 4] = np.where(wm, 0.0, np.clip(wt, 0, 250) / 100.0)
    x[:, 5] = wm
    x[:, 6:6 + len(COMORBIDITIES)] = flags
    x[:, -1] = np.clip(n_drugs, 0, MAX_DRUGS) / MAX_DRUGS
    return x


def patient_vector(age: float | None, sex: str | None, wt: float | None, flags: dict[str, bool],
                   n_drugs: int) -> np.ndarray:
    return patient_matrix(
        np.array([np.nan if age is None else float(age)]),
        np.array([SEX_CODE.get((sex or "").upper()[:1], 0)]),
        np.array([np.nan if wt is None else float(wt)]),
        np.array([[float(bool(flags.get(k))) for k in COMORBIDITIES]]),
        np.array([n_drugs]),
    )[0]
