"""Команды владельца бота: стикерпаки, глобальные слова, статистика."""
from __future__ import annotations

import logging

from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from config import settings
from storage import (
    GLOBAL_CHAT_ID,
    KIND_DEFAULT,
    KIND_NSFW,
    KINDS,
    Storage,
)
from utils import esc, fmt_words, parse_bool, parse_set_name, split_words

log = logging.getLogger(__name__)

router = Router(name="owner")


def _is_owner(message: Message) -> bool:
    # Явная функция вместо магического фильтра: у сообщений из каналов
    # from_user бывает None, и F.from_user.id на нём споткнётся.
    return settings.is_owner(message.from_user.id if message.from_user else None)


# Все хендлеры этого роутера — только для владельца бота.
router.message.filter(_is_owner)


async def _fetch_pack(bot: Bot, set_name: str) -> tuple[str, list[tuple[str, str]]] | None:
    try:
        sticker_set = await bot.get_sticker_set(set_name)
    except TelegramAPIError as exc:
        log.info("get_sticker_set(%s) failed: %s", set_name, exc)
        return None
    stickers = [(s.file_id, s.emoji or "") for s in sticker_set.stickers]
    return sticker_set.title, stickers


@router.message(Command("addpack"))
async def cmd_addpack(
    message: Message, command: CommandObject, bot: Bot, storage: Storage
) -> None:
    args = (command.args or "").split()
    if not args:
        await message.answer(
            "Как пользоваться:\n"
            "<code>/addpack HotCherry</code>\n"
            "<code>/addpack https://t.me/addstickers/HotCherry nsfw</code>"
        )
        return

    set_name = parse_set_name(args[0])
    if not set_name:
        await message.answer("Не понял имя пака. Дай короткое имя или ссылку t.me/addstickers/…")
        return

    kind = KIND_NSFW if len(args) > 1 and args[1].lower() in {"nsfw", "18", "🔞"} else KIND_DEFAULT

    fetched = await _fetch_pack(bot, set_name)
    if fetched is None:
        await message.answer(
            f"Telegram не отдал пак <code>{esc(set_name)}</code>. "
            "Проверь имя — оно чувствительно к регистру."
        )
        return

    title, stickers = fetched
    if not stickers:
        await message.answer("В паке нет стикеров.")
        return

    count = await storage.upsert_pack(set_name, title, kind, stickers)
    label = "🔞 NSFW" if kind == KIND_NSFW else "обычный"
    await message.answer(
        f"✅ <b>{esc(title)}</b> (<code>{esc(set_name)}</code>)\n"
        f"Стикеров: <b>{count}</b> · категория: {label}"
    )


@router.message(Command("delpack"))
async def cmd_delpack(
    message: Message, command: CommandObject, storage: Storage
) -> None:
    set_name = parse_set_name(command.args or "")
    if not set_name:
        await message.answer("Как пользоваться: <code>/delpack HotCherry</code>")
        return
    if await storage.delete_pack(set_name):
        await message.answer(f"🗑 Пак <code>{esc(set_name)}</code> удалён.")
    else:
        await message.answer("Такого пака нет.")


@router.message(Command("packs"))
async def cmd_packs(message: Message, storage: Storage) -> None:
    packs = await storage.list_packs()
    if not packs:
        await message.answer("Паков нет. Добавь: <code>/addpack &lt;имя&gt;</code>")
        return

    lines = ["<b>Стикерпаки</b>", ""]
    for kind, header in ((KIND_DEFAULT, "Обычные"), (KIND_NSFW, "🔞 NSFW")):
        group = [p for p in packs if p.kind == kind]
        if not group:
            continue
        lines.append(f"<b>{header}</b>")
        for pack in group:
            flag = "" if pack.enabled else " ⛔"
            lines.append(
                f"• <code>{esc(pack.set_name)}</code> — {esc(pack.title)} "
                f"({pack.sticker_count} шт){flag}"
            )
        lines.append("")

    total = await storage.count_stickers()
    lines.append(f"Всего активных стикеров: <b>{total}</b>")
    await message.answer("\n".join(lines))


@router.message(Command("packkind"))
async def cmd_packkind(
    message: Message, command: CommandObject, storage: Storage
) -> None:
    args = (command.args or "").split()
    if len(args) < 2 or args[1].lower() not in KINDS:
        await message.answer("Как пользоваться: <code>/packkind HotCherry nsfw</code>")
        return
    set_name = parse_set_name(args[0])
    if set_name and await storage.set_pack_kind(set_name, args[1].lower()):
        await message.answer(f"Категория <code>{esc(set_name)}</code> → {args[1].lower()}")
    else:
        await message.answer("Пак не найден.")


@router.message(Command("packtoggle"))
async def cmd_packtoggle(
    message: Message, command: CommandObject, storage: Storage
) -> None:
    args = (command.args or "").split()
    enabled = parse_bool(args[1]) if len(args) > 1 else None
    set_name = parse_set_name(args[0]) if args else None
    if set_name is None or enabled is None:
        await message.answer("Как пользоваться: <code>/packtoggle HotCherry off</code>")
        return
    if await storage.set_pack_enabled(set_name, enabled):
        await message.answer(
            f"<code>{esc(set_name)}</code> {'включён' if enabled else 'выключен'}."
        )
    else:
        await message.answer("Пак не найден.")


@router.message(Command("refresh"))
async def cmd_refresh(
    message: Message, command: CommandObject, bot: Bot, storage: Storage
) -> None:
    """Перечитывает паки из Telegram — на случай, если автор добавил стикеры."""
    arg = parse_set_name(command.args or "") if command.args else None
    names = [arg] if arg else await storage.list_pack_names()
    if not names:
        await message.answer("Нечего обновлять.")
        return

    status = await message.answer(f"Обновляю {len(names)} пак(ов)…")
    ok, failed, total = 0, [], 0
    for name in names:
        pack = await storage.get_pack(name)
        if pack is None:
            failed.append(name)
            continue
        fetched = await _fetch_pack(bot, name)
        if fetched is None:
            failed.append(name)
            continue
        title, stickers = fetched
        total += await storage.upsert_pack(name, title, pack.kind, stickers)
        ok += 1

    text = f"✅ Обновлено паков: <b>{ok}</b>, стикеров: <b>{total}</b>"
    if failed:
        text += "\n⚠️ Не удалось: " + ", ".join(f"<code>{esc(n)}</code>" for n in failed)
    await status.edit_text(text)


# ---------- глобальные слова ----------


@router.message(Command("gwords"))
async def cmd_gwords(message: Message, storage: Storage) -> None:
    words = await storage.list_triggers(GLOBAL_CHAT_ID)
    await message.answer(
        f"<b>Глобальные слова</b> ({len(words)}) — действуют во всех чатах:\n"
        + fmt_words(words)
    )


@router.message(Command("gaddword"))
async def cmd_gaddword(
    message: Message, command: CommandObject, storage: Storage
) -> None:
    words = split_words(command.args or "")
    if not words:
        await message.answer("Как пользоваться: <code>/gaddword кот, пёс</code>")
        return
    added = await storage.add_triggers(GLOBAL_CHAT_ID, words)
    await message.answer(
        f"Добавлено глобально: {fmt_words(added)}" if added else "Все эти слова уже есть."
    )


@router.message(Command("gdelword"))
async def cmd_gdelword(
    message: Message, command: CommandObject, storage: Storage
) -> None:
    words = split_words(command.args or "")
    removed = await storage.remove_triggers(GLOBAL_CHAT_ID, words)
    await message.answer(
        f"Удалено: {fmt_words(removed)}" if removed else "Нечего удалять."
    )


# ---------- фразы-предупреждения ----------


@router.message(Command("phrases"))
async def cmd_phrases(message: Message, storage: Storage) -> None:
    phrases = await storage.list_phrases()
    if not phrases:
        await message.answer(
            "Список фраз пуст — бот будет бомбить молча.\n"
            "Добавить: <code>/addphrase ну всё, ты попал</code>\n"
            "Вернуть стартовый набор: <code>/seedphrases</code>"
        )
        return
    lines = ["<b>Фразы перед залпом</b>", ""]
    for pid, text, enabled in phrases:
        mark = "" if enabled else " ⛔"
        lines.append(f"<code>{pid}</code>. {esc(text)}{mark}")
    lines += [
        "",
        "<code>/addphrase текст</code> — добавить",
        "<code>/delphrase 3</code> — удалить по номеру",
        "<code>/togglephrase 3 off</code> — временно отключить",
    ]
    await message.answer("\n".join(lines))


@router.message(Command("addphrase"))
async def cmd_addphrase(
    message: Message, command: CommandObject, storage: Storage
) -> None:
    text = (command.args or "").strip()
    if not text:
        await message.answer("Как пользоваться: <code>/addphrase обстрел активирован</code>")
        return
    phrase_id = await storage.add_phrase(text)
    if phrase_id is None:
        await message.answer("Такая фраза уже есть.")
        return
    await message.answer(f"✅ Фраза <code>{phrase_id}</code> добавлена: {esc(text)}")


@router.message(Command("delphrase"))
async def cmd_delphrase(
    message: Message, command: CommandObject, storage: Storage
) -> None:
    raw = (command.args or "").strip()
    if not raw.isdigit():
        await message.answer("Как пользоваться: <code>/delphrase 3</code> (номер из /phrases)")
        return
    if await storage.delete_phrase(int(raw)):
        await message.answer("🗑 Удалено.")
    else:
        await message.answer("Фразы с таким номером нет.")


@router.message(Command("togglephrase"))
async def cmd_togglephrase(
    message: Message, command: CommandObject, storage: Storage
) -> None:
    args = (command.args or "").split()
    enabled = parse_bool(args[1]) if len(args) > 1 else None
    if len(args) < 2 or not args[0].isdigit() or enabled is None:
        await message.answer("Как пользоваться: <code>/togglephrase 3 off</code>")
        return
    if await storage.set_phrase_enabled(int(args[0]), enabled):
        await message.answer("Готово.")
    else:
        await message.answer("Фразы с таким номером нет.")


@router.message(Command("seedphrases"))
async def cmd_seedphrases(message: Message, storage: Storage) -> None:
    n = await storage.seed_phrases()
    if n:
        await message.answer(f"Добавлен стартовый набор: <b>{n}</b> фраз.")
    else:
        await message.answer(
            "Список не пуст, ничего не трогаю. "
            "Чтобы залить заново: <code>/clearphrases</code>, потом <code>/seedphrases</code>."
        )


@router.message(Command("clearphrases"))
async def cmd_clearphrases(message: Message, storage: Storage) -> None:
    n = await storage.clear_phrases()
    await message.answer(f"Удалено фраз: <b>{n}</b>")


# ---------- чаты и статистика ----------


@router.message(Command("stats"))
async def cmd_stats(message: Message, storage: Storage) -> None:
    packs = await storage.list_packs()
    chats = await storage.list_chats()
    total = await storage.count_stickers()
    nsfw_total = await storage.count_stickers([KIND_NSFW])
    global_words = await storage.list_triggers(GLOBAL_CHAT_ID)
    bombs = sum(c.bombs for c in chats)

    await message.answer(
        "<b>Статистика</b>\n"
        f"Паков: <b>{len(packs)}</b> (🔞 {sum(1 for p in packs if p.kind == KIND_NSFW)})\n"
        f"Стикеров активно: <b>{total}</b> (🔞 {nsfw_total})\n"
        f"Чатов: <b>{len(chats)}</b> (активных {sum(1 for c in chats if c.enabled)})\n"
        f"Глобальных слов: <b>{len(global_words)}</b>\n"
        f"Бомбёжек всего: <b>{bombs}</b>"
    )


@router.message(Command("scope"))
async def cmd_scope(
    message: Message, command: CommandObject, storage: Storage
) -> None:
    """Ограничить чат конкретными паками: /scope <chat_id> add|del|clear|list [пак]."""
    args = (command.args or "").split()
    if not args or not args[0].lstrip("-").isdigit():
        await message.answer(
            "Как пользоваться:\n"
            "<code>/scope -1001234567890 list</code>\n"
            "<code>/scope -1001234567890 add HotCherry</code>\n"
            "<code>/scope -1001234567890 del HotCherry</code>\n"
            "<code>/scope -1001234567890 clear</code>\n\n"
            "Пустой список = чату доступны все паки разрешённых категорий."
        )
        return

    chat_id = int(args[0])
    action = args[1].lower() if len(args) > 1 else "list"
    target = parse_set_name(args[2]) if len(args) > 2 else None

    if action == "list":
        scope = await storage.scope_packs(chat_id)
        await message.answer(
            f"Паки чата <code>{chat_id}</code>: " + (fmt_words(scope) if scope else "<i>все</i>")
        )
    elif action == "add" and target:
        if await storage.get_pack(target) is None:
            await message.answer("Такого пака нет в боте.")
            return
        await storage.scope_add(chat_id, target)
        await message.answer(f"Добавлен в скоуп: <code>{esc(target)}</code>")
    elif action == "del" and target:
        await storage.scope_remove(chat_id, target)
        await message.answer(f"Убран из скоупа: <code>{esc(target)}</code>")
    elif action == "clear":
        n = await storage.scope_clear(chat_id)
        await message.answer(f"Скоуп очищен ({n}). Чату снова доступны все паки.")
    else:
        await message.answer("Не понял действие. Доступно: list, add, del, clear.")
