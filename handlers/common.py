"""Базовые команды и демо в личке."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message

from bomber import strike
from config import settings
from matcher import build_pattern, normalize
from storage import GLOBAL_CHAT_ID, Storage, kinds_for
from utils import esc, parse_int_range

router = Router(name="common")

HELP_USER = """<b>Sticker Bomber</b>

Добавь меня в группу или канал, дай право отправлять сообщения — и я буду
кидать стикеры на заданные слова.

<b>Демо тут, в личке</b>
/demo [1-10] — покажу, как это выглядит (владельцу: <code>/demo 5 nsfw</code>)
/try &lt;текст&gt; — проверю, сработал бы триггер на этой фразе

<b>Замена по регулярке</b>
Ответь на любое сообщение строкой <code>s/что/на_что</code> — пришлю
исправленный текст реплаем на оригинал. Вместо <code>s</code> можно <code>ы</code>.
Например <code>s/маш/м</code> превратит «красивую машину» в «красивую мину».
/sedhelp — флаги, группы и прочие тонкости

<b>Настройка чата</b> (для админов чата)
/chats — чаты, где я есть
/use &lt;chat_id&gt; — выбрать чат для настройки из лички
/settings — текущие настройки

<b>Слова</b>
/words — список
/addword слово, ещё слово
/delword слово
/clearwords

<b>Поведение</b>
/toggle on|off — включить/выключить бота в чате
/nsfw on|off — разрешить 🔞 паки
/count 1-{max_burst} — сколько стикеров за раз
/chance 1-100 — вероятность срабатывания в процентах
/delay 0-{max_delay} — обратный отсчёт перед залпом
/warn on|off — фраза-предупреждение перед залпом
/cooldown &lt;сек&gt; — пауза между бомбёжками
/reply on|off — отвечать реплаем на триггер
/match word|substring — по границам слова или по подстроке
/sed on|off — разрешить замену по регулярке
/id — id текущего чата"""

HELP_OWNER = """
<b>Только для владельца</b>
/addpack &lt;имя|ссылка&gt; [nsfw] — добавить стикерпак
/delpack &lt;имя&gt;
/packs — список паков
/packkind &lt;имя&gt; default|nsfw
/packtoggle &lt;имя&gt; on|off
/refresh [имя] — перечитать стикеры из Telegram
/gwords, /gaddword, /gdelword — глобальные слова для всех чатов
/phrases — фразы-предупреждения перед залпом
/addphrase &lt;текст&gt;, /delphrase &lt;номер&gt;, /togglephrase &lt;номер&gt; on|off
/seedphrases, /clearphrases — стартовый набор фраз
/scope — паки, разрешённые конкретному чату
/stats — общая статистика"""


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    text = HELP_USER.format(
        max_burst=settings.max_burst, max_delay=settings.max_delay
    )
    if settings.is_owner(message.from_user.id if message.from_user else None):
        text += "\n" + HELP_OWNER
    await message.answer(text)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await cmd_start(message)


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else "—"
    await message.answer(
        f"Чат: <code>{message.chat.id}</code> ({message.chat.type})\n"
        f"Ты: <code>{user_id}</code>"
    )


@router.message(Command("demo"), F.chat.type == "private")
async def cmd_demo(
    message: Message, command: CommandObject, bot: Bot, storage: Storage
) -> None:
    args = (command.args or "").split()
    count = parse_int_range(args[0], 1, 10) if args else None
    count = count or 3

    # 🔞-демо доступно только владельцу и по явному запросу: /demo 5 nsfw
    is_owner = settings.is_owner(message.from_user.id if message.from_user else None)
    want_nsfw = is_owner and any(a.lower() in {"nsfw", "18", "🔞"} for a in args)

    file_ids = await storage.pick_stickers(kinds_for(want_nsfw), count)
    if not file_ids:
        if want_nsfw:
            await message.answer(
                "NSFW-паков нет. Добавь: <code>/addpack &lt;имя&gt; nsfw</code>"
            )
        else:
            await message.answer(
                "Паков пока нет. Владелец добавляет их командой "
                "<code>/addpack &lt;имя_пака&gt;</code>."
            )
        return

    result = await strike(
        bot,
        message.chat.id,
        file_ids,
        phrase=await storage.random_phrase(),
        delay=settings.default_delay,
        reply_to=message.message_id,
        concurrency=settings.send_concurrency,
    )
    if not result.ok:
        await message.answer("Не смог отправить ни одного стикера 🤷")


@router.message(Command("try"), F.chat.type == "private")
async def cmd_try(message: Message, command: CommandObject, storage: Storage) -> None:
    """Проверка фразы против слов выбранного чата (или глобальных)."""
    text = (command.args or "").strip()
    if not text:
        await message.answer("Как пользоваться: <code>/try привет как дела</code>")
        return

    user_id = message.from_user.id if message.from_user else 0
    target = await storage.get_ctx(user_id)
    if target is not None:
        cfg = await storage.get_chat(target)
        words = await storage.effective_triggers(target)
        mode = cfg.match_mode if cfg else "word"
        scope_label = f"чата <code>{target}</code>"
    else:
        words = await storage.list_triggers(GLOBAL_CHAT_ID)
        mode = "word"
        scope_label = "глобального списка"

    pattern = build_pattern(words, mode)
    hit = pattern.search(normalize(text)) if pattern else None
    if hit:
        await message.answer(
            f"✅ Сработало на <code>{esc(hit.group(0))}</code> (слова {scope_label})"
        )
    else:
        await message.answer(f"❌ Ни одно слово из {scope_label} не совпало")


@router.message(F.sticker, F.chat.type == "private")
async def on_sticker(message: Message) -> None:
    """Подсказка владельцу: кинул стикер — получил готовую команду добавления пака."""
    if not settings.is_owner(message.from_user.id if message.from_user else None):
        return
    sticker = message.sticker
    if sticker is None:
        return
    if not sticker.set_name:
        await message.answer("Этот стикер не принадлежит паку — добавить нечего.")
        return
    await message.answer(
        f"Пак: <code>{esc(sticker.set_name)}</code>\n\n"
        f"Добавить как обычный:\n<code>/addpack {esc(sticker.set_name)}</code>\n"
        f"Добавить как 🔞:\n<code>/addpack {esc(sticker.set_name)} nsfw</code>"
    )
