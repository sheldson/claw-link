"""Relay server configuration — reads from env vars or config.yaml."""

from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass, field

import yaml


_CONFIG_YAML_PATHS = [
    Path("config.yaml"),
    Path("relay/config.yaml"),
    Path(__file__).parent.parent / "config.yaml",
]


def _load_yaml() -> dict:
    for p in _CONFIG_YAML_PATHS:
        if p.exists():
            return yaml.safe_load(p.read_text()) or {}
    return {}


def _get(key: str, yaml_cfg: dict, default: str) -> str:
    """Env var wins, then YAML, then default."""
    return os.environ.get(key, yaml_cfg.get(key.lower(), default))


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite+aiosqlite:///claw_relay.db"
    host: str = "0.0.0.0"
    port: int = 8000
    max_pending_messages: int = 1000
    message_expire_days: int = 7

    @classmethod
    def load(cls) -> Settings:
        cfg = _load_yaml()
        return cls(
            database_url=_get("DATABASE_URL", cfg, cls.database_url),
            host=_get("HOST", cfg, cls.host),
            port=int(_get("PORT", cfg, str(cls.port))),
            max_pending_messages=int(
                _get("MAX_PENDING_MESSAGES", cfg, str(cls.max_pending_messages))
            ),
            message_expire_days=int(
                _get("MESSAGE_EXPIRE_DAYS", cfg, str(cls.message_expire_days))
            ),
        )


settings = Settings.load()
