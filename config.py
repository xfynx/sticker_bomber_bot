"""Конфигурация из переменных окружения."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _ids(name: str) -> set[int]:
    raw = os.getenv(name, "")
    out: set[int] = set()
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk.lstrip("-").isdigit():
            out.add(int(chunk))
    return out


@dataclass(frozen=True)
class Settings:
    bot_token: str
    owner_ids: frozenset[int]
    db_path: Path
    default_burst: int = 5
    default_cooldown: int = 5
    default_chance: int = 100
    default_delay: int = 3
    max_delay: int = 60
    max_burst: int = 25
    send_concurrency: int = 8
    log_level: str = "INFO"

    def is_owner(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.owner_ids


def load_settings() -> Settings:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN не задан. Скопируй .env.example в .env и заполни.")

    owners = _ids("OWNER_IDS")
    if not owners:
        raise RuntimeError("OWNER_IDS не задан — без него никто не сможет управлять паками.")

    db_raw = os.getenv("DB_PATH", "data/bot.sqlite3").strip()
    db_path = Path(db_raw)
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)

    return Settings(
        bot_token=token,
        owner_ids=frozenset(owners),
        db_path=db_path,
        default_burst=max(1, _int("DEFAULT_BURST", 5)),
        default_cooldown=max(0, _int("DEFAULT_COOLDOWN", 5)),
        default_chance=min(100, max(1, _int("DEFAULT_CHANCE", 100))),
        default_delay=min(60, max(0, _int("DEFAULT_DELAY", 3))),
        max_burst=max(1, _int("MAX_BURST", 25)),
        send_concurrency=max(1, _int("SEND_CONCURRENCY", 8)),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
    )


settings = load_settings()
