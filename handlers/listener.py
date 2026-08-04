"""Слушатель сообщений: ловит триггерные слова и запускает бомбинг."""
from __future__ import annotations

import logging
import random

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.types import ChatMemberUpdated, Message

from bomber import CooldownGate, strike
from config import settings
from matcher import TriggerMatcher
from storage import KIND_DEFAULT, KIND_NSFW, Storage
from utils import enum_value

log = logging.getLogger(__name__)

router = Router(name="listener")

# Важно: ChatType/ChatMemberStatus — str-Enum, но хэшируются по ИМЕНИ участника
# ('SUPERGROUP'), а не по значению. Telegram присылает обычные строки, поэтому
# в множествах держим именно .value, иначе проверка `in` молча не сработает.
GROUPS = {ChatType.GROUP.value, ChatType.SUPERGROUP.value}
LIVE_STATUSES = {
    ChatMemberStatus.MEMBER.value,
    ChatMemberStatus.ADMINISTRATOR.value,
    ChatMemberStatus.CREATOR.value,
    ChatMemberStatus.RESTRICTED.value,
}
GONE_STATUSES = {ChatMemberStatus.LEFT.value, ChatMemberStatus.KICKED.value}
# Личку в список чатов не пишем — там нечего настраивать.
TRACKED_TYPES = GROUPS | {ChatType.CHANNEL.value}


async def _handle(
    message: Message,
    bot: Bot,
    storage: Storage,
    matcher: TriggerMatcher,
    gate: CooldownGate,
) -> None:
    text = message.text or message.caption or ""
    if not text or text.startswith("/"):
        return

    chat = message.chat
    cfg = await storage.get_chat(chat.id)
    if cfg is None:
        cfg = await storage.ensure_chat(
            chat.id,
            chat.title or "",
            chat.type,
            default_burst=settings.default_burst,
            default_cooldown=settings.default_cooldown,
            default_chance=settings.default_chance,
            default_delay=settings.default_delay,
        )
    if not cfg.enabled:
        return

    hit = await matcher.match(chat.id, text, cfg.match_mode)
    if hit is None:
        return

    # Шанс срабатывания. Проверяем ДО кулдауна, чтобы «промах» не съедал паузу.
    if cfg.chance < 100 and random.randint(1, 100) > cfg.chance:
        log.debug("chat=%s hit=%r skipped by chance=%s%%", chat.id, hit, cfg.chance)
        return

    if not gate.check(chat.id, cfg.cooldown):
        return
    gate.hit(chat.id)

    kinds = [KIND_DEFAULT] + ([KIND_NSFW] if cfg.nsfw else [])
    scope = await storage.scope_packs(chat.id)
    file_ids = await storage.pick_stickers(kinds, cfg.burst, scope or None)
    if not file_ids:
        log.info("chat=%s: нет доступных стикеров (kinds=%s scope=%s)", chat.id, kinds, scope)
        gate.reset(chat.id)
        return

    reply_to = message.message_id if cfg.reply_mode else None
    phrase = await storage.random_phrase() if cfg.warn else None
    result = await strike(
        bot,
        chat.id,
        file_ids,
        phrase=phrase,
        delay=cfg.delay,
        reply_to=reply_to,
        concurrency=settings.send_concurrency,
    )

    if result.sent:
        await storage.bump_bombs(chat.id, result.sent)
        log.info(
            "chat=%s hit=%r sent=%s/%s за %.2fs",
            chat.id, hit, result.sent, len(file_ids), result.elapsed,
        )
    if result.forbidden:
        await storage.update_chat(chat.id, enabled=False)
        log.warning("chat=%s: нет прав на отправку, выключаю бота в чате", chat.id)


@router.message(F.chat.type.in_(GROUPS), F.text | F.caption)
async def on_group_message(
    message: Message,
    bot: Bot,
    storage: Storage,
    matcher: TriggerMatcher,
    gate: CooldownGate,
) -> None:
    await _handle(message, bot, storage, matcher, gate)


@router.channel_post(F.text | F.caption)
async def on_channel_post(
    message: Message,
    bot: Bot,
    storage: Storage,
    matcher: TriggerMatcher,
    gate: CooldownGate,
) -> None:
    await _handle(message, bot, storage, matcher, gate)


@router.message(F.chat.type == ChatType.PRIVATE.value, F.text)
async def on_private_message(
    message: Message,
    bot: Bot,
    storage: Storage,
    matcher: TriggerMatcher,
    gate: CooldownGate,
) -> None:
    """Демо в личке: реагируем на слова выбранного чата, иначе на глобальные."""
    text = message.text or ""
    if text.startswith("/"):
        return

    user_id = message.from_user.id if message.from_user else 0
    source_chat = await storage.get_ctx(user_id)
    cfg = await storage.get_chat(source_chat) if source_chat is not None else None
    mode = cfg.match_mode if cfg else "word"
    burst = cfg.burst if cfg else settings.default_burst
    nsfw = cfg.nsfw if cfg else False

    # Для лички ищем по словам выбранного чата; если чат не выбран — по глобальным.
    lookup_chat = source_chat if source_chat is not None else 0
    hit = await matcher.match(lookup_chat, text, mode)
    if hit is None:
        return

    kinds = [KIND_DEFAULT] + ([KIND_NSFW] if nsfw else [])
    file_ids = await storage.pick_stickers(kinds, burst)
    if not file_ids:
        return

    delay = cfg.delay if cfg else settings.default_delay
    phrase = await storage.random_phrase() if (cfg is None or cfg.warn) else None
    await strike(
        bot,
        message.chat.id,
        file_ids,
        phrase=phrase,
        delay=delay,
        reply_to=message.message_id,
        concurrency=settings.send_concurrency,
    )


@router.my_chat_member()
async def on_membership_change(event: ChatMemberUpdated, storage: Storage) -> None:
    """Регистрируем чат при добавлении бота и чистим при удалении."""
    status = enum_value(event.new_chat_member.status)
    chat = event.chat
    if enum_value(chat.type) not in TRACKED_TYPES:
        return

    if status in LIVE_STATUSES:
        await storage.ensure_chat(
            chat.id,
            chat.title or "",
            chat.type,
            default_burst=settings.default_burst,
            default_cooldown=settings.default_cooldown,
            default_chance=settings.default_chance,
            default_delay=settings.default_delay,
        )
        log.info("Добавлен в чат %s (%s)", chat.id, chat.title)
    elif status in GONE_STATUSES:
        await storage.forget_chat(chat.id)
        log.info("Удалён из чата %s", chat.id)
