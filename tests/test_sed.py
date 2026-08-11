"""Тесты замены по регулярке."""
from __future__ import annotations

import pytest

import sed
from sed import SedCommand, SedError, apply, looks_like_sed, parse


async def run(text: str, command: str) -> str:
    """Хелпер: разобрать вызов и применить его к тексту."""
    cmd = parse(command)
    assert cmd is not None, f"{command!r} не распознан как замена"
    return await apply(cmd, text)


class TestUserExamples:
    """Примеры из постановки задачи — проверяем дословно."""

    async def test_mashina_to_mina(self):
        result = await run(
            "летал и щебетал увидел красивую машину", "s/маш/м"
        )
        assert result == "летал и щебетал увидел красивую мину"

    async def test_mashina_via_cyrillic_prefix(self):
        result = await run(
            "летал и щебетал увидел красивую машину", "ы/маш/м"
        )
        assert result == "летал и щебетал увидел красивую мину"

    async def test_babushki_replaces_all_occurrences(self):
        result = await run("василий бежал к бабам и бабинам", "s/аб/абушк")
        assert result == "василий бежал к бабушкам и бабушкинам"

    async def test_babushki_via_cyrillic_prefix(self):
        result = await run("василий бежал к бабам и бабинам", "ы/аб/абушк")
        assert result == "василий бежал к бабушкам и бабушкинам"

    async def test_greedy_group_pattern(self):
        result = await run("летающий корабль", "s/к.*ь/пень")
        assert result == "летающий пень"


class TestLooksLikeSed:
    @pytest.mark.parametrize(
        "text",
        ["s/a/b", "ы/а/б", "S/a/b", "Ы/а/б", "s|a|b", "ы#а#б", "s/a/", "s/a/b/g"],
    )
    def test_recognised(self, text):
        assert looks_like_sed(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "s",
            "s/",
            "s/a",              # нет второго разделителя
            "просто текст",
            "sabc",             # буква вместо разделителя
            "s1/a/b",           # цифра вместо разделителя
            "s a b",            # пробел не разделитель
            "с/а/б",            # кириллическая 'с', не наш префикс
            "/start",
        ],
    )
    def test_rejected(self, text):
        assert looks_like_sed(text) is False

    def test_leading_whitespace_ok(self):
        assert looks_like_sed("  s/a/b  ") is True


class TestParse:
    def test_basic(self):
        cmd = parse("s/кот/пёс")
        assert cmd == SedCommand(
            pattern="кот", replacement="пёс", delimiter="/", count=0, ignore_case=False
        )

    def test_trailing_delimiter_gives_empty_flags(self):
        cmd = parse("s/кот/пёс/")
        assert cmd.count == 0
        assert cmd.ignore_case is False

    def test_empty_replacement_is_deletion(self):
        cmd = parse("s/кот/")
        assert cmd.replacement == ""

    def test_alternative_delimiters(self):
        for text, delim in (("s|a|b", "|"), ("s#a#b", "#"), ("s,a,b", ","), ("s.a.b", ".")):
            cmd = parse(text)
            assert cmd.delimiter == delim
            assert cmd.pattern == "a"
            assert cmd.replacement == "b"

    def test_not_a_command_returns_none(self):
        assert parse("привет как дела") is None
        assert parse("s/a") is None
        assert parse("") is None

    def test_uppercase_prefix(self):
        assert parse("S/a/b") is not None
        assert parse("Ы/а/б") is not None

    def test_flag_i(self):
        assert parse("s/a/b/i").ignore_case is True

    def test_flag_1_limits_to_first(self):
        assert parse("s/a/b/1").count == 1

    def test_flag_g_is_accepted_as_noop(self):
        assert parse("s/a/b/g").count == 0

    def test_flags_combined(self):
        cmd = parse("s/a/b/i1")
        assert cmd.ignore_case is True
        assert cmd.count == 1

    def test_unknown_flag_raises(self):
        with pytest.raises(SedError, match="флаг"):
            parse("s/a/b/x")

    def test_empty_pattern_raises(self):
        with pytest.raises(SedError, match="Пустой шаблон"):
            parse("s//b")

    def test_too_many_delimiters_raises(self):
        with pytest.raises(SedError, match="Слишком много"):
            parse("s/a/b/c/d")

    def test_escaped_delimiter_is_not_a_split(self):
        cmd = parse(r"s/a\/b/c")
        assert cmd.pattern == r"a\/b"
        assert cmd.replacement == "c"

    def test_long_pattern_raises(self):
        with pytest.raises(SedError, match="Шаблон длиннее"):
            parse("s/" + "a" * (sed.MAX_PATTERN_LEN + 1) + "/b")

    def test_long_replacement_raises(self):
        with pytest.raises(SedError, match="Замена длиннее"):
            parse("s/a/" + "b" * (sed.MAX_REPLACEMENT_LEN + 1))


class TestApply:
    async def test_replaces_all_by_default(self):
        assert await run("а-а-а", "s/а/о") == "о-о-о"

    async def test_flag_1_replaces_only_first(self):
        assert await run("а-а-а", "s/а/о/1") == "о-а-а"

    async def test_ignore_case(self):
        assert await run("Кот и КОТ", "s/кот/пёс/i") == "пёс и пёс"

    async def test_case_sensitive_by_default(self):
        assert await run("Кот и кот", "s/кот/пёс") == "Кот и пёс"

    async def test_deletion(self):
        assert await run("убери лишнее слово", "s/ лишнее//") == "убери слово"

    async def test_regex_quantifiers(self):
        assert await run("ааа ббб", "s/а+/х") == "х ббб"

    async def test_anchors(self):
        assert await run("кот сидит", "s/^кот/пёс") == "пёс сидит"

    async def test_character_class(self):
        assert await run("дом1 дом2", r"s/\d/N") == "домN домN"

    async def test_numbered_group_backreference(self):
        result = await run("привет мир", r"s/(\w+) (\w+)/\2 \1")
        assert result == "мир привет"

    async def test_named_group_backreference(self):
        result = await run("кот", r"s/(?<animal>кот)/большой \g<animal>")
        assert result == "большой кот"

    async def test_escaped_delimiter_matches_literal(self):
        assert await run("a/b", r"s/a\/b/x") == "x"

    async def test_alternative_delimiter_keeps_slashes_usable(self):
        assert await run("путь/к/файлу", "s|/|-") == "путь-к-файлу"

    async def test_unicode_word_boundaries(self):
        assert await run("кот котёл", r"s/\bкот\b/пёс") == "пёс котёл"


class TestApplyErrors:
    async def test_no_match_raises(self):
        cmd = parse("s/зебра/лошадь")
        with pytest.raises(SedError, match="Ничего не нашлось"):
            await apply(cmd, "кот и пёс")

    async def test_empty_source_raises(self):
        cmd = parse("s/a/b")
        with pytest.raises(SedError, match="нет текста"):
            await apply(cmd, "")

    async def test_whitespace_only_source_raises(self):
        cmd = parse("s/a/b")
        with pytest.raises(SedError, match="нет текста"):
            await apply(cmd, "   \n  ")

    async def test_too_long_source_raises(self):
        cmd = parse("s/a/b")
        with pytest.raises(SedError, match="длиннее"):
            await apply(cmd, "a" * (sed.MAX_TEXT_LEN + 1))

    async def test_result_cannot_be_empty(self):
        cmd = parse("s/кот//")
        with pytest.raises(SedError, match="не осталось текста"):
            await apply(cmd, "кот")

    async def test_broken_regex_raises_friendly_error(self):
        cmd = parse("s/(незакрытая/b")
        with pytest.raises(SedError, match="Не понял регулярку"):
            await apply(cmd, "текст (незакрытая скобка")

    async def test_missing_group_reference_raises(self):
        cmd = parse(r"s/кот/\9")
        with pytest.raises(SedError):
            await apply(cmd, "кот")

    async def test_result_is_truncated(self, monkeypatch):
        monkeypatch.setattr(sed, "MAX_RESULT_LEN", 20)
        cmd = parse("s/x/" + "y" * 30)
        result = await apply(cmd, "x")
        assert len(result) <= 22          # 20 + " …"
        assert result.endswith("…")


class TestSafety:
    async def test_catastrophic_backtracking_times_out(self, monkeypatch):
        """Классическая ReDoS-бомба должна упереться в таймаут, а не повесить бота."""
        monkeypatch.setattr(sed, "TIMEOUT", 0.05)
        cmd = parse("s/(a+)+$/x")
        payload = "a" * 60 + "b"
        with pytest.raises(SedError, match="тяжёлый"):
            await apply(cmd, payload)

    async def test_normal_pattern_is_fast(self):
        # Здоровый шаблон на длинном тексте укладывается в дефолтный таймаут
        text = "слово " * 500
        assert await run(text, "s/слово/буква/1").startswith("буква ")

    async def test_empty_match_pattern_does_not_explode(self):
        """s/x*/-/ матчит пустую строку между символами — результат должен быть обрезан."""
        cmd = parse("s/z*/-")
        result = await apply(cmd, "абв")
        assert len(result) <= sed.MAX_RESULT_LEN

    def test_pattern_length_is_capped(self):
        assert sed.MAX_PATTERN_LEN <= 500

    def test_timeout_is_set(self):
        assert 0 < sed.TIMEOUT <= 5
