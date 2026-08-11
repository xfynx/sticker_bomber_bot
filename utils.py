"""Общие помощники: права, парсинг аргументов, форматирование."""
from __future__ import annotations

import html
import re
from enum import Enum
from typing import Any, Iterable

from aiogram import Bot
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message

from config import settings
from storage import ChatConfig, Storage

# .value обязателен: str-Enum в aiogram хэшируется по имени члена ('SUPERGROUP'),
# а Telegram присылает строки вида 'supergroup' — без .value `in` не сработает.
GROUP_TYPES = {ChatType.GROUP.value, ChatType.SUPERGROUP.value}
ADMIN_STATUSES = {ChatMemberStatus.CREATOR.value, ChatMemberStatus.ADMINISTRATOR.value}

def enum_value(value: Any) -> str:
    """Строковое значение независимо от того, пришёл enum или голая строка.

    Нужно потому, что str(ChatType.GROUP) даёт 'ChatType.GROUP', а не 'group'.
    """
    if isinstance(value, Enum):
        return str(value.value)
    return str(value) if value is not None else ""


_STICKER_LINK = re.compile(
    r"(?:https?://)?t(?:elegram)?\.me/(?:addstickers|addemoji)/([A-Za-z0-9_]+)",
    re.IGNORECASE,
)
_SET_NAME = re.compile(r"^[A-Za-z0-9_]{1,64}$")


def parse_set_name(raw: str) -> str | None:
    """Принимает и голое имя пака, и ссылку t.me/addstickers/NAME."""
    raw = (raw or "").strip()
    if not raw:
        return None
    link = _STICKER_LINK.search(raw)
    if link:
        return link.group(1)
    raw = raw.lstrip("@")
    return raw if _SET_NAME.match(raw) else None


def split_words(raw: str) -> list[str]:
    """Слова через запятую или перевод строки. Пробелы внутри фразы сохраняются."""
    if not raw:
        return []
    chunks = re.split(r"[,\n;]+", raw)
    out: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        word = " ".join(chunk.split()).lower()
        if word and word not in seen:
            seen.add(word)
            out.append(word)
    return out


def parse_bool(raw: str | None) -> bool | None:
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in {"on", "вкл", "1", "true", "yes", "да", "y"}:
        return True
    if value in {"off", "выкл", "0", "false", "no", "нет", "n"}:
        return False
    return None


def parse_int_range(raw: str | None, low: int, high: int) -> int | None:
    if raw is None:
        return None
    value = raw.strip().rstrip("%")
    if not value.lstrip("-").isdigit():
        return None
    number = int(value)
    return number if low <= number <= high else None


async def is_chat_admin(bot: Bot, chat_id: int, user_id: int | None) -> bool:
    """Владелец бота — всегда админ. Иначе спрашиваем Telegram."""
    if settings.is_owner(user_id):
        return True
    if user_id is None:
        return False
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except TelegramAPIError:
        return False
    return enum_value(member.status) in ADMIN_STATUSES


async def resolve_target(
    message: Message, storage: Storage
) -> tuple[int, ChatConfig | None, str | None]:
    """Определяет, какой чат настраивается.

    В группе — сам чат. В личке — чат, выбранный через /use.
    Возвращает (chat_id, config, error_text).
    """
    if enum_value(message.chat.type) in GROUP_TYPES:
        cfg = await storage.ensure_chat(
            message.chat.id,
            message.chat.title or "",
            message.chat.type,
            default_burst=settings.default_burst,
            default_cooldown=settings.default_cooldown,
            default_chance=settings.default_chance,
            default_delay=settings.default_delay,
        )
        return message.chat.id, cfg, None

    user_id = message.from_user.id if message.from_user else None
    if user_id is None:
        return 0, None, "Не удалось определить пользователя."

    ctx = await storage.get_ctx(user_id)
    if ctx is None:
        return 0, None, (
            "Сначала выбери чат: <code>/chats</code> — список, "
            "затем <code>/use &lt;chat_id&gt;</code>."
        )
    cfg = await storage.get_chat(ctx)
    if cfg is None:
        await storage.clear_ctx(user_id)
        return 0, None, "Выбранный чат больше не известен боту. Выбери заново: /chats"
    return ctx, cfg, None


def esc(text: str) -> str:
    return html.escape(str(text), quote=False)


def fmt_words(words: Iterable[str], limit: int = 80) -> str:
    words = list(words)
    if not words:
        return "<i>пусто</i>"
    shown = words[:limit]
    tail = f"\n… и ещё {len(words) - limit}" if len(words) > limit else ""
    return ", ".join(f"<code>{esc(w)}</code>" for w in shown) + tail


def fmt_chat_settings(cfg: ChatConfig, words_count: int, scope: list[str]) -> str:
    lines = [
        f"<b>Настройки чата</b> <code>{cfg.chat_id}</code>",
        f"{esc(cfg.title) or '—'} ({cfg.type or '?'})",
        "",
        f"Бот: {'✅ включён' if cfg.enabled else '⛔ выключен'}",
        f"Источник стикеров: {'🔞 только NSFW-паки' if cfg.nsfw else 'обычные паки'}",
        f"Стикеров за триггер: <b>{cfg.burst}</b>",
        f"Шанс срабатывания: <b>{cfg.chance}%</b>",
        f"Предупреждение перед залпом: {'да' if cfg.warn else 'нет'}",
        f"Обратный отсчёт: <b>{cfg.delay}</b> сек" if cfg.delay else "Обратный отсчёт: выключен",
        f"Кулдаун: <b>{cfg.cooldown}</b> сек",
        f"Реплай на сообщение: {'да' if cfg.reply_mode else 'нет'}",
        f"Замена по регулярке: {'✏️ да' if cfg.sed else 'нет'}",
        f"Режим поиска: <code>{cfg.match_mode}</code>",
        f"Слов в чате: <b>{words_count}</b>",
    ]
    if scope:
        lines.append(f"Паки только из списка: {', '.join(f'<code>{esc(s)}</code>' for s in scope)}")
    else:
        lines.append("Паки: все разрешённые категории")
    lines.append(f"Бомбёжек всего: {cfg.bombs}")
    return "\n".join(lines)
