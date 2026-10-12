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


PIECE = 8 << 20  # parallel download piece (small: the server drops long connections)


def download_file_parallel(url: str, dest: Path, client: httpx.Client, connections: int) -> Path:
    """Download ``url`` in PIECE-sized ranges over ``connections`` parallel connections (servers that throttle each
    connection - EMBL-EBI gives ~40 KB/s per connection). Resumable: finished pieces are listed in ``*.part.json``."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from threading import Lock

    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        log.info("exists, skipping: %s", dest)
        return dest
    head = client.head(url)
    head.raise_for_status()
    size = int(head.headers["Content-Length"])
    part, state_f = dest.with_name(dest.name + ".part"), dest.with_name(dest.name + ".part.json")
    if not part.exists() or part.stat().st_size != size:
        with part.open("wb") as f:
            f.truncate(size)
        state_f.unlink(missing_ok=True)
    state = json.loads(state_f.read_text()) if state_f.exists() else {}
    # progress is valid only for the same piece size (older runs stored a bare list)
    done: set[int] = set(state.get("done", [])) if isinstance(state, dict) and state.get("piece") == PIECE else set()
    pieces = [k for k in range((size + PIECE - 1) // PIECE) if k not in done]
    lock = Lock()
    bar = tqdm(total=size, initial=len(done) * PIECE, unit="B", unit_scale=True, desc=dest.name)

    def get(k: int) -> int:
        lo, hi = k * PIECE, min(size, (k + 1) * PIECE) - 1
        buf = bytearray()  # kept across retries: a dropped connection continues where it stopped
        for attempt in range(1, 4 * MAX_RETRIES + 1):
            try:
                with client.stream("GET", url, headers={"Range": f"bytes={lo + len(buf)}-{hi}"}) as r:
                    if r.status_code != 206:
                        raise httpx.HTTPError(f"range not honoured (HTTP {r.status_code})")
                    for chunk in r.iter_bytes(CHUNK):
                        buf += chunk
                if len(buf) != hi - lo + 1:
                    raise httpx.HTTPError(f"piece {k}: {len(buf)} of {hi - lo + 1} bytes")
                with lock:
                    with part.open("r+b") as f:
                        f.seek(lo)
                        f.write(buf)
                    done.add(k)
                    state_f.write_text(json.dumps({"piece": PIECE, "done": sorted(done)}))
                    bar.update(len(buf))
                return k
            except (httpx.HTTPError, OSError) as exc:
                if attempt == 4 * MAX_RETRIES:
                    raise RuntimeError(f"piece {k} failed: {exc}") from exc
                log.info("piece %d: %s - resuming", k, exc)
                time.sleep(min(attempt, 10))
        return k

    with ThreadPoolExecutor(connections) as ex:
        for fut in as_completed([ex.submit(get, k) for k in pieces]):
            fut.result()
    bar.close()
    part.replace(dest)
    state_f.unlink(missing_ok=True)
    return dest


def verify_checksum(dest: Path, checksum_url: str, client: httpx.Client) -> str:
    """Compare with the publisher's checksum list ('<sha256>  <file name>' lines); returns the SHA-256."""
    digest = sha256_file(dest)
    listed = {ln.split()[-1].lstrip("*"): ln.split()[0] for ln in client.get(checksum_url).text.splitlines()
              if len(ln.split()) >= 2}
    want = listed.get(dest.name)
    if want and want.lower() != digest:
        dest.rename(dest.with_name(dest.name + ".bad"))
        raise RuntimeError(f"checksum mismatch for {dest.name}: {digest} != {want}")
    return digest


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
            if source.connections > 1:
                download_file_parallel(url, dest, client, source.connections)
            else:
                download_file(url, dest, client)
            key = dest.relative_to(settings.raw_dir).as_posix()
            if key not in manifest:
                digest = (verify_checksum(dest, source.checksum_url, client) if source.checksum_url
                          else sha256_file(dest))
                manifest[key] = {
                    "source": name,
                    "url": url,
                    "bytes": dest.stat().st_size,
                    "sha256": digest,
                    "downloaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "license": source.license,
                }
                _save_manifest(manifest)
            out.append(dest)
    return out
