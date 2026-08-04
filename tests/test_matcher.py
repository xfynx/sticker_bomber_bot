"""Тесты матчинга триггерных слов."""
from __future__ import annotations

import pytest

from matcher import TriggerMatcher, build_pattern, match_words, normalize
from storage import GLOBAL_CHAT_ID, MATCH_SUBSTRING, MATCH_WORD


class TestNormalize:
    def test_lowercases(self):
        assert normalize("ПрИвЕт") == "привет"

    def test_strips_zero_width(self):
        assert normalize("к​о‍т") == "кот"

    def test_strips_soft_hyphen_and_bom(self):
        assert normalize("к­о﻿т") == "кот"

    def test_zero_width_does_not_break_trigger(self):
        assert match_words("к​от", ["кот"]) == "кот"

    def test_handles_none_like(self):
        assert normalize("") == ""

    def test_homoglyphs_off_by_default(self):
        assert normalize("кot") == "кot"

    def test_homoglyphs_folded_on_demand(self):
        assert normalize("кot", fold_homoglyphs=True) == "кот"


class TestBuildPattern:
    def test_empty_words_gives_none(self):
        assert build_pattern([]) is None
        assert build_pattern(["", "   "]) is None

    def test_escapes_regex_metachars(self):
        # Слово со спецсимволами не должно ломать компиляцию
        pattern = build_pattern(["c++", "(тест)", "a|b"])
        assert pattern is not None
        assert pattern.search("я пишу на c++") is not None

    def test_longest_alternative_wins(self):
        # «доброе утро» должно матчиться целиком, а не по куску «утро»
        hit = match_words("всем доброе утро", ["утро", "доброе утро"])
        assert hit == "доброе утро"


class TestWordMode:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("кот", "кот"),
            ("Кот!", "кот"),
            ("а вот и кот, привет", "кот"),
            ("(кот)", "кот"),
            ("кот-обормот", "кот"),
        ],
    )
    def test_matches(self, text, expected):
        assert match_words(text, ["кот"], MATCH_WORD) == expected

    @pytest.mark.parametrize("text", ["котёл", "котлета", "мкоть", "коты"])
    def test_no_partial_match(self, text):
        assert match_words(text, ["кот"], MATCH_WORD) is None

    def test_case_insensitive_latin(self):
        assert match_words("HELLO there", ["hello"], MATCH_WORD) == "hello"

    def test_digits_are_word_chars(self):
        assert match_words("test123", ["test"], MATCH_WORD) is None

    def test_underscore_is_boundary(self):
        # _ исключён из класса «символ слова», значит граница проходит по нему
        assert match_words("foo_bar", ["foo"], MATCH_WORD) == "foo"

    def test_multiword_phrase(self):
        assert match_words("ну и что тут у нас", ["что тут"], MATCH_WORD) == "что тут"


class TestSubstringMode:
    def test_matches_inside_word(self):
        assert match_words("котёл", ["кот"], MATCH_SUBSTRING) == "кот"

    def test_still_case_insensitive(self):
        assert match_words("КОТЁЛ", ["кот"], MATCH_SUBSTRING) == "кот"


class TestTriggerMatcher:
    async def test_matches_chat_words(self, storage):
        await storage.add_triggers(-100, ["кот"])
        matcher = TriggerMatcher(storage)
        assert await matcher.match(-100, "смотри какой кот") == "кот"

    async def test_global_words_apply_everywhere(self, storage):
        await storage.add_triggers(GLOBAL_CHAT_ID, ["пёс"])
        matcher = TriggerMatcher(storage)
        assert await matcher.match(-100, "вон пёс бежит") == "пёс"
        assert await matcher.match(-200, "и тут пёс") == "пёс"

    async def test_cache_invalidated_on_add(self, storage):
        matcher = TriggerMatcher(storage)
        assert await matcher.match(-100, "кот") is None

        await storage.add_triggers(-100, ["кот"])
        # Версия выросла — кэш должен перестроиться сам, без invalidate()
        assert await matcher.match(-100, "кот") == "кот"

    async def test_cache_invalidated_on_remove(self, storage):
        await storage.add_triggers(-100, ["кот"])
        matcher = TriggerMatcher(storage)
        assert await matcher.match(-100, "кот") == "кот"

        await storage.remove_triggers(-100, ["кот"])
        assert await matcher.match(-100, "кот") is None

    async def test_global_change_invalidates_chat_cache(self, storage):
        matcher = TriggerMatcher(storage)
        await matcher.match(-100, "прогрев кэша")

        await storage.add_triggers(GLOBAL_CHAT_ID, ["ключ"])
        assert await matcher.match(-100, "вот ключ") == "ключ"

    async def test_words_are_isolated_between_chats(self, storage):
        await storage.add_triggers(-100, ["кот"])
        matcher = TriggerMatcher(storage)
        assert await matcher.match(-200, "кот") is None

    async def test_empty_text_returns_none(self, storage):
        await storage.add_triggers(-100, ["кот"])
        matcher = TriggerMatcher(storage)
        assert await matcher.match(-100, "") is None

    async def test_manual_invalidate(self, storage):
        await storage.add_triggers(-100, ["кот"])
        matcher = TriggerMatcher(storage)
        await matcher.match(-100, "кот")
        matcher.invalidate(-100)
        assert await matcher.match(-100, "кот") == "кот"

    async def test_match_all_finds_every_hit(self, storage):
        await storage.add_triggers(-100, ["кот", "пёс"])
        matcher = TriggerMatcher(storage)
        hits = await matcher.match_all(-100, "кот и пёс и снова кот")
        assert sorted(hits) == ["кот", "кот", "пёс"]
