"""Хендлер замены по регулярке.

Работает только на реплаях: отвечаешь на сообщение строкой вида ``s/маш/м``,
бот присылает исправленный текст реплаем на то же исходное сообщение.

Разбор и применение живут в модуле ``sed`` в корне проекта — это он имеется
в виду ниже в ``from sed import ...``, а не этот файл: импорты в Python 3
абсолютные, так что ``sed`` всегда означает верхнеуровневый модуль.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import Message, ReplyParameters

from bomber import CooldownGate
from sed import SedError, apply, looks_like_sed, parse
from storage import Storage
from utils import enum_value, esc

log = logging.getLogger(__name__)

router = Router(name="sed")

# Замены — дешёвая операция, но спамить ими всё равно можно.
THROTTLE_SECONDS = 2.0

ALLOWED_TYPES = {
    ChatType.PRIVATE.value,
    ChatType.GROUP.value,
    ChatType.SUPERGROUP.value,
}


def _is_sed_command(message: Message) -> bool:
    return looks_like_sed(message.text or "")


@router.message(F.reply_to_message, _is_sed_command)
async def on_sed(message: Message, storage: Storage, gate: CooldownGate) -> None:
    if enum_value(message.chat.type) not in ALLOWED_TYPES:
        return

    # В личке правим всегда, в группе — если админ не выключил через /sed off.
    if enum_value(message.chat.type) != ChatType.PRIVATE.value:
        cfg = await storage.get_chat(message.chat.id)
        if cfg is not None and not cfg.sed:
            return

    try:
        cmd = parse(message.text or "")
    except SedError as exc:
        await message.reply(f"⚠️ {exc}")
        return
    if cmd is None:
        return

    user_id = message.from_user.id if message.from_user else 0
    throttle_key = ("sed", message.chat.id, user_id)
    if not gate.check(throttle_key, THROTTLE_SECONDS):
        return
    gate.hit(throttle_key)

    source = message.reply_to_message
    original = source.text or source.caption or ""

    try:
        fixed = await apply(cmd, original)
    except SedError as exc:
        await message.reply(f"⚠️ {exc}")
        return

    try:
        # Реплай именно на правимое сообщение, а не на команду замены.
        await message.bot.send_message(
            message.chat.id,
            f"✏️ {esc(fixed)}",
            reply_parameters=ReplyParameters(
                message_id=source.message_id,
                allow_sending_without_reply=True,
            ),
        )
    except TelegramForbiddenError:
        log.warning("chat=%s: нет прав ответить на замену", message.chat.id)
    except TelegramAPIError as exc:
        log.info("sed chat=%s send failed: %s", message.chat.id, exc)


@router.message(Command("sedhelp"))
async def cmd_sedhelp(message: Message) -> None:
    await message.answer(_HELP)


_HELP = """<b>Замена по регулярке</b>

Ответь на сообщение строкой <code>s/что/на_что</code> — пришлю исправленный текст.
Вместо <code>s</code> можно <code>ы</code>, чтобы не переключать раскладку.

<b>Примеры</b>
<code>s/маш/м</code>
«увидел красивую машину» → «увидел красивую мину»

<code>s/аб/абушк</code>
«к бабам и бабинам» → «к бабушкам и бабушкинам»

<code>s/к.*ь/пень</code>
«летающий корабль» → «летающий пень»

<b>Флаги</b>
<code>s/а/б/i</code> — без учёта регистра
<code>s/а/б/1</code> — только первое вхождение (по умолчанию меняются все)

<b>Мелочи</b>
Разделителем может быть любой небуквенный символ: <code>s|а|б</code>, <code>ы#а#б</code>.
Группы работают: <code>s/(\\w+) (\\w+)/\\2 \\1</code> меняет два слова местами.
Пустая замена удаляет найденное: <code>s/лишнее слово//</code>"""
