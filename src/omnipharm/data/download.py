"""Reproducible, resumable downloader.

* Resumes interrupted downloads (HTTP Range) and retries with backoff.
* Writes to ``*.part`` and atomically renames on success - a file that exists
  under its final name is always complete.
* Records SHA-256, size, URL and timestamp in ``data/raw/MANIFEST.json`` so any
  trained model can be traced back to the exact bytes it was built from.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from tqdm import tqdm

from omnipharm.config import settings
from omnipharm.data.sources import SOURCES, Acquisition, Source

log = logging.getLogger(__name__)

CHUNK = 1 << 20
MAX_RETRIES = 6
TIMEOUT = httpx.Timeout(30.0, read=120.0)
USER_AGENT = "OMNI-PHARM/2.1 (drug-interaction research tool)"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def _manifest_path() -> Path:
    return settings.raw_dir / "MANIFEST.json"


def load_manifest() -> dict[str, dict[str, object]]:
    p = _manifest_path()
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _save_manifest(manifest: dict[str, dict[str, object]]) -> None:
    p = _manifest_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(p)


def download_file(url: str, dest: Path, client: httpx.Client) -> Path:
    """Download ``url`` to ``dest`` with resume + retry. Returns ``dest``."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        log.info("exists, skipping: %s", dest)
        return dest
    part = dest.with_name(dest.name + ".part")

    for attempt in range(1, MAX_RETRIES + 1):
        offset = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        try:
            with client.stream("GET", url, headers=headers) as r:
                if r.status_code == 416:  # already complete
                    break
                r.raise_for_status()
                if offset and r.status_code != 206:  # server ignored Range -> restart
                    offset = 0
                total = int(r.headers.get("Content-Length", 0)) + offset
                mode = "ab" if offset else "wb"
                with (
                    part.open(mode) as f,
                    tqdm(
                        total=total or None, initial=offset, unit="B", unit_scale=True, desc=dest.name
                    ) as bar,
                ):
                    for chunk in r.iter_bytes(CHUNK):
                        f.write(chunk)
                        bar.update(len(chunk))
            if total and part.stat().st_size != total:
                raise httpx.HTTPError(f"size mismatch: got {part.stat().st_size}, expected {total}")
            break
        except (httpx.HTTPError, OSError) as exc:
            if attempt == MAX_RETRIES:
                raise RuntimeError(f"download failed after {MAX_RETRIES} attempts: {url}") from exc
            wait = min(2**attempt, 60)
            log.warning("attempt %d failed (%s); retrying in %ds", attempt, exc, wait)
            time.sleep(wait)

    part.replace(dest)
    return dest


def _openfda_label_urls(client: httpx.Client) -> list[str]:
    r = client.get("https://api.fda.gov/download.json")
    r.raise_for_status()
    parts = r.json()["results"]["drug"]["label"]["partitions"]
    return [p["file"] for p in parts]


def resolve_urls(source: Source, client: httpx.Client) -> list[str]:
    if source.acquisition is Acquisition.HTTP:
        return list(source.urls)
    return _openfda_label_urls(client)


def fetch(name: str) -> list[Path]:
    source = SOURCES[name]
    manifest = load_manifest()
    out: list[Path] = []
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        for url in resolve_urls(source, client):
            dest = settings.raw_dir / name / Path(urlparse(url).path).name
            download_file(url, dest, client)
            key = dest.relative_to(settings.raw_dir).as_posix()
            if key not in manifest:
                manifest[key] = {
                    "source": name,
                    "url": url,
                    "bytes": dest.stat().st_size,
                    "sha256": sha256_file(dest),
                    "downloaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "license": source.license,
                }
                _save_manifest(manifest)
            out.append(dest)
    return out
