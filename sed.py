"""Sed-подобная замена по регулярке: ``s/что/на_что[/флаги]``.

Формат вызова повторяет sed, но с оглядкой на то, что писать это будут с телефона:

* префикс — ``s`` или ``ы`` (одна и та же клавиша в разных раскладках), регистр не важен;
* разделитель — любой небуквенный символ сразу после префикса, так что
  ``s/а/б``, ``s|а|б`` и ``ы#а#б`` эквивалентны;
* по умолчанию меняются **все** вхождения, флаг ``1`` оставляет только первое,
  ``i`` игнорирует регистр, ``g`` принимается для мышечной памяти и ничего не меняет.

Регулярки приходят от пользователей, поэтому здесь всё построено вокруг
недоверия к вводу: длина шаблона ограничена, выполнение идёт с таймаутом
через модуль ``regex`` (в стандартном ``re`` таймаутов нет) и в отдельном
потоке с отпущенным GIL, чтобы злой шаблон не подвесил бота целиком.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import regex

# 's' и 'ы' — одна физическая клавиша в латинской и русской раскладках.
PREFIXES = frozenset("sSыЫ")

MAX_PATTERN_LEN = 200
MAX_REPLACEMENT_LEN = 300
MAX_TEXT_LEN = 4000
MAX_RESULT_LEN = 4000

# Столько секунд даём регулярке. Хватает на любой осмысленный шаблон и
# отсекает катастрофический бэктрекинг вида (a+)+$.
TIMEOUT = 0.5

# Разделителем не может быть буква, цифра, пробел или обратный слеш.
_BAD_DELIMITERS = frozenset("\\ \t\n\r")

# Минимум — 's/а/', то есть удаление найденного.
_MIN_LEN = 4


class SedError(Exception):
    """Ошибка, текст которой можно показать пользователю как есть."""


@dataclass(frozen=True)
class SedCommand:
    pattern: str
    replacement: str
    delimiter: str
    count: int = 0            # 0 — заменить все вхождения
    ignore_case: bool = False


def looks_like_sed(text: str) -> bool:
    """Быстрая проверка для фильтра хендлера, без разбора и без исключений."""
    text = (text or "").strip()
    if len(text) < _MIN_LEN or text[0] not in PREFIXES:
        return False
    delimiter = text[1]
    if delimiter.isalnum() or delimiter in _BAD_DELIMITERS:
        return False
    # Нужен как минимум ещё один разделитель — иначе это не замена.
    return delimiter in text[2:]


def _split_unescaped(body: str, delimiter: str) -> list[str]:
    """Режет строку по разделителю, пропуская экранированные вхождения.

    Экранирование сохраняется в результате: в шаблоне ``\\/`` — это то, что
    движок регулярок и так прочитает как литеральный слеш.
    """
    parts: list[str] = []
    buf: list[str] = []
    escaped = False

    for char in body:
        if escaped:
            buf.append(char)
            escaped = False
        elif char == "\\":
            buf.append(char)
            escaped = True
        elif char == delimiter:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(char)

    parts.append("".join(buf))
    return parts


def _parse_flags(raw: str) -> tuple[int, bool]:
    count, ignore_case = 0, False
    for char in raw.strip().lower():
        if char == "i":
            ignore_case = True
        elif char == "g":
            count = 0          # глобальная замена и так по умолчанию
        elif char == "1":
            count = 1
        else:
            raise SedError(
                f"Не знаю флаг <code>{char}</code>. Доступны: "
                "<code>i</code> — без учёта регистра, <code>1</code> — только первое вхождение."
            )
    return count, ignore_case


def parse(text: str) -> SedCommand | None:
    """Разбирает вызов замены.

    Возвращает ``None``, если это вообще не похоже на замену — тогда сообщение
    надо молча пропустить. Бросает :class:`SedError`, если замену узнали,
    но записана она криво: об этом пользователю стоит сказать.
    """
    text = (text or "").strip()
    if len(text) < _MIN_LEN or text[0] not in PREFIXES:
        return None

    delimiter = text[1]
    if delimiter.isalnum() or delimiter in _BAD_DELIMITERS:
        return None

    parts = _split_unescaped(text[2:], delimiter)
    if len(parts) < 2:
        return None

    if len(parts) > 3:
        raise SedError(
            f"Слишком много разделителей <code>{delimiter}</code>. "
            f"Если он нужен внутри — экранируй: <code>\\{delimiter}</code>, "
            "или возьми другой разделитель."
        )

    pattern, replacement = parts[0], parts[1]
    count, ignore_case = _parse_flags(parts[2] if len(parts) > 2 else "")

    if not pattern:
        raise SedError("Пустой шаблон — нечего искать.")
    if len(pattern) > MAX_PATTERN_LEN:
        raise SedError(f"Шаблон длиннее {MAX_PATTERN_LEN} символов.")
    if len(replacement) > MAX_REPLACEMENT_LEN:
        raise SedError(f"Замена длиннее {MAX_REPLACEMENT_LEN} символов.")

    return SedCommand(
        pattern=pattern,
        replacement=replacement,
        delimiter=delimiter,
        count=count,
        ignore_case=ignore_case,
    )


def _prepare_replacement(replacement: str, delimiter: str) -> str:
    """Снимает экранирование разделителя, оставляя ссылки на группы (\\1, \\g<name>)."""
    return replacement.replace("\\" + delimiter, delimiter)


def _substitute(cmd: SedCommand, text: str) -> str:
    """Синхронная часть — выполняется в отдельном потоке."""
    return regex.sub(
        cmd.pattern,
        _prepare_replacement(cmd.replacement, cmd.delimiter),
        text,
        count=cmd.count,
        flags=regex.IGNORECASE if cmd.ignore_case else 0,
        # concurrent=True отпускает GIL: пока regex работает, бот
        # продолжает обслуживать остальные чаты.
        concurrent=True,
        timeout=TIMEOUT,
    )


async def apply(cmd: SedCommand, text: str) -> str:
    """Применяет замену к тексту. Бросает :class:`SedError` с готовым сообщением."""
    if not text or not text.strip():
        raise SedError("В том сообщении нет текста, который можно править.")
    if len(text) > MAX_TEXT_LEN:
        raise SedError(f"Сообщение длиннее {MAX_TEXT_LEN} символов — не берусь.")

    try:
        result = await asyncio.to_thread(_substitute, cmd, text)
    except TimeoutError:
        raise SedError(
            "Шаблон слишком тяжёлый — не уложился в отведённое время. "
            "Похоже на катастрофический бэктрекинг, упрости регулярку."
        ) from None
    except regex.error as exc:
        raise SedError(f"Не понял регулярку: {exc}") from None
    except (IndexError, KeyError):
        raise SedError("В замене ссылка на группу, которой нет в шаблоне.") from None

    if result == text:
        raise SedError("Ничего не нашлось — текст не изменился.")
    if not result.strip():
        raise SedError("После замены не осталось текста.")
    if len(result) > MAX_RESULT_LEN:
        result = result[:MAX_RESULT_LEN].rstrip() + " …"
    return result
