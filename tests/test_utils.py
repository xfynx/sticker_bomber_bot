"""Тесты парсинга аргументов и форматирования."""
from __future__ import annotations

import pytest
from aiogram.enums import ChatType

from utils import (
    ADMIN_STATUSES,
    GROUP_TYPES,
    enum_value,
    esc,
    fmt_words,
    parse_bool,
    parse_int_range,
    parse_set_name,
    split_words,
)


class TestEnumValue:
    def test_unwraps_enum(self):
        assert enum_value(ChatType.SUPERGROUP) == "supergroup"

    def test_passes_plain_string(self):
        assert enum_value("supergroup") == "supergroup"

    def test_none_becomes_empty(self):
        assert enum_value(None) == ""

    def test_str_enum_is_not_stringified_naively(self):
        """str(ChatType.GROUP) даёт 'ChatType.GROUP' — ловим регрессию."""
        assert enum_value(ChatType.GROUP) == "group"


class TestStatusSets:
    """Регрессия: str-Enum хэшируется по имени, поэтому в множествах только .value."""

    def test_group_types_are_plain_values(self):
        assert GROUP_TYPES == {"group", "supergroup"}

    def test_telegram_string_matches_group_types(self):
        assert "supergroup" in GROUP_TYPES

    def test_admin_statuses_are_plain_values(self):
        assert ADMIN_STATUSES == {"creator", "administrator"}

    def test_telegram_string_matches_admin_statuses(self):
        assert "administrator" in ADMIN_STATUSES

    def test_every_entry_is_a_plain_str(self):
        for value in GROUP_TYPES | ADMIN_STATUSES:
            assert type(value) is str, f"{value!r} должен быть голой строкой, а не enum"


class TestParseSetName:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("HotCherry", "HotCherry"),
            ("@HotCherry", "HotCherry"),
            ("  HotCherry  ", "HotCherry"),
            ("https://t.me/addstickers/HotCherry", "HotCherry"),
            ("http://t.me/addstickers/HotCherry", "HotCherry"),
            ("t.me/addstickers/HotCherry", "HotCherry"),
            ("telegram.me/addstickers/HotCherry", "HotCherry"),
            ("https://t.me/addemoji/MyEmoji", "MyEmoji"),
            ("pack_with_underscores_1", "pack_with_underscores_1"),
        ],
    )
    def test_valid(self, raw, expected):
        assert parse_set_name(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", "bad name", "имя-по-русски", "a" * 65, "a-b"])
    def test_invalid(self, raw):
        assert parse_set_name(raw) is None


class TestSplitWords:
    def test_comma_separated(self):
        assert split_words("кот, пёс, ёж") == ["кот", "пёс", "ёж"]

    def test_newline_separated(self):
        assert split_words("кот\nпёс") == ["кот", "пёс"]

    def test_semicolon_separated(self):
        assert split_words("кот; пёс") == ["кот", "пёс"]

    def test_keeps_multiword_phrases(self):
        assert split_words("доброе утро, добрый вечер") == ["доброе утро", "добрый вечер"]

    def test_collapses_inner_spaces(self):
        assert split_words("доброе    утро") == ["доброе утро"]

    def test_lowercases(self):
        assert split_words("КоТ") == ["кот"]

    def test_dedupes_preserving_order(self):
        assert split_words("кот, пёс, КОТ") == ["кот", "пёс"]

    def test_drops_empty_chunks(self):
        assert split_words("кот,,  ,пёс") == ["кот", "пёс"]

    def test_empty_input(self):
        assert split_words("") == []


class TestParseBool:
    @pytest.mark.parametrize("raw", ["on", "ON", "вкл", "1", "true", "yes", "да", " y "])
    def test_true(self, raw):
        assert parse_bool(raw) is True

    @pytest.mark.parametrize("raw", ["off", "выкл", "0", "false", "no", "нет", "n"])
    def test_false(self, raw):
        assert parse_bool(raw) is False

    @pytest.mark.parametrize("raw", [None, "", "может быть", "2"])
    def test_undecidable(self, raw):
        assert parse_bool(raw) is None


class TestParseIntRange:
    def test_in_range(self):
        assert parse_int_range("50", 1, 100) == 50

    def test_bounds_inclusive(self):
        assert parse_int_range("1", 1, 100) == 1
        assert parse_int_range("100", 1, 100) == 100

    def test_out_of_range(self):
        assert parse_int_range("0", 1, 100) is None
        assert parse_int_range("101", 1, 100) is None

    def test_strips_percent_sign(self):
        assert parse_int_range("25%", 1, 100) == 25

    def test_whitespace(self):
        assert parse_int_range("  7  ", 1, 100) == 7

    @pytest.mark.parametrize("raw", [None, "", "много", "3.5"])
    def test_garbage(self, raw):
        assert parse_int_range(raw, 1, 100) is None

    def test_zero_allowed_when_in_range(self):
        assert parse_int_range("0", 0, 60) == 0

    def test_negative_rejected_when_out_of_range(self):
        assert parse_int_range("-5", 0, 60) is None


class TestFormatting:
    def test_esc_escapes_html(self):
        assert esc("<b>x</b>") == "&lt;b&gt;x&lt;/b&gt;"

    def test_fmt_words_empty(self):
        assert fmt_words([]) == "<i>пусто</i>"

    def test_fmt_words_wraps_in_code(self):
        assert fmt_words(["кот"]) == "<code>кот</code>"

    def test_fmt_words_truncates(self):
        out = fmt_words([f"w{i}" for i in range(10)], limit=3)
        assert "и ещё 7" in out

    def test_fmt_words_escapes(self):
        assert "&lt;" in fmt_words(["<script>"])
