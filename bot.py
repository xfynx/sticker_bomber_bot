"""Точка входа: сборка Dispatcher и long polling."""
from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats

from bomber import CooldownGate
from config import settings
from handlers import build_router
from matcher import TriggerMatcher
from storage import Storage

log = logging.getLogger("stickerbomber")

PRIVATE_COMMANDS = [
    BotCommand(command="start", description="Что я умею"),
    BotCommand(command="demo", description="Демо-бомбинг"),
    BotCommand(command="try", description="Проверить фразу на триггеры"),
    BotCommand(command="sedhelp", description="Замена по регулярке: s/что/на_что"),
    BotCommand(command="chats", description="Мои чаты"),
    BotCommand(command="use", description="Выбрать чат для настройки"),
    BotCommand(command="settings", description="Настройки чата"),
    BotCommand(command="words", description="Список слов"),
    BotCommand(command="addword", description="Добавить слова"),
    BotCommand(command="delword", description="Удалить слово"),
    BotCommand(command="chance", description="Шанс срабатывания, 1-100%"),
    BotCommand(command="count", description="Стикеров за триггер"),
    BotCommand(command="delay", description="Обратный отсчёт перед залпом"),
    BotCommand(command="nsfw", description="Разрешить 18+ паки"),
]

GROUP_COMMANDS = [
    BotCommand(command="settings", description="Настройки чата"),
    BotCommand(command="words", description="Список слов"),
    BotCommand(command="addword", description="Добавить слова"),
    BotCommand(command="delword", description="Удалить слово"),
    BotCommand(command="chance", description="Шанс срабатывания, 1-100%"),
    BotCommand(command="count", description="Стикеров за триггер"),
    BotCommand(command="delay", description="Обратный отсчёт перед залпом"),
    BotCommand(command="warn", description="Фраза-предупреждение"),
    BotCommand(command="cooldown", description="Пауза между бомбёжками"),
    BotCommand(command="nsfw", description="Разрешить 18+ паки"),
    BotCommand(command="sed", description="Замена по регулярке on/off"),
    BotCommand(command="sedhelp", description="Как работает замена"),
    BotCommand(command="toggle", description="Включить/выключить бота"),
]


def setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


def build_dispatcher(storage: Storage) -> Dispatcher:
    dp = Dispatcher()
    # Прокидываем зависимости во все хендлеры через workflow data.
    dp["storage"] = storage
    dp["matcher"] = TriggerMatcher(storage)
    dp["gate"] = CooldownGate()
    dp.include_router(build_router())
    return dp


async def on_startup(bot: Bot) -> None:
    await bot.set_my_commands(PRIVATE_COMMANDS, scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands(GROUP_COMMANDS, scope=BotCommandScopeAllGroupChats())
    me = await bot.get_me()
    log.info("Запущен как @%s (id=%s)", me.username, me.id)


async def main() -> None:
    setup_logging()

    storage = Storage(settings.db_path)
    await storage.connect()
    seeded = await storage.seed_phrases()
    log.info("База: %s (фраз добавлено: %s)", settings.db_path, seeded)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = build_dispatcher(storage)
    dp.startup.register(on_startup)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        await storage.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Остановлен")
