"""Paged download of a data.go.kr MFDS operation into gzip-compressed page files (resumable: finished pages are kept).

The service key comes from .env and never appears in logs or errors (the httpx logger is kept at WARNING because it
logs request URLs). data.go.kr returns at most 100 rows per page for these services.
"""

from __future__ import annotations

import gzip
import json
import logging
import time
from pathlib import Path

import httpx

from omnipharm.config import settings

log = logging.getLogger("pipeline.mfds_api")


def _items(body: dict) -> list[dict]:
    items = body.get("items") or []
    if isinstance(items, dict):
        items = items.get("item") or []
    return [i.get("item", i) if isinstance(i, dict) else i for i in items]


def fetch_pages(url: str, cache: Path, keep: tuple[str, ...] | None = None, rows: int = 100,
                label: str = "") -> list[dict]:
    """Every row of the operation; page files cache/page_NNNNN.json.gz ({'total', 'items'}). keep = fields to store."""
    key = settings.data_go_kr_api_key
    if not key:
        raise RuntimeError("OMNIPHARM_DATA_GO_KR_API_KEY is not set (.env)")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cache.mkdir(parents=True, exist_ok=True)

    def page(c: httpx.Client, n: int) -> dict:
        f = cache / f"page_{n:05d}.json.gz"
        if f.exists():
            return json.loads(gzip.decompress(f.read_bytes()))
        for attempt in range(8):
            try:
                r = c.get(url, params={"serviceKey": key, "type": "json", "numOfRows": rows, "pageNo": n})
                r.raise_for_status()
                body = r.json()["body"]
                break
            except Exception as e:  # network / quota hiccup; the message never contains the key
                if attempt == 7:
                    raise RuntimeError(f"{label or url.rsplit('/', 1)[-1]} page {n}: {type(e).__name__}") from None
                time.sleep(5 * (attempt + 1))
        items = _items(body)
        out = {"total": int(body.get("totalCount") or 0),
               "items": [{k: it.get(k) for k in keep} if keep else it for it in items]}
        f.write_bytes(gzip.compress(json.dumps(out, ensure_ascii=False).encode("utf-8")))
        return out

    rows_out: list[dict] = []
    with httpx.Client(timeout=180) as c:
        first = page(c, 1)
        pages = (first["total"] + rows - 1) // rows
        rows_out += first["items"]
        for n in range(2, pages + 1):
            rows_out += page(c, n)["items"]
            if n % 100 == 0:
                log.info(f"{label}: page {n}/{pages} ({len(rows_out):,} rows)")
    log.info(f"{label}: {len(rows_out):,} rows")
    return rows_out
