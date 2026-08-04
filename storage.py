"""Слой хранения на SQLite (aiosqlite).

Схема:
  packs        — стикерпаки, добавленные владельцем; kind = 'default' | 'nsfw'
  stickers     — закэшированные file_id стикеров из паков
  chats        — чаты/каналы, где живёт бот, и их настройки
  triggers     — слова-триггеры; chat_id = 0 означает глобальный список
  pack_scope   — опциональный вайтлист паков для конкретного чата
  admin_ctx    — какой чат сейчас настраивает пользователь из лички
  phrases      — фразы-предупреждения перед залпом
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import aiosqlite

GLOBAL_CHAT_ID = 0

KIND_DEFAULT = "default"
KIND_NSFW = "nsfw"
KINDS = (KIND_DEFAULT, KIND_NSFW)

MATCH_WORD = "word"
MATCH_SUBSTRING = "substring"
MATCH_MODES = (MATCH_WORD, MATCH_SUBSTRING)


def kinds_for(nsfw: bool) -> list[str]:
    """Какие категории паков доступны чату.

    NSFW — это переключение пула, а не расширение: с включённым флагом летят
    только 🔞 паки, обычные не подмешиваются.
    """
    return [KIND_NSFW] if nsfw else [KIND_DEFAULT]

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS packs (
    set_name      TEXT PRIMARY KEY,
    title         TEXT NOT NULL DEFAULT '',
    kind          TEXT NOT NULL DEFAULT 'default',
    enabled       INTEGER NOT NULL DEFAULT 1,
    sticker_count INTEGER NOT NULL DEFAULT 0,
    updated_at    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS stickers (
    file_id  TEXT PRIMARY KEY,
    set_name TEXT NOT NULL REFERENCES packs(set_name) ON DELETE CASCADE,
    emoji    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_stickers_set ON stickers(set_name);

CREATE TABLE IF NOT EXISTS chats (
    chat_id    INTEGER PRIMARY KEY,
    title      TEXT NOT NULL DEFAULT '',
    type       TEXT NOT NULL DEFAULT '',
    enabled    INTEGER NOT NULL DEFAULT 1,
    nsfw       INTEGER NOT NULL DEFAULT 0,
    burst      INTEGER NOT NULL DEFAULT 5,
    cooldown   INTEGER NOT NULL DEFAULT 5,
    chance     INTEGER NOT NULL DEFAULT 100,
    delay      INTEGER NOT NULL DEFAULT 3,
    warn       INTEGER NOT NULL DEFAULT 1,
    reply_mode INTEGER NOT NULL DEFAULT 1,
    match_mode TEXT NOT NULL DEFAULT 'word',
    added_at   INTEGER NOT NULL DEFAULT 0,
    bombs      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS triggers (
    chat_id INTEGER NOT NULL,
    word    TEXT NOT NULL,
    PRIMARY KEY (chat_id, word)
);

CREATE TABLE IF NOT EXISTS pack_scope (
    chat_id  INTEGER NOT NULL,
    set_name TEXT NOT NULL,
    PRIMARY KEY (chat_id, set_name)
);

CREATE TABLE IF NOT EXISTS admin_ctx (
    user_id TEXT PRIMARY KEY,
    chat_id INTEGER NOT NULL
);

-- Фразы-предупреждения, которые бот кидает реплаем перед бомбингом
CREATE TABLE IF NOT EXISTS phrases (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    text    TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1
);
"""

DEFAULT_PHRASES = (
    "ну всё, сука, ты огребаешь",
    "обстрел активирован",
    "ёмаё чё щас будет",
    "так, кто-то тут напросился",
    "цель захвачена, наводим стволы",
    "ложись, сейчас накроет",
)


# (имя колонки, DDL) — применяется через ALTER TABLE, если колонки ещё нет
MIGRATIONS_CHATS: tuple[tuple[str, str], ...] = (
    ("chance", "chance INTEGER NOT NULL DEFAULT 100"),
    ("bombs", "bombs INTEGER NOT NULL DEFAULT 0"),
    ("delay", "delay INTEGER NOT NULL DEFAULT 3"),
    ("warn", "warn INTEGER NOT NULL DEFAULT 1"),
)


@dataclass
class Pack:
    set_name: str
    title: str
    kind: str
    enabled: bool
    sticker_count: int
    updated_at: int


@dataclass
class ChatConfig:
    chat_id: int
    title: str
    type: str
    enabled: bool
    nsfw: bool
    burst: int
    cooldown: int
    chance: int
    delay: int
    warn: bool
    reply_mode: bool
    match_mode: str
    added_at: int
    bombs: int


class Storage:
    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        self._db: aiosqlite.Connection | None = None
        # Версия конфига триггеров — растёт при любом изменении слов/режима.
        # Используется matcher'ом для инвалидации кэша скомпилированных regex.
        self.triggers_version: dict[int, int] = {}

    # ---------- lifecycle ----------

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._migrate()
        await self._db.commit()

    async def _migrate(self) -> None:
        """Добавляет колонки, появившиеся после первого релиза, в уже созданные БД."""
        async with self.db.execute("PRAGMA table_info(chats)") as cur:
            existing = {r["name"] for r in await cur.fetchall()}
        for column, ddl in MIGRATIONS_CHATS:
            if column not in existing:
                await self.db.execute(f"ALTER TABLE chats ADD COLUMN {ddl}")
        await self.db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Storage.connect() не вызван")
        return self._db

    def _bump(self, chat_id: int) -> None:
        self.triggers_version[chat_id] = self.triggers_version.get(chat_id, 0) + 1

    # ---------- packs ----------

    async def upsert_pack(
        self,
        set_name: str,
        title: str,
        kind: str,
        stickers: Sequence[tuple[str, str]],
    ) -> int:
        """Добавить/обновить пак вместе с кэшем стикеров. Возвращает кол-во стикеров."""
        kind = kind if kind in KINDS else KIND_DEFAULT
        now = int(time.time())
        await self.db.execute(
            """
            INSERT INTO packs (set_name, title, kind, enabled, sticker_count, updated_at)
            VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(set_name) DO UPDATE SET
                title = excluded.title,
                kind = excluded.kind,
                sticker_count = excluded.sticker_count,
                updated_at = excluded.updated_at
            """,
            (set_name, title, kind, len(stickers), now),
        )
        await self.db.execute("DELETE FROM stickers WHERE set_name = ?", (set_name,))
        await self.db.executemany(
            "INSERT OR REPLACE INTO stickers (file_id, set_name, emoji) VALUES (?, ?, ?)",
            [(fid, set_name, emoji or "") for fid, emoji in stickers],
        )
        await self.db.commit()
        return len(stickers)

    async def delete_pack(self, set_name: str) -> bool:
        cur = await self.db.execute("DELETE FROM packs WHERE set_name = ?", (set_name,))
        await self.db.execute("DELETE FROM stickers WHERE set_name = ?", (set_name,))
        await self.db.execute("DELETE FROM pack_scope WHERE set_name = ?", (set_name,))
        await self.db.commit()
        return cur.rowcount > 0

    async def set_pack_kind(self, set_name: str, kind: str) -> bool:
        if kind not in KINDS:
            return False
        cur = await self.db.execute(
            "UPDATE packs SET kind = ? WHERE set_name = ?", (kind, set_name)
        )
        await self.db.commit()
        return cur.rowcount > 0

    async def set_pack_enabled(self, set_name: str, enabled: bool) -> bool:
        cur = await self.db.execute(
            "UPDATE packs SET enabled = ? WHERE set_name = ?", (int(enabled), set_name)
        )
        await self.db.commit()
        return cur.rowcount > 0

    async def get_pack(self, set_name: str) -> Pack | None:
        async with self.db.execute(
            "SELECT * FROM packs WHERE set_name = ?", (set_name,)
        ) as cur:
            row = await cur.fetchone()
        return _row_to_pack(row) if row else None

    async def list_packs(self, kind: str | None = None) -> list[Pack]:
        sql = "SELECT * FROM packs"
        args: tuple = ()
        if kind:
            sql += " WHERE kind = ?"
            args = (kind,)
        sql += " ORDER BY kind, set_name"
        async with self.db.execute(sql, args) as cur:
            rows = await cur.fetchall()
        return [_row_to_pack(r) for r in rows]

    async def list_pack_names(self) -> list[str]:
        async with self.db.execute("SELECT set_name FROM packs ORDER BY set_name") as cur:
            return [r["set_name"] for r in await cur.fetchall()]

    # ---------- stickers ----------

    async def pick_stickers(
        self, kinds: Sequence[str], limit: int, only_sets: Sequence[str] | None = None
    ) -> list[str]:
        """Случайные file_id из включённых паков нужных категорий."""
        if not kinds or limit <= 0:
            return []
        kind_ph = ",".join("?" * len(kinds))
        sql = f"""
            SELECT s.file_id
            FROM stickers s
            JOIN packs p ON p.set_name = s.set_name
            WHERE p.enabled = 1 AND p.kind IN ({kind_ph})
        """
        args: list = list(kinds)
        if only_sets:
            set_ph = ",".join("?" * len(only_sets))
            sql += f" AND s.set_name IN ({set_ph})"
            args.extend(only_sets)
        sql += " ORDER BY RANDOM() LIMIT ?"
        args.append(limit)
        async with self.db.execute(sql, args) as cur:
            return [r["file_id"] for r in await cur.fetchall()]

    async def count_stickers(self, kinds: Sequence[str] | None = None) -> int:
        sql = """
            SELECT COUNT(*) AS n FROM stickers s
            JOIN packs p ON p.set_name = s.set_name
            WHERE p.enabled = 1
        """
        args: list = []
        if kinds:
            sql += f" AND p.kind IN ({','.join('?' * len(kinds))})"
            args.extend(kinds)
        async with self.db.execute(sql, args) as cur:
            row = await cur.fetchone()
        return int(row["n"]) if row else 0

    # ---------- chats ----------

    async def ensure_chat(
        self,
        chat_id: int,
        title: str = "",
        chat_type: str = "",
        *,
        default_burst: int = 5,
        default_cooldown: int = 5,
        default_chance: int = 100,
        default_delay: int = 3,
    ) -> ChatConfig:
        await self.db.execute(
            """
            INSERT INTO chats (chat_id, title, type, burst, cooldown, chance, delay, added_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                title = CASE WHEN excluded.title != '' THEN excluded.title ELSE chats.title END,
                type  = CASE WHEN excluded.type  != '' THEN excluded.type  ELSE chats.type  END
            """,
            (
                chat_id, title, chat_type, default_burst,
                default_cooldown, default_chance, default_delay, int(time.time()),
            ),
        )
        await self.db.commit()
        cfg = await self.get_chat(chat_id)
        assert cfg is not None
        return cfg

    async def get_chat(self, chat_id: int) -> ChatConfig | None:
        async with self.db.execute(
            "SELECT * FROM chats WHERE chat_id = ?", (chat_id,)
        ) as cur:
            row = await cur.fetchone()
        return _row_to_chat(row) if row else None

    async def list_chats(self) -> list[ChatConfig]:
        async with self.db.execute("SELECT * FROM chats ORDER BY added_at DESC") as cur:
            return [_row_to_chat(r) for r in await cur.fetchall()]

    async def update_chat(self, chat_id: int, **fields) -> None:
        allowed = {
            "title", "type", "enabled", "nsfw", "burst",
            "cooldown", "chance", "delay", "warn", "reply_mode", "match_mode",
        }
        sets, args = [], []
        for key, value in fields.items():
            if key not in allowed:
                continue
            sets.append(f"{key} = ?")
            args.append(int(value) if isinstance(value, bool) else value)
        if not sets:
            return
        args.append(chat_id)
        await self.db.execute(
            f"UPDATE chats SET {', '.join(sets)} WHERE chat_id = ?", args
        )
        await self.db.commit()
        if "match_mode" in fields:
            self._bump(chat_id)

    async def forget_chat(self, chat_id: int) -> None:
        await self.db.execute("DELETE FROM chats WHERE chat_id = ?", (chat_id,))
        await self.db.execute("DELETE FROM triggers WHERE chat_id = ?", (chat_id,))
        await self.db.execute("DELETE FROM pack_scope WHERE chat_id = ?", (chat_id,))
        await self.db.commit()
        self._bump(chat_id)

    async def bump_bombs(self, chat_id: int, n: int = 1) -> None:
        await self.db.execute(
            "UPDATE chats SET bombs = bombs + ? WHERE chat_id = ?", (n, chat_id)
        )
        await self.db.commit()

    # ---------- triggers ----------

    async def add_triggers(self, chat_id: int, words: Iterable[str]) -> list[str]:
        added: list[str] = []
        for word in words:
            word = word.strip().lower()
            if not word:
                continue
            cur = await self.db.execute(
                "INSERT OR IGNORE INTO triggers (chat_id, word) VALUES (?, ?)",
                (chat_id, word),
            )
            if cur.rowcount:
                added.append(word)
        await self.db.commit()
        if added:
            self._bump(chat_id)
        return added

    async def remove_triggers(self, chat_id: int, words: Iterable[str]) -> list[str]:
        removed: list[str] = []
        for word in words:
            word = word.strip().lower()
            cur = await self.db.execute(
                "DELETE FROM triggers WHERE chat_id = ? AND word = ?", (chat_id, word)
            )
            if cur.rowcount:
                removed.append(word)
        await self.db.commit()
        if removed:
            self._bump(chat_id)
        return removed

    async def clear_triggers(self, chat_id: int) -> int:
        cur = await self.db.execute("DELETE FROM triggers WHERE chat_id = ?", (chat_id,))
        await self.db.commit()
        self._bump(chat_id)
        return cur.rowcount

    async def list_triggers(self, chat_id: int) -> list[str]:
        async with self.db.execute(
            "SELECT word FROM triggers WHERE chat_id = ? ORDER BY word", (chat_id,)
        ) as cur:
            return [r["word"] for r in await cur.fetchall()]

    async def effective_triggers(self, chat_id: int) -> list[str]:
        """Слова чата + глобальные."""
        async with self.db.execute(
            "SELECT DISTINCT word FROM triggers WHERE chat_id IN (?, ?)",
            (chat_id, GLOBAL_CHAT_ID),
        ) as cur:
            return [r["word"] for r in await cur.fetchall()]

    def version(self, chat_id: int) -> int:
        """Версия конфига триггеров чата (учитывает глобальные)."""
        return self.triggers_version.get(chat_id, 0) + self.triggers_version.get(
            GLOBAL_CHAT_ID, 0
        )

    # ---------- pack scope ----------

    async def scope_packs(self, chat_id: int) -> list[str]:
        async with self.db.execute(
            "SELECT set_name FROM pack_scope WHERE chat_id = ? ORDER BY set_name",
            (chat_id,),
        ) as cur:
            return [r["set_name"] for r in await cur.fetchall()]

    async def scope_add(self, chat_id: int, set_name: str) -> bool:
        cur = await self.db.execute(
            "INSERT OR IGNORE INTO pack_scope (chat_id, set_name) VALUES (?, ?)",
            (chat_id, set_name),
        )
        await self.db.commit()
        return cur.rowcount > 0

    async def scope_remove(self, chat_id: int, set_name: str) -> bool:
        cur = await self.db.execute(
            "DELETE FROM pack_scope WHERE chat_id = ? AND set_name = ?",
            (chat_id, set_name),
        )
        await self.db.commit()
        return cur.rowcount > 0

    async def scope_clear(self, chat_id: int) -> int:
        cur = await self.db.execute(
            "DELETE FROM pack_scope WHERE chat_id = ?", (chat_id,)
        )
        await self.db.commit()
        return cur.rowcount

    # ---------- фразы-предупреждения ----------

    async def seed_phrases(self) -> int:
        """Наполняет список дефолтными фразами, если он пуст."""
        async with self.db.execute("SELECT COUNT(*) AS n FROM phrases") as cur:
            row = await cur.fetchone()
        if row and int(row["n"]) > 0:
            return 0
        await self.db.executemany(
            "INSERT OR IGNORE INTO phrases (text) VALUES (?)",
            [(p,) for p in DEFAULT_PHRASES],
        )
        await self.db.commit()
        return len(DEFAULT_PHRASES)

    async def add_phrase(self, text: str) -> int | None:
        text = " ".join((text or "").split())
        if not text:
            return None
        cur = await self.db.execute(
            "INSERT OR IGNORE INTO phrases (text) VALUES (?)", (text,)
        )
        await self.db.commit()
        return cur.lastrowid if cur.rowcount else None

    async def delete_phrase(self, phrase_id: int) -> bool:
        cur = await self.db.execute("DELETE FROM phrases WHERE id = ?", (phrase_id,))
        await self.db.commit()
        return cur.rowcount > 0

    async def list_phrases(self, only_enabled: bool = False) -> list[tuple[int, str, bool]]:
        sql = "SELECT id, text, enabled FROM phrases"
        if only_enabled:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY id"
        async with self.db.execute(sql) as cur:
            return [(int(r["id"]), r["text"], bool(r["enabled"])) for r in await cur.fetchall()]

    async def set_phrase_enabled(self, phrase_id: int, enabled: bool) -> bool:
        cur = await self.db.execute(
            "UPDATE phrases SET enabled = ? WHERE id = ?", (int(enabled), phrase_id)
        )
        await self.db.commit()
        return cur.rowcount > 0

    async def random_phrase(self) -> str | None:
        async with self.db.execute(
            "SELECT text FROM phrases WHERE enabled = 1 ORDER BY RANDOM() LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
        return row["text"] if row else None

    async def clear_phrases(self) -> int:
        cur = await self.db.execute("DELETE FROM phrases")
        await self.db.commit()
        return cur.rowcount

    # ---------- admin context (настройка канала из лички) ----------

    async def set_ctx(self, user_id: int, chat_id: int) -> None:
        await self.db.execute(
            "INSERT OR REPLACE INTO admin_ctx (user_id, chat_id) VALUES (?, ?)",
            (str(user_id), chat_id),
        )
        await self.db.commit()

    async def get_ctx(self, user_id: int) -> int | None:
        async with self.db.execute(
            "SELECT chat_id FROM admin_ctx WHERE user_id = ?", (str(user_id),)
        ) as cur:
            row = await cur.fetchone()
        return int(row["chat_id"]) if row else None

    async def clear_ctx(self, user_id: int) -> None:
        await self.db.execute("DELETE FROM admin_ctx WHERE user_id = ?", (str(user_id),))
        await self.db.commit()


def _row_to_pack(row: aiosqlite.Row) -> Pack:
    return Pack(
        set_name=row["set_name"],
        title=row["title"],
        kind=row["kind"],
        enabled=bool(row["enabled"]),
        sticker_count=int(row["sticker_count"]),
        updated_at=int(row["updated_at"]),
    )


def _row_to_chat(row: aiosqlite.Row) -> ChatConfig:
    return ChatConfig(
        chat_id=int(row["chat_id"]),
        title=row["title"],
        type=row["type"],
        enabled=bool(row["enabled"]),
        nsfw=bool(row["nsfw"]),
        burst=int(row["burst"]),
        cooldown=int(row["cooldown"]),
        chance=int(row["chance"]),
        delay=int(row["delay"]),
        warn=bool(row["warn"]),
        reply_mode=bool(row["reply_mode"]),
        match_mode=row["match_mode"],
        added_at=int(row["added_at"]),
        bombs=int(row["bombs"]),
    )
