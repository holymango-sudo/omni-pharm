"""Shared pipeline helpers."""

from __future__ import annotations

import logging

from omnipharm.config import PROJECT_ROOT, settings

RAW = settings.raw_dir
INTERIM = settings.interim_dir
PROC = settings.processed_dir
CACHE = settings.cache_dir
ART = settings.artifacts_dir


def setup_logging(name: str = "pipeline") -> logging.Logger:
    for d in (INTERIM, PROC, CACHE, ART, settings.logs_dir):
        d.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            handlers=[logging.FileHandler(settings.logs_dir / f"{name}.log", encoding="utf-8"),
                      logging.StreamHandler()],
        )
    # httpx logs full request URLs at INFO, which would write API keys (serviceKey=...) into log files
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return logging.getLogger(name)


__all__ = ["ART", "CACHE", "INTERIM", "PROC", "PROJECT_ROOT", "RAW", "setup_logging"]
