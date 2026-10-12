"""Project paths and settings (overridable via environment / .env, prefix OMNIPHARM_)."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VERSION = "2.16.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OMNIPHARM_", env_file=PROJECT_ROOT / ".env", extra="ignore")

    data_dir: Path = PROJECT_ROOT / "data"
    artifacts_dir: Path = PROJECT_ROOT / "artifacts"
    logs_dir: Path = PROJECT_ROOT / "logs"
    frontend_dist: Path = PROJECT_ROOT / "frontend" / "dist"

    # External services - all optional. The system works fully offline from local files.
    data_go_kr_api_key: str | None = None  # 공공데이터포털 (식약처 의약품 허가정보) - only for `pipeline.py mfds`
    ollama_url: str = "http://127.0.0.1:11434"
    # gemma4:12b won scripts/bench_llm.py on an RTX 5070 Ti (logs/llm_bench): fits fully in VRAM next to the
    # engine, ~145 tok/s, no invented numbers, all sections, verdict stated in every case. qwen3.8:27b writes
    # slightly richer text but only ~15 tok/s (partly on CPU). Any Ollama model via OMNIPHARM_LLM_MODEL.
    llm_model: str = "gemma4:12b"
    online_lookup: bool = True  # PubChem name lookup for drugs of countries without a local product DB

    # Access control. On a shared network set a PIN: every API call must then carry it
    # (the web UI asks for it once). Empty = no PIN (fine when bound to 127.0.0.1 only).
    access_pin: str | None = None

    # Commercial deployment: switch off components whose source data is licensed for research only -
    # TWOSIDES (pair structure model + observed pair signals) and KEGG (Japanese product name translation).
    # FDA / MFDS / Health Canada / ChEMBL / PubChem based evidence, the patient model and all rules stay on.
    commercial_safe: bool = False

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def interim_dir(self) -> Path:
        return self.data_dir / "interim"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"


settings = Settings()
