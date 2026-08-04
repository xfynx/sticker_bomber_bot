"""Общие фикстуры. Env выставляем до импорта config, иначе он упадёт на валидации."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "123456:TEST-TOKEN")
os.environ.setdefault("OWNER_IDS", "111")
os.environ.setdefault("DB_PATH", str(ROOT / "data" / "test.sqlite3"))

import pytest  # noqa: E402

from storage import Storage  # noqa: E402


@pytest.fixture
async def storage(tmp_path) -> Storage:
    st = Storage(tmp_path / "test.sqlite3")
    await st.connect()
    try:
        yield st
    finally:
        await st.close()


@pytest.fixture
def stickers_factory():
    """Генератор пар (file_id, emoji) для наполнения пака."""

    def make(prefix: str, count: int) -> list[tuple[str, str]]:
        return [(f"{prefix}_{i}", "🔥") for i in range(count)]

    return make
