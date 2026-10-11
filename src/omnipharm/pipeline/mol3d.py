"""Pre-compute 3D conformers for every drug in drug_master (served to the 3D viewer)."""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ProcessPoolExecutor

import pandas as pd

from omnipharm.chem.conformer import mol_3d
from omnipharm.pipeline.common import PROC

log = logging.getLogger("pipeline.mol3d")


def _one(args: tuple[str, str]) -> tuple[str, dict | None]:
    name, smi = args
    try:
        return name, mol_3d(smi)
    except Exception:
        return name, None


def build() -> None:
    master = pd.read_parquet(PROC / "drug_master.parquet")
    jobs = list(zip(master["name"], master["std_smiles"], strict=True))
    out: dict[str, dict] = {}
    with ProcessPoolExecutor(max(1, (os.cpu_count() or 2) - 2)) as ex:
        for k, (name, res) in enumerate(ex.map(_one, jobs, chunksize=8), 1):
            if res is not None:
                out[name] = res
            if k % 200 == 0:
                log.info(f"3D {k}/{len(jobs)}")
    (PROC / "mol3d.json").write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    log.info(f"mol3d: {len(out)}/{len(jobs)} molecules with 3D coordinates "
             f"({sum(1 for v in out.values() if v['atoms'])} embedded)")
