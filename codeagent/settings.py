"""Runtime settings, read from environment variables and the YAML files in /config."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name, default)
    # Allow inline comments in .env values (docker compose keeps them verbatim).
    if value and " #" in value:
        value = value.split(" #", 1)[0]
    return value.strip()


def _env_bool(name: str, default: bool = False) -> bool:
    return _env(name, str(default)).lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    mongodb_uri: str = field(default_factory=lambda: _env("MONGODB_URI"))
    mongodb_db: str = field(default_factory=lambda: _env("MONGODB_DB", "coding_agent"))

    voyage_api_key: str = field(default_factory=lambda: _env("VOYAGE_API_KEY"))
    voyage_base_url: str = field(default_factory=lambda: _env("VOYAGE_BASE_URL", "https://api.voyageai.com/v1").rstrip("/"))
    embed_model: str = field(default_factory=lambda: _env("EMBED_MODEL", "voyage-code-3"))
    embed_dimensions: int = field(default_factory=lambda: _env_int("EMBED_DIMENSIONS", 1024))
    rerank_model: str = field(default_factory=lambda: _env("RERANK_MODEL", "rerank-2.5"))

    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    openai_base_url: str = field(default_factory=lambda: _env("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"))
    openai_model: str = field(default_factory=lambda: _env("OPENAI_MODEL", "gpt-5-mini"))

    gitlab_url: str = field(default_factory=lambda: _env("GITLAB_URL").rstrip("/"))
    gitlab_token: str = field(default_factory=lambda: _env("GITLAB_TOKEN"))

    config_dir: Path = field(default_factory=lambda: Path(_env("CONFIG_DIR", "/config")))
    ingest_interval_seconds: int = field(default_factory=lambda: _env_int("INGEST_INTERVAL_SECONDS", 600))
    log_prompts: bool = field(default_factory=lambda: _env_bool("LOG_PROMPTS", False))
    sources_footer: bool = field(default_factory=lambda: _env_bool("SOURCES_FOOTER", True))
    events_ttl_days: int = field(default_factory=lambda: _env_int("EVENTS_TTL_DAYS", 90))
    rollup_interval_seconds: int = field(default_factory=lambda: _env_int("ROLLUP_INTERVAL_SECONDS", 300))

    # Retrieval tuning
    top_k: int = field(default_factory=lambda: _env_int("RETRIEVAL_TOP_K", 8))
    candidates: int = field(default_factory=lambda: _env_int("RETRIEVAL_CANDIDATES", 30))
    max_context_chars: int = field(default_factory=lambda: _env_int("MAX_CONTEXT_CHARS", 24000))

    def yaml(self, name: str) -> dict:
        path = self.config_dir / name
        if not path.exists():
            return {}
        with path.open() as fh:
            return yaml.safe_load(fh) or {}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
