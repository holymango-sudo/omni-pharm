"""Registry of the bulk sources fetched by `scripts/download_data.py`.

Every source records its license so the provenance of every model input is auditable. The other inputs
(FAERS, PubChem, 식약처, NDC, DPD, EMA, BDPM, MHLW, KEGG) are fetched by their own pipeline stages; all
licenses are listed in docs/DATA_LICENSES.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Acquisition(StrEnum):
    HTTP = "http"  # direct download, URL known
    OPENFDA_LABEL = "openfda_label"  # partition list resolved from api.fda.gov/download.json


@dataclass(frozen=True, slots=True)
class Source:
    name: str
    description: str
    license: str
    commercial_use: str
    acquisition: Acquisition
    urls: tuple[str, ...] = field(default_factory=tuple)
    connections: int = 1  # > 1: parallel range download (servers that throttle each connection)
    checksum_url: str | None = None  # publisher's '<sha256>  <file>' list, verified after download


SOURCES: dict[str, Source] = {
    s.name: s
    for s in [
        Source(
            name="twosides",
            description="TWOSIDES: drug-pair adverse effect signals mined from FAERS (Tatonetti lab, nSIDES)",
            license="nSIDES terms - research use",
            commercial_use="verify with licensor",
            acquisition=Acquisition.HTTP,
            urls=("https://tatonettilab-resources.s3.amazonaws.com/nsides/TWOSIDES.csv.gz",),
        ),
        Source(
            name="openfda_label",
            description="FDA structured product labels (SPL): interaction / contraindication evidence",
            license="Public domain (US Government work), openFDA terms",
            commercial_use="yes",
            acquisition=Acquisition.OPENFDA_LABEL,
        ),
        Source(
            name="offsides",
            description="OFFSIDES: single-drug adverse effect signals from FAERS with propensity-score matched controls "
                        "(Tatonetti lab, nSIDES) - confirms a drug's own adverse effects",
            license="nSIDES terms - research use",
            commercial_use="verify with licensor",
            acquisition=Acquisition.HTTP,
            urls=("https://tatonettilab-resources.s3.amazonaws.com/nsides/OFFSIDES.csv.gz",),
        ),
        Source(
            name="chembl_sqlite",
            description="ChEMBL 37 full SQLite dump (EMBL-EBI): bioactivities, mechanisms, metabolism, drug indications",
            license="CC BY-SA 3.0",
            commercial_use="yes (share-alike, attribution)",
            acquisition=Acquisition.HTTP,
            urls=("https://ftp.ebi.ac.uk/pub/databases/chembl/ChEMBLdb/releases/chembl_37/chembl_37_sqlite.tar.gz",),
            connections=24,  # EBI throttles each connection (~40-60 KB/s): 24 connections ~1.5 MB/s (5.4 GB in ~1 h)
            checksum_url="https://ftp.ebi.ac.uk/pub/databases/chembl/ChEMBLdb/releases/chembl_37/checksums.txt",
        ),
    ]
}
