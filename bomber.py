"""Движок бомбинга: параллельная отправка стикеров с обработкой FloodWait."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import Message, ReplyParameters

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
MAX_RETRY_WAIT = 30  # если Telegram просит ждать дольше — сдаёмся

MAX_DELAY = 60          # потолок обратного отсчёта, сек
TICK = 1.0              # шаг таймера
BAR_WIDTH = 10          # ширина полоски прогресса
FIRE_TEXT = "💥 <b>ОГОНЬ</b>"


@dataclass
class BombResult:
    sent: int = 0
    failed: int = 0
    flood_waits: int = 0
    forbidden: bool = False
    elapsed: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.sent > 0


class CooldownGate:
    """Не даём чату бомбить чаще, чем раз в N секунд."""

    def __init__(self) -> None:
        self._last: dict[int, float] = {}

    def check(self, chat_id: int, cooldown: int) -> bool:
        if cooldown <= 0:
            return True
        now = time.monotonic()
        last = self._last.get(chat_id, 0.0)
        return now - last >= cooldown

    def hit(self, chat_id: int) -> None:
        self._last[chat_id] = time.monotonic()

    def remaining(self, chat_id: int, cooldown: int) -> float:
        if cooldown <= 0:
            return 0.0
        left = cooldown - (time.monotonic() - self._last.get(chat_id, 0.0))
        return max(0.0, left)

    def reset(self, chat_id: int | None = None) -> None:
        if chat_id is None:
            self._last.clear()
        else:
            self._last.pop(chat_id, None)


def render_timer(left: int, total: int) -> str:
    """Текст обновляемого сообщения-таймера."""
    total = max(1, total)
    left = max(0, min(left, total))
    filled = round(BAR_WIDTH * (total - left) / total)
    bar = "▓" * filled + "░" * (BAR_WIDTH - filled)
    if left <= 0:
        return FIRE_TEXT
    return f"💣 залп через <b>{left}</b>…\n<code>{bar}</code>"


async def announce(
    bot: Bot,
    chat_id: int,
    phrase: str | None,
    reply_to: int | None = None,
) -> Message | None:
    """Фраза-предупреждение реплаем на триггерное сообщение."""
    if not phrase:
        return None
    reply_params = (
        ReplyParameters(message_id=reply_to, allow_sending_without_reply=True)
        if reply_to
        else None
    )
    try:
        return await bot.send_message(chat_id, phrase, reply_parameters=reply_params)
    except TelegramForbiddenError:
        raise
    except TelegramAPIError as exc:
        log.info("announce chat=%s failed: %s", chat_id, exc)
        return None


async def countdown(
    bot: Bot,
    chat_id: int,
    seconds: int,
    *,
    delete_after: bool = True,
) -> Message | None:
    """Отдельным сообщением показывает обратный отсчёт, обновляя его через editMessageText.

    Возвращает сообщение таймера (уже удалённое, если delete_after=True).
    """
    seconds = max(0, min(int(seconds), MAX_DELAY))
    if seconds <= 0:
        return None

    try:
        timer = await bot.send_message(chat_id, render_timer(seconds, seconds))
    except TelegramForbiddenError:
        raise
    except TelegramAPIError as exc:
        # Не смогли показать таймер — всё равно выдерживаем паузу.
        log.info("countdown start chat=%s failed: %s", chat_id, exc)
        for _ in range(seconds):
            await asyncio.sleep(TICK)
        return None

    for left in range(seconds - 1, -1, -1):
        await asyncio.sleep(TICK)
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=timer.message_id,
                text=render_timer(left, seconds),
            )
        except TelegramRetryAfter as exc:
            # Слишком часто редактируем — просто досидим оставшееся время молча.
            await asyncio.sleep(min(exc.retry_after, MAX_RETRY_WAIT))
        except TelegramBadRequest:
            # "message is not modified" или сообщение удалили — не критично.
            continue
        except TelegramAPIError as exc:
            log.debug("countdown tick chat=%s: %s", chat_id, exc)
            continue

    if delete_after:
        try:
            await bot.delete_message(chat_id, timer.message_id)
        except TelegramAPIError:
            pass
    return timer


async def _send_one(
    bot: Bot,
    chat_id: int,
    file_id: str,
    reply_to: int | None,
    result: BombResult,
    sem: asyncio.Semaphore,
) -> None:
    reply_params = (
        ReplyParameters(message_id=reply_to, allow_sending_without_reply=True)
        if reply_to
        else None
    )
    async with sem:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                await bot.send_sticker(
                    chat_id=chat_id,
                    sticker=file_id,
                    reply_parameters=reply_params,
                )
                result.sent += 1
                return
            except TelegramRetryAfter as exc:
                result.flood_waits += 1
                if exc.retry_after > MAX_RETRY_WAIT or attempt == MAX_ATTEMPTS:
                    result.failed += 1
                    result.errors.append(f"flood {exc.retry_after}s")
                    return
                await asyncio.sleep(exc.retry_after + 0.1)
            except TelegramForbiddenError:
                # Бота кикнули или он не имеет права писать — дальше пробовать бессмысленно.
                result.forbidden = True
                result.failed += 1
                result.errors.append("forbidden")
                return
            except TelegramBadRequest as exc:
                # Битый file_id, удалённое сообщение для reply и т.п. — не ретраим.
                result.failed += 1
                result.errors.append(str(exc)[:120])
                return
            except TelegramNetworkError as exc:
                if attempt == MAX_ATTEMPTS:
                    result.failed += 1
                    result.errors.append(f"network: {str(exc)[:80]}")
                    return
                await asyncio.sleep(0.5 * attempt)


async def bomb(
    bot: Bot,
    chat_id: int,
    file_ids: list[str],
    *,
    reply_to: int | None = None,
    concurrency: int = 8,
) -> BombResult:
    """Отправляет пачку стикеров максимально быстро, но не теряя контроль над флудом.

    reply_to применяется только к первому стикеру — иначе получается стена реплаев.
    """
    result = BombResult()
    if not file_ids:
        return result

    started = time.monotonic()
    sem = asyncio.Semaphore(max(1, concurrency))
    tasks = [
        asyncio.create_task(
            _send_one(bot, chat_id, fid, reply_to if i == 0 else None, result, sem)
        )
        for i, fid in enumerate(file_ids)
    ]
    await asyncio.gather(*tasks, return_exceptions=True)
    result.elapsed = time.monotonic() - started

    if result.failed:
        log.warning(
            "bomb chat=%s sent=%s failed=%s floods=%s errors=%s",
            chat_id, result.sent, result.failed, result.flood_waits, result.errors[:3],
        )
    return result


async def strike(
    bot: Bot,
    chat_id: int,
    file_ids: list[str],
    *,
    phrase: str | None = None,
    delay: int = 0,
    reply_to: int | None = None,
    concurrency: int = 8,
) -> BombResult:
    """Полный сценарий: предупреждение → таймер → бомбинг."""
    try:
        warned = await announce(bot, chat_id, phrase, reply_to)
        await countdown(bot, chat_id, delay)
    except TelegramForbiddenError:
        result = BombResult(forbidden=True, failed=len(file_ids))
        result.errors.append("forbidden")
        return result

    # Если предупреждение уже ушло реплаем — стикеры кидаем без реплая,
    # иначе получается стена цитат на одно и то же сообщение.
    sticker_reply = None if warned else reply_to
    return await bomb(
        bot, chat_id, file_ids, reply_to=sticker_reply, concurrency=concurrency
    )
