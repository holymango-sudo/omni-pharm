"""OMNI-PHARM pipeline CLI (each stage is idempotent; downloads are cached under data/raw).

  python scripts/pipeline.py <stage>    or    python scripts/pipeline.py all

  twosides      TWOSIDES pair signals              structures   PubChem structures -> drug_master
  faers         FDA FAERS quarters (2004-)         canada       Health Canada adverse reaction DB
  extend        new small molecules from FAERS     labels       pair labels + patient report table
  fdalabels     representative FDA label per drug  mol3d        3D coordinates for the UI
  train-pair    pair structure model (GPU)         train-patient  patient regimen model (GPU, --no-patient)
  mfds          Korean products (API key)          world        US/CA/EU/FR/JP products
  chembl-drugs  approved drugs: names, ATC         strengths    per-unit ingredient amounts
  dur           MFDS DUR rules + quasi-drugs       chembl       CYP / hERG / mechanism pharmacology
  labelsearch   full-text index of all FDA labels   pk           Tmax / half-life from FDA labels (body view)
  stats         95% CIs, significance tests, baselines for the trained models (GPU, ~30 min)
  kr-labels     Korean labels of every Korean medicine (용법·주의사항, API key)
  offsides      OFFSIDES single-drug signals (confirms 'a drug's own risk' lines)
  chembl-dump   ChEMBL 37 full dump: drug indications + pharmacology for every molecule (after download_data.py)
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omnipharm.pipeline.common import setup_logging


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["twosides", "faers", "canada", "structures", "extend", "mfds", "dur", "world", "strengths",
                                      "chembl", "chembl-drugs", "labelsearch", "pk",
                                      "labels", "fdalabels",
                                      "train-pair", "train-patient", "mol3d", "stats", "kr-labels", "offsides",
                                      "chembl-dump", "all"])
    ap.add_argument("--faers-start", default="2004Q1")  # legacy AERS 2004Q1-2012Q3 + FAERS
    ap.add_argument("--epochs", type=int, default=None)
    ap.add_argument("--max-drugs", type=int, default=None)
    ap.add_argument("--no-patient", action="store_true", help="train-patient ablation without patient features")
    args = ap.parse_args()
    log = setup_logging(args.stage if args.stage != "all" else "pipeline")
    t0 = time.time()
    # dependency order: models need drug_master + the report table; product DBs and ChEMBL add untrained
    # ingredients that strengths / DUR / pharmacology / the label index then use
    stages = ([args.stage] if args.stage != "all" else
              ["twosides", "structures", "faers", "canada", "extend", "labels", "fdalabels", "mol3d", "train-pair",
               "train-patient", "mfds", "world", "chembl-drugs", "strengths", "dur", "chembl", "labelsearch", "pk"])
    for s in stages:
        log.info(f"===== stage: {s}")
        if s == "twosides":
            from omnipharm.pipeline import twosides
            twosides.build()
        elif s == "faers":
            from omnipharm.pipeline import faers
            y, q = args.faers_start.upper().split("Q")
            faers.build(start=(int(y), int(q)))
        elif s == "canada":
            from omnipharm.pipeline import canada
            canada.build()
        elif s == "structures":
            from omnipharm.pipeline import structures
            structures.build(args.max_drugs)
        elif s == "extend":
            from omnipharm.pipeline import extend
            extend.build()
        elif s == "mfds":
            from omnipharm.pipeline import mfds
            mfds.build()
        elif s == "dur":
            from omnipharm.pipeline import dur
            dur.build()
        elif s == "world":
            from omnipharm.pipeline import world
            world.build()
        elif s == "chembl":
            from omnipharm.pipeline import chembl
            chembl.build()
        elif s == "chembl-drugs":
            from omnipharm.pipeline import chembl_drugs
            chembl_drugs.build()
        elif s == "labelsearch":
            from omnipharm.pipeline import labelsearch
            labelsearch.build()
        elif s == "pk":
            from omnipharm.pipeline import pk
            pk.build()
        elif s == "strengths":
            from omnipharm.pipeline import strengths
            strengths.build()
        elif s == "labels":
            from omnipharm.pipeline import labels
            labels.build()
        elif s == "fdalabels":
            from omnipharm.pipeline import fda_labels
            fda_labels.build()
        elif s == "train-pair":
            from omnipharm.pipeline import train_pair
            train_pair.train(epochs=args.epochs)
        elif s == "train-patient":
            from omnipharm.pipeline import train_patient
            train_patient.train(epochs=args.epochs, no_patient=args.no_patient)
        elif s == "mol3d":
            from omnipharm.pipeline import mol3d
            mol3d.build()
        elif s == "stats":
            from omnipharm.pipeline import stats
            stats.build()
        elif s == "kr-labels":
            from omnipharm.pipeline import kr_labels
            kr_labels.build()
        elif s == "offsides":
            from omnipharm.pipeline import offsides
            offsides.build()
        elif s == "chembl-dump":
            from omnipharm.pipeline import chembl_dump
            chembl_dump.build()
    log.info(f"done in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
