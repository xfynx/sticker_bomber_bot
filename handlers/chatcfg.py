"""Настройка конкретного чата: слова, NSFW, количество, шанс, кулдаун."""
from __future__ import annotations

from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from config import settings
from matcher import TriggerMatcher
from storage import MATCH_MODES, ChatConfig, Storage
from utils import (
    esc,
    fmt_chat_settings,
    fmt_words,
    is_chat_admin,
    parse_bool,
    parse_int_range,
    resolve_target,
    split_words,
)

router = Router(name="chatcfg")


async def _target(
    message: Message, bot: Bot, storage: Storage
) -> tuple[int, ChatConfig] | None:
    """Определяет чат и проверяет права. При отказе сам отвечает пользователю."""
    chat_id, cfg, error = await resolve_target(message, storage)
    if error:
        await message.answer(error)
        return None
    user_id = message.from_user.id if message.from_user else None
    if not await is_chat_admin(bot, chat_id, user_id):
        await message.answer("Это может делать только админ чата.")
        return None
    return chat_id, cfg


@router.message(Command("settings"))
async def cmd_settings(message: Message, bot: Bot, storage: Storage) -> None:
    target = await _target(message, bot, storage)
    if target is None:
        return
    chat_id, cfg = target
    words = await storage.list_triggers(chat_id)
    scope = await storage.scope_packs(chat_id)
    await message.answer(fmt_chat_settings(cfg, len(words), scope))


@router.message(Command("chats"))
async def cmd_chats(message: Message, bot: Bot, storage: Storage) -> None:
    """Чаты, где бот есть и где пользователь — админ."""
    user_id = message.from_user.id if message.from_user else None
    if user_id is None:
        return

    chats = await storage.list_chats()
    mine = []
    for cfg in chats:
        if await is_chat_admin(bot, cfg.chat_id, user_id):
            mine.append(cfg)

    if not mine:
        await message.answer(
            "Я не нашёл чатов, где ты админ и где я состою.\n"
            "Добавь меня в чат и дай права на отправку сообщений."
        )
        return

    current = await storage.get_ctx(user_id)
    lines = ["<b>Твои чаты</b>", ""]
    for cfg in mine:
        mark = " ← выбран" if cfg.chat_id == current else ""
        state = "✅" if cfg.enabled else "⛔"
        lines.append(
            f"{state} <b>{esc(cfg.title) or cfg.chat_id}</b>{mark}\n"
            f"   <code>/use {cfg.chat_id}</code>"
        )
    await message.answer("\n".join(lines))


@router.message(Command("use"))
async def cmd_use(
    message: Message, command: CommandObject, bot: Bot, storage: Storage
) -> None:
    raw = (command.args or "").strip()
    user_id = message.from_user.id if message.from_user else None
    if user_id is None:
        return
    if not raw.lstrip("-").isdigit():
        await message.answer("Как пользоваться: <code>/use -1001234567890</code> (см. /chats)")
        return

    chat_id = int(raw)
    cfg = await storage.get_chat(chat_id)
    if cfg is None:
        await message.answer("Я не состою в этом чате.")
        return
    if not await is_chat_admin(bot, chat_id, user_id):
        await message.answer("Ты не админ этого чата.")
        return

    await storage.set_ctx(user_id, chat_id)
    await message.answer(
        f"Настраиваю <b>{esc(cfg.title) or chat_id}</b>. "
        "Дальше команды в личке применяются к нему."
    )


# ---------- слова ----------


@router.message(Command("words"))
async def cmd_words(message: Message, bot: Bot, storage: Storage) -> None:
    target = await _target(message, bot, storage)
    if target is None:
        return
    chat_id, _ = target
    own = await storage.list_triggers(chat_id)
    total = await storage.effective_triggers(chat_id)
    globals_count = len(total) - len({w for w in own})
    text = f"<b>Слова чата</b> ({len(own)}):\n{fmt_words(own)}"
    if globals_count > 0:
        text += f"\n\n+ глобальных слов от владельца: <b>{globals_count}</b>"
    await message.answer(text)


@router.message(Command("addword"))
async def cmd_addword(
    message: Message, command: CommandObject, bot: Bot, storage: Storage
) -> None:
    target = await _target(message, bot, storage)
    if target is None:
        return
    chat_id, _ = target
    words = split_words(command.args or "")
    if not words:
        await message.answer(
            "Как пользоваться: <code>/addword кот, собака, доброе утро</code>\n"
            "Разделяй запятой или переводом строки."
        )
        return
    added = await storage.add_triggers(chat_id, words)
    skipped = len(words) - len(added)
    text = f"Добавлено ({len(added)}): {fmt_words(added)}" if added else "Все эти слова уже есть."
    if skipped and added:
        text += f"\nПропущено дублей: {skipped}"
    await message.answer(text)


@router.message(Command("delword"))
async def cmd_delword(
    message: Message, command: CommandObject, bot: Bot, storage: Storage
) -> None:
    target = await _target(message, bot, storage)
    if target is None:
        return
    chat_id, _ = target
    words = split_words(command.args or "")
    if not words:
        await message.answer("Как пользоваться: <code>/delword кот</code>")
        return
    removed = await storage.remove_triggers(chat_id, words)
    await message.answer(
        f"Удалено: {fmt_words(removed)}" if removed else "Таких слов в чате нет."
    )


@router.message(Command("clearwords"))
async def cmd_clearwords(message: Message, bot: Bot, storage: Storage) -> None:
    target = await _target(message, bot, storage)
    if target is None:
        return
    chat_id, _ = target
    n = await storage.clear_triggers(chat_id)
    await message.answer(f"Удалено слов: <b>{n}</b>")


# ---------- поведение ----------


@router.message(Command("toggle"))
async def cmd_toggle(
    message: Message, command: CommandObject, bot: Bot, storage: Storage
) -> None:
    target = await _target(message, bot, storage)
    if target is None:
        return
    chat_id, cfg = target
    value = parse_bool(command.args)
    if value is None:
        value = not cfg.enabled
    await storage.update_chat(chat_id, enabled=value)
    await message.answer("✅ Бот включён в чате." if value else "⛔ Бот выключен в чате.")


@router.message(Command("nsfw"))
async def cmd_nsfw(
    message: Message, command: CommandObject, bot: Bot, storage: Storage
) -> None:
    target = await _target(message, bot, storage)
    if target is None:
        return
    chat_id, cfg = target
    value = parse_bool(command.args)
    if value is None:
        await message.answer(
            f"Сейчас NSFW: <b>{'включён' if cfg.nsfw else 'выключен'}</b>\n"
            "Переключить: <code>/nsfw on</code> / <code>/nsfw off</code>"
        )
        return
    await storage.update_chat(chat_id, nsfw=value)
    await message.answer(
        "🔞 NSFW-паки включены в этом чате." if value else "NSFW-паки выключены."
    )


@router.message(Command("count"))
async def cmd_count(
    message: Message, command: CommandObject, bot: Bot, storage: Storage
) -> None:
    target = await _target(message, bot, storage)
    if target is None:
        return
    chat_id, cfg = target
    value = parse_int_range(command.args, 1, settings.max_burst)
    if value is None:
        await message.answer(
            f"Сейчас: <b>{cfg.burst}</b> стикеров за триггер.\n"
            f"Изменить: <code>/count 10</code> (1–{settings.max_burst})"
        )
        return
    await storage.update_chat(chat_id, burst=value)
    await message.answer(f"Теперь кидаю по <b>{value}</b> стикеров.")


@router.message(Command("chance"))
async def cmd_chance(
    message: Message, command: CommandObject, bot: Bot, storage: Storage
) -> None:
    target = await _target(message, bot, storage)
    if target is None:
        return
    chat_id, cfg = target
    value = parse_int_range(command.args, 1, 100)
    if value is None:
        await message.answer(
            f"Шанс срабатывания сейчас: <b>{cfg.chance}%</b>\n"
            "Изменить: <code>/chance 25</code> (1–100).\n"
            "При 25% бот отреагирует примерно на каждое четвёртое совпадение."
        )
        return
    await storage.update_chat(chat_id, chance=value)
    note = " — реагирую на каждое совпадение" if value == 100 else ""
    await message.answer(f"Шанс срабатывания: <b>{value}%</b>{note}")


@router.message(Command("delay"))
async def cmd_delay(
    message: Message, command: CommandObject, bot: Bot, storage: Storage
) -> None:
    target = await _target(message, bot, storage)
    if target is None:
        return
    chat_id, cfg = target
    value = parse_int_range(command.args, 0, settings.max_delay)
    if value is None:
        await message.answer(
            f"Обратный отсчёт сейчас: <b>{cfg.delay}</b> сек.\n"
            f"Изменить: <code>/delay 5</code> (0–{settings.max_delay}, 0 — без таймера).\n"
            "Бот кидает фразу-предупреждение, потом тикающий таймер, потом залп."
        )
        return
    await storage.update_chat(chat_id, delay=value)
    await message.answer(
        f"Обратный отсчёт: <b>{value}</b> сек."
        if value
        else "Таймер выключен — бомблю сразу после предупреждения."
    )


@router.message(Command("warn"))
async def cmd_warn(
    message: Message, command: CommandObject, bot: Bot, storage: Storage
) -> None:
    target = await _target(message, bot, storage)
    if target is None:
        return
    chat_id, cfg = target
    value = parse_bool(command.args)
    if value is None:
        value = not cfg.warn
    await storage.update_chat(chat_id, warn=value)
    await message.answer(
        "Перед залпом кидаю фразу-предупреждение."
        if value
        else "Предупреждения выключены — только таймер и стикеры."
    )


@router.message(Command("cooldown"))
async def cmd_cooldown(
    message: Message, command: CommandObject, bot: Bot, storage: Storage
) -> None:
    target = await _target(message, bot, storage)
    if target is None:
        return
    chat_id, cfg = target
    value = parse_int_range(command.args, 0, 3600)
    if value is None:
        await message.answer(
            f"Кулдаун сейчас: <b>{cfg.cooldown}</b> сек.\n"
            "Изменить: <code>/cooldown 30</code> (0 — без ограничений)"
        )
        return
    await storage.update_chat(chat_id, cooldown=value)
    await message.answer(
        f"Кулдаун: <b>{value}</b> сек." if value else "Кулдаун снят — бомблю без пауз."
    )


@router.message(Command("reply"))
async def cmd_reply(
    message: Message, command: CommandObject, bot: Bot, storage: Storage
) -> None:
    target = await _target(message, bot, storage)
    if target is None:
        return
    chat_id, cfg = target
    value = parse_bool(command.args)
    if value is None:
        value = not cfg.reply_mode
    await storage.update_chat(chat_id, reply_mode=value)
    await message.answer(
        "Отвечаю реплаем на триггерное сообщение."
        if value
        else "Кидаю стикеры просто в чат, без реплая."
    )


@router.message(Command("match"))
async def cmd_match(
    message: Message,
    command: CommandObject,
    bot: Bot,
    storage: Storage,
    matcher: TriggerMatcher,
) -> None:
    target = await _target(message, bot, storage)
    if target is None:
        return
    chat_id, cfg = target
    mode = (command.args or "").strip().lower()
    if mode not in MATCH_MODES:
        await message.answer(
            f"Режим сейчас: <code>{cfg.match_mode}</code>\n\n"
            "<code>/match word</code> — только целые слова "
            "(«кот» не сработает на «котёл»)\n"
            "<code>/match substring</code> — совпадение в любом месте "
            "(«кот» сработает и на «котёл»)"
        )
        return
    await storage.update_chat(chat_id, match_mode=mode)
    matcher.invalidate(chat_id)
    await message.answer(f"Режим поиска: <code>{mode}</code>")
