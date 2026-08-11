"""Тесты слоя хранения."""
from __future__ import annotations

import pytest

from storage import (
    DEFAULT_PHRASES,
    GLOBAL_CHAT_ID,
    KIND_DEFAULT,
    KIND_NSFW,
    Storage,
    kinds_for,
)


class TestKindsFor:
    """NSFW переключает пул целиком, а не дополняет обычный."""

    def test_plain_mode_is_default_only(self):
        assert kinds_for(False) == [KIND_DEFAULT]

    def test_nsfw_mode_is_nsfw_only(self):
        assert kinds_for(True) == [KIND_NSFW]

    def test_default_never_leaks_into_nsfw(self):
        assert KIND_DEFAULT not in kinds_for(True)

    def test_nsfw_never_leaks_into_plain(self):
        assert KIND_NSFW not in kinds_for(False)


class TestPacks:
    async def test_add_and_read(self, storage, stickers_factory):
        n = await storage.upsert_pack("Pack1", "Первый", KIND_DEFAULT, stickers_factory("a", 5))
        assert n == 5

        pack = await storage.get_pack("Pack1")
        assert pack is not None
        assert pack.title == "Первый"
        assert pack.kind == KIND_DEFAULT
        assert pack.sticker_count == 5
        assert pack.enabled is True

    async def test_upsert_replaces_stickers(self, storage, stickers_factory):
        await storage.upsert_pack("Pack1", "t", KIND_DEFAULT, stickers_factory("old", 5))
        await storage.upsert_pack("Pack1", "t", KIND_DEFAULT, stickers_factory("new", 3))

        assert await storage.count_stickers() == 3
        picked = await storage.pick_stickers([KIND_DEFAULT], 10)
        assert all(fid.startswith("new_") for fid in picked)

    async def test_unknown_kind_falls_back_to_default(self, storage, stickers_factory):
        await storage.upsert_pack("Pack1", "t", "чепуха", stickers_factory("a", 2))
        pack = await storage.get_pack("Pack1")
        assert pack.kind == KIND_DEFAULT

    async def test_delete_cascades_stickers(self, storage, stickers_factory):
        await storage.upsert_pack("Pack1", "t", KIND_DEFAULT, stickers_factory("a", 4))
        assert await storage.delete_pack("Pack1") is True
        assert await storage.count_stickers() == 0
        assert await storage.get_pack("Pack1") is None

    async def test_delete_missing_returns_false(self, storage):
        assert await storage.delete_pack("Nope") is False

    async def test_change_kind(self, storage, stickers_factory):
        await storage.upsert_pack("P", "t", KIND_DEFAULT, stickers_factory("a", 2))
        assert await storage.set_pack_kind("P", KIND_NSFW) is True
        assert (await storage.get_pack("P")).kind == KIND_NSFW

    async def test_invalid_kind_rejected(self, storage, stickers_factory):
        await storage.upsert_pack("P", "t", KIND_DEFAULT, stickers_factory("a", 2))
        assert await storage.set_pack_kind("P", "мусор") is False

    async def test_disabled_pack_excluded_from_picks(self, storage, stickers_factory):
        await storage.upsert_pack("P", "t", KIND_DEFAULT, stickers_factory("a", 5))
        await storage.set_pack_enabled("P", False)
        assert await storage.pick_stickers([KIND_DEFAULT], 5) == []
        assert await storage.count_stickers() == 0


class TestStickerPicking:
    @pytest.fixture(autouse=True)
    async def _packs(self, storage, stickers_factory):
        await storage.upsert_pack("Safe", "t", KIND_DEFAULT, stickers_factory("safe", 10))
        await storage.upsert_pack("Spicy", "t", KIND_NSFW, stickers_factory("nsfw", 10))

    async def test_default_only(self, storage):
        picked = await storage.pick_stickers([KIND_DEFAULT], 20)
        assert len(picked) == 10
        assert all(fid.startswith("safe_") for fid in picked)

    async def test_nsfw_included_when_requested(self, storage):
        picked = await storage.pick_stickers([KIND_DEFAULT, KIND_NSFW], 20)
        assert len(picked) == 20

    async def test_nsfw_mode_gives_only_nsfw(self, storage):
        picked = await storage.pick_stickers(kinds_for(True), 20)
        assert len(picked) == 10
        assert all(fid.startswith("nsfw_") for fid in picked)

    async def test_plain_mode_gives_only_default(self, storage):
        picked = await storage.pick_stickers(kinds_for(False), 20)
        assert len(picked) == 10
        assert all(fid.startswith("safe_") for fid in picked)

    async def test_nsfw_mode_is_silent_without_nsfw_packs(self, storage):
        await storage.delete_pack("Spicy")
        assert await storage.pick_stickers(kinds_for(True), 10) == []

    async def test_respects_limit(self, storage):
        assert len(await storage.pick_stickers([KIND_DEFAULT, KIND_NSFW], 7)) == 7

    async def test_zero_limit(self, storage):
        assert await storage.pick_stickers([KIND_DEFAULT], 0) == []

    async def test_no_kinds(self, storage):
        assert await storage.pick_stickers([], 5) == []

    async def test_scope_narrows_to_named_packs(self, storage):
        picked = await storage.pick_stickers([KIND_DEFAULT, KIND_NSFW], 20, ["Spicy"])
        assert len(picked) == 10
        assert all(fid.startswith("nsfw_") for fid in picked)

    async def test_picks_are_unique(self, storage):
        picked = await storage.pick_stickers([KIND_DEFAULT], 10)
        assert len(set(picked)) == len(picked)


class TestTriggers:
    async def test_add_normalizes_case_and_spaces(self, storage):
        added = await storage.add_triggers(-100, ["  КоТ  "])
        assert added == ["кот"]
        assert await storage.list_triggers(-100) == ["кот"]

    async def test_duplicates_ignored(self, storage):
        await storage.add_triggers(-100, ["кот"])
        assert await storage.add_triggers(-100, ["кот", "КОТ"]) == []

    async def test_blank_words_skipped(self, storage):
        assert await storage.add_triggers(-100, ["", "   "]) == []

    async def test_remove(self, storage):
        await storage.add_triggers(-100, ["кот", "пёс"])
        assert await storage.remove_triggers(-100, ["КОТ"]) == ["кот"]
        assert await storage.list_triggers(-100) == ["пёс"]

    async def test_remove_missing(self, storage):
        assert await storage.remove_triggers(-100, ["нет"]) == []

    async def test_clear(self, storage):
        await storage.add_triggers(-100, ["a", "b", "c"])
        assert await storage.clear_triggers(-100) == 3
        assert await storage.list_triggers(-100) == []

    async def test_effective_merges_global(self, storage):
        await storage.add_triggers(-100, ["кот"])
        await storage.add_triggers(GLOBAL_CHAT_ID, ["пёс"])
        assert sorted(await storage.effective_triggers(-100)) == ["кот", "пёс"]

    async def test_effective_dedupes(self, storage):
        await storage.add_triggers(-100, ["кот"])
        await storage.add_triggers(GLOBAL_CHAT_ID, ["кот"])
        assert await storage.effective_triggers(-100) == ["кот"]

    async def test_chats_isolated(self, storage):
        await storage.add_triggers(-100, ["кот"])
        assert await storage.list_triggers(-200) == []

    async def test_version_grows_on_change(self, storage):
        before = storage.version(-100)
        await storage.add_triggers(-100, ["кот"])
        assert storage.version(-100) > before

    async def test_version_stable_without_change(self, storage):
        await storage.add_triggers(-100, ["кот"])
        v = storage.version(-100)
        await storage.add_triggers(-100, ["кот"])  # дубль, ничего не поменялось
        assert storage.version(-100) == v

    async def test_global_change_affects_chat_version(self, storage):
        before = storage.version(-100)
        await storage.add_triggers(GLOBAL_CHAT_ID, ["ключ"])
        assert storage.version(-100) > before


class TestChats:
    async def test_ensure_creates_with_defaults(self, storage):
        cfg = await storage.ensure_chat(
            -100, "Чат", "supergroup",
            default_burst=7, default_cooldown=12, default_chance=50, default_delay=4,
        )
        assert cfg.chat_id == -100
        assert cfg.burst == 7
        assert cfg.cooldown == 12
        assert cfg.chance == 50
        assert cfg.delay == 4
        assert cfg.enabled is True
        assert cfg.nsfw is False
        assert cfg.warn is True
        assert cfg.sed is True

    async def test_ensure_is_idempotent(self, storage):
        await storage.ensure_chat(-100, "Чат", "supergroup", default_burst=7)
        await storage.update_chat(-100, burst=99)
        # Повторный ensure не должен затирать пользовательские настройки
        cfg = await storage.ensure_chat(-100, "Чат", "supergroup", default_burst=7)
        assert cfg.burst == 99

    async def test_ensure_updates_title(self, storage):
        await storage.ensure_chat(-100, "Старое", "supergroup")
        cfg = await storage.ensure_chat(-100, "Новое", "supergroup")
        assert cfg.title == "Новое"

    async def test_ensure_keeps_title_when_empty(self, storage):
        await storage.ensure_chat(-100, "Название", "supergroup")
        cfg = await storage.ensure_chat(-100, "", "")
        assert cfg.title == "Название"
        assert cfg.type == "supergroup"

    async def test_update_ignores_unknown_fields(self, storage):
        await storage.ensure_chat(-100)
        await storage.update_chat(-100, burst=3, evil_column="drop table")
        assert (await storage.get_chat(-100)).burst == 3

    async def test_update_casts_bools(self, storage):
        await storage.ensure_chat(-100)
        await storage.update_chat(-100, nsfw=True, enabled=False, warn=False, sed=False)
        cfg = await storage.get_chat(-100)
        assert cfg.nsfw is True
        assert cfg.enabled is False
        assert cfg.warn is False
        assert cfg.sed is False

    async def test_update_noop_without_fields(self, storage):
        await storage.ensure_chat(-100)
        await storage.update_chat(-100)  # не должно падать
        assert await storage.get_chat(-100) is not None

    async def test_forget_removes_everything(self, storage):
        await storage.ensure_chat(-100)
        await storage.add_triggers(-100, ["кот"])
        await storage.scope_add(-100, "Pack")
        await storage.forget_chat(-100)

        assert await storage.get_chat(-100) is None
        assert await storage.list_triggers(-100) == []
        assert await storage.scope_packs(-100) == []

    async def test_bump_bombs(self, storage):
        await storage.ensure_chat(-100)
        await storage.bump_bombs(-100, 5)
        await storage.bump_bombs(-100, 3)
        assert (await storage.get_chat(-100)).bombs == 8

    async def test_missing_chat_is_none(self, storage):
        assert await storage.get_chat(-999) is None


class TestScope:
    async def test_add_and_list(self, storage):
        assert await storage.scope_add(-100, "P1") is True
        assert await storage.scope_add(-100, "P1") is False  # дубль
        assert await storage.scope_packs(-100) == ["P1"]

    async def test_remove(self, storage):
        await storage.scope_add(-100, "P1")
        assert await storage.scope_remove(-100, "P1") is True
        assert await storage.scope_packs(-100) == []

    async def test_clear(self, storage):
        await storage.scope_add(-100, "P1")
        await storage.scope_add(-100, "P2")
        assert await storage.scope_clear(-100) == 2

    async def test_deleting_pack_clears_it_from_scopes(self, storage, stickers_factory):
        await storage.upsert_pack("P1", "t", KIND_DEFAULT, stickers_factory("a", 2))
        await storage.scope_add(-100, "P1")
        await storage.delete_pack("P1")
        assert await storage.scope_packs(-100) == []


class TestPhrases:
    async def test_seed_fills_defaults(self, storage):
        assert await storage.seed_phrases() == len(DEFAULT_PHRASES)
        phrases = [text for _, text, _ in await storage.list_phrases()]
        assert "обстрел активирован" in phrases

    async def test_seed_is_noop_when_not_empty(self, storage):
        await storage.add_phrase("своя фраза")
        assert await storage.seed_phrases() == 0

    async def test_add_dedupes_and_trims(self, storage):
        first = await storage.add_phrase("  ну   всё, ты попал  ")
        assert first is not None
        assert await storage.add_phrase("ну всё, ты попал") is None

    async def test_add_rejects_blank(self, storage):
        assert await storage.add_phrase("   ") is None

    async def test_delete(self, storage):
        pid = await storage.add_phrase("фраза")
        assert await storage.delete_phrase(pid) is True
        assert await storage.delete_phrase(pid) is False

    async def test_random_returns_only_enabled(self, storage):
        pid = await storage.add_phrase("выключенная")
        await storage.add_phrase("рабочая")
        await storage.set_phrase_enabled(pid, False)
        for _ in range(10):
            assert await storage.random_phrase() == "рабочая"

    async def test_random_on_empty_list(self, storage):
        assert await storage.random_phrase() is None

    async def test_list_only_enabled_filter(self, storage):
        pid = await storage.add_phrase("off")
        await storage.add_phrase("on")
        await storage.set_phrase_enabled(pid, False)
        assert len(await storage.list_phrases()) == 2
        assert len(await storage.list_phrases(only_enabled=True)) == 1


class TestAdminContext:
    async def test_set_get_clear(self, storage):
        await storage.set_ctx(111, -100)
        assert await storage.get_ctx(111) == -100
        await storage.clear_ctx(111)
        assert await storage.get_ctx(111) is None

    async def test_overwrite(self, storage):
        await storage.set_ctx(111, -100)
        await storage.set_ctx(111, -200)
        assert await storage.get_ctx(111) == -200

    async def test_unknown_user(self, storage):
        assert await storage.get_ctx(999) is None


class TestMigrations:
    async def test_old_db_gets_new_columns(self, tmp_path):
        """БД, созданная до появления chance/delay/warn, должна доехать через ALTER TABLE."""
        import aiosqlite

        path = tmp_path / "legacy.sqlite3"
        async with aiosqlite.connect(path) as db:
            await db.execute(
                """
                CREATE TABLE chats (
                    chat_id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    type TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    nsfw INTEGER NOT NULL DEFAULT 0,
                    burst INTEGER NOT NULL DEFAULT 5,
                    cooldown INTEGER NOT NULL DEFAULT 5,
                    reply_mode INTEGER NOT NULL DEFAULT 1,
                    match_mode TEXT NOT NULL DEFAULT 'word',
                    added_at INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            await db.execute("INSERT INTO chats (chat_id, title) VALUES (-100, 'Старый')")
            await db.commit()

        st = Storage(path)
        await st.connect()
        try:
            cfg = await st.get_chat(-100)
            assert cfg is not None
            assert cfg.title == "Старый"
            assert cfg.chance == 100
            assert cfg.delay == 3
            assert cfg.warn is True
            assert cfg.sed is True
            assert cfg.bombs == 0
        finally:
            await st.close()
