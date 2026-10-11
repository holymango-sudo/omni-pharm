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
    ]
}
