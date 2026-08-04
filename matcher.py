"""Компиляция и кэширование regex-матчеров триггерных слов."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from storage import MATCH_SUBSTRING, MATCH_WORD, Storage

# Границы «слова» с поддержкой кириллицы: \b в Python по умолчанию юникодный,
# но мы строим свои lookaround'ы, чтобы триггер «кот» не срабатывал на «котёл»,
# при этом «кот!» и «кот,» — срабатывали.
_WORD_CHAR = r"[^\W_]"

# Zero-width и управляющие символы, которыми обычно пытаются разорвать слово.
_INVISIBLE = re.compile(
    "["
    "­"              # soft hyphen
    "​-‏"       # ZWSP, ZWNJ, ZWJ, LRM, RLM
    "‪-‮"       # bidi overrides
    "⁠"              # word joiner
    "﻿"              # BOM
    "]"
)

# Латиница, визуально неотличимая от кириллицы. Применяется после casefold,
# поэтому в таблице только строчные буквы.
_HOMOGLYPHS = str.maketrans({
    "a": "а", "e": "е", "o": "о", "p": "р", "c": "с",
    "y": "у", "x": "х", "k": "к", "m": "м", "t": "т",
})


def normalize(text: str, *, fold_homoglyphs: bool = False) -> str:
    """Нижний регистр + вычистка zero-width мусора.

    fold_homoglyphs склеивает латиницу с кириллицей ('кot' → 'кот'), но может
    давать ложные срабатывания на англоязычных словах — поэтому по умолчанию выключен.
    """
    text = _INVISIBLE.sub("", text or "")
    text = text.casefold()
    if fold_homoglyphs:
        text = text.translate(_HOMOGLYPHS)
    return text


def build_pattern(words: Iterable[str], mode: str = MATCH_WORD) -> re.Pattern[str] | None:
    """Один альтернативный regex на все слова чата.

    mode='word'      — совпадение только по границам слова
    mode='substring' — совпадение в любом месте (в т.ч. внутри слова)
    """
    cleaned = sorted(
        {normalize(w) for w in words if w and w.strip()},
        key=len,
        reverse=True,
    )
    if not cleaned:
        return None

    body = "|".join(re.escape(w) for w in cleaned)
    if mode == MATCH_SUBSTRING:
        pattern = body
    else:
        pattern = rf"(?<!{_WORD_CHAR})(?:{body})(?!{_WORD_CHAR})"
    return re.compile(pattern, re.IGNORECASE | re.UNICODE)


@dataclass
class _Entry:
    version: int
    pattern: re.Pattern[str] | None
    mode: str


class TriggerMatcher:
    """Кэш скомпилированных паттернов по чатам. Инвалидация — по версии из Storage."""

    def __init__(self, storage: Storage) -> None:
        self._storage = storage
        self._cache: dict[int, _Entry] = {}

    def invalidate(self, chat_id: int | None = None) -> None:
        if chat_id is None:
            self._cache.clear()
        else:
            self._cache.pop(chat_id, None)

    async def pattern_for(self, chat_id: int, mode: str = MATCH_WORD) -> re.Pattern[str] | None:
        version = self._storage.version(chat_id)
        entry = self._cache.get(chat_id)
        if entry is not None and entry.version == version and entry.mode == mode:
            return entry.pattern

        words = await self._storage.effective_triggers(chat_id)
        pattern = build_pattern(words, mode)
        self._cache[chat_id] = _Entry(version=version, pattern=pattern, mode=mode)
        return pattern

    async def match(self, chat_id: int, text: str, mode: str = MATCH_WORD) -> str | None:
        """Возвращает первое сработавшее слово или None."""
        if not text:
            return None
        pattern = await self.pattern_for(chat_id, mode)
        if pattern is None:
            return None
        found = pattern.search(normalize(text))
        return found.group(0) if found else None

    async def match_all(
        self, chat_id: int, text: str, mode: str = MATCH_WORD
    ) -> list[str]:
        if not text:
            return []
        pattern = await self.pattern_for(chat_id, mode)
        if pattern is None:
            return []
        return pattern.findall(normalize(text))


def match_words(text: str, words: Sequence[str], mode: str = MATCH_WORD) -> str | None:
    """Разовая проверка без кэша — удобно для тестов и демо."""
    pattern = build_pattern(words, mode)
    if pattern is None:
        return None
    found = pattern.search(normalize(text))
    return found.group(0) if found else None
