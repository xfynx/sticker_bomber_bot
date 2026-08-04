"""Тесты движка бомбинга на фейковом боте (сеть не трогаем)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)

import bomber
from bomber import BombResult, CooldownGate, announce, bomb, countdown, render_timer, strike


@dataclass
class FakeMessage:
    message_id: int
    chat_id: int
    text: str = ""


@dataclass
class FakeBot:
    """Минимальная замена aiogram.Bot: пишет вызовы в лог и умеет падать по сценарию."""

    fail_with: list[Exception | None] = field(default_factory=list)
    sent_stickers: list[str] = field(default_factory=list)
    sent_messages: list[str] = field(default_factory=list)
    edits: list[str] = field(default_factory=list)
    deleted: list[int] = field(default_factory=list)
    send_message_error: Exception | None = None
    edit_error: Exception | None = None
    delete_error: Exception | None = None
    _next_id: int = 1000

    def _pop_failure(self) -> Exception | None:
        return self.fail_with.pop(0) if self.fail_with else None

    async def send_sticker(self, chat_id, sticker, reply_parameters=None, **kw):
        failure = self._pop_failure()
        if failure is not None:
            raise failure
        self.sent_stickers.append(sticker)
        self._next_id += 1
        return FakeMessage(self._next_id, chat_id)

    async def send_message(self, chat_id, text, reply_parameters=None, **kw):
        if self.send_message_error is not None:
            raise self.send_message_error
        self.sent_messages.append(text)
        self._next_id += 1
        return FakeMessage(self._next_id, chat_id, text)

    async def edit_message_text(self, chat_id, message_id, text, **kw):
        if self.edit_error is not None:
            raise self.edit_error
        self.edits.append(text)
        return FakeMessage(message_id, chat_id, text)

    async def delete_message(self, chat_id, message_id, **kw):
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted.append(message_id)
        return True


def flood(seconds: int = 0) -> TelegramRetryAfter:
    return TelegramRetryAfter(method=None, message="flood", retry_after=seconds)


def bad_request(text: str = "bad") -> TelegramBadRequest:
    return TelegramBadRequest(method=None, message=text)


def forbidden() -> TelegramForbiddenError:
    return TelegramForbiddenError(method=None, message="bot was kicked")


@pytest.fixture(autouse=True)
def fast_ticks(monkeypatch):
    """Чтобы таймер не растягивал тесты на реальные секунды."""
    monkeypatch.setattr(bomber, "TICK", 0.001)


class TestBomb:
    async def test_sends_everything(self):
        bot = FakeBot()
        result = await bomb(bot, -100, ["a", "b", "c"])
        assert result.sent == 3
        assert result.failed == 0
        assert result.ok is True
        assert sorted(bot.sent_stickers) == ["a", "b", "c"]

    async def test_empty_list_is_noop(self):
        bot = FakeBot()
        result = await bomb(bot, -100, [])
        assert result.sent == 0
        assert result.ok is False
        assert bot.sent_stickers == []

    async def test_retries_after_flood(self):
        bot = FakeBot(fail_with=[flood(0)])
        result = await bomb(bot, -100, ["a"], concurrency=1)
        assert result.sent == 1
        assert result.flood_waits == 1

    async def test_gives_up_after_max_attempts(self):
        bot = FakeBot(fail_with=[flood(0), flood(0), flood(0)])
        result = await bomb(bot, -100, ["a"], concurrency=1)
        assert result.sent == 0
        assert result.failed == 1
        assert result.flood_waits == 3

    async def test_long_flood_not_retried(self):
        bot = FakeBot(fail_with=[flood(bomber.MAX_RETRY_WAIT + 1)])
        result = await bomb(bot, -100, ["a"], concurrency=1)
        assert result.sent == 0
        assert result.failed == 1
        assert "flood" in result.errors[0]

    async def test_bad_request_not_retried(self):
        bot = FakeBot(fail_with=[bad_request("wrong file_id")])
        result = await bomb(bot, -100, ["a"], concurrency=1)
        assert result.sent == 0
        assert result.failed == 1
        assert bot.sent_stickers == []

    async def test_forbidden_flags_result(self):
        bot = FakeBot(fail_with=[forbidden()])
        result = await bomb(bot, -100, ["a"], concurrency=1)
        assert result.forbidden is True
        assert result.sent == 0

    async def test_network_error_is_retried(self):
        bot = FakeBot(fail_with=[TelegramNetworkError(method=None, message="timeout")])
        result = await bomb(bot, -100, ["a"], concurrency=1)
        assert result.sent == 1

    async def test_partial_failure_still_sends_rest(self):
        bot = FakeBot(fail_with=[bad_request()])
        result = await bomb(bot, -100, ["a", "b", "c"], concurrency=1)
        assert result.sent == 2
        assert result.failed == 1

    async def test_concurrency_limit_respected(self):
        """Одновременно в полёте не больше `concurrency` отправок."""
        peak = 0
        current = 0

        class CountingBot(FakeBot):
            async def send_sticker(self, chat_id, sticker, reply_parameters=None, **kw):
                nonlocal peak, current
                current += 1
                peak = max(peak, current)
                await asyncio.sleep(0.005)
                current -= 1
                self.sent_stickers.append(sticker)
                return FakeMessage(1, chat_id)

        bot = CountingBot()
        await bomb(bot, -100, [f"s{i}" for i in range(20)], concurrency=3)
        assert peak <= 3

    async def test_reply_only_on_first_sticker(self):
        seen: list[object] = []

        class RecordingBot(FakeBot):
            async def send_sticker(self, chat_id, sticker, reply_parameters=None, **kw):
                seen.append(reply_parameters)
                return FakeMessage(1, chat_id)

        await bomb(RecordingBot(), -100, ["a", "b", "c"], reply_to=55, concurrency=1)
        assert seen[0] is not None
        assert all(p is None for p in seen[1:])


class TestRenderTimer:
    def test_shows_seconds_left(self):
        assert "3" in render_timer(3, 5)

    def test_zero_is_fire(self):
        assert render_timer(0, 5) == bomber.FIRE_TEXT

    def test_bar_width_constant(self):
        for left in range(0, 6):
            text = render_timer(left, 5)
            if left == 0:
                continue
            bar = text.split("<code>")[1].split("</code>")[0]
            assert len(bar) == bomber.BAR_WIDTH

    def test_bar_fills_as_time_passes(self):
        early = render_timer(5, 5).count("▓")
        late = render_timer(1, 5).count("▓")
        assert late > early

    def test_clamps_out_of_range(self):
        assert render_timer(99, 5) == render_timer(5, 5)
        assert render_timer(-3, 5) == bomber.FIRE_TEXT

    def test_zero_total_does_not_divide_by_zero(self):
        assert render_timer(0, 0) == bomber.FIRE_TEXT


class TestAnnounce:
    async def test_sends_phrase(self):
        bot = FakeBot()
        msg = await announce(bot, -100, "обстрел активирован", reply_to=7)
        assert msg is not None
        assert bot.sent_messages == ["обстрел активирован"]

    async def test_none_phrase_is_skipped(self):
        bot = FakeBot()
        assert await announce(bot, -100, None) is None
        assert bot.sent_messages == []

    async def test_empty_phrase_is_skipped(self):
        bot = FakeBot()
        assert await announce(bot, -100, "") is None

    async def test_api_error_swallowed(self):
        bot = FakeBot(send_message_error=bad_request())
        assert await announce(bot, -100, "текст") is None

    async def test_forbidden_propagates(self):
        bot = FakeBot(send_message_error=forbidden())
        with pytest.raises(TelegramForbiddenError):
            await announce(bot, -100, "текст")


class TestCountdown:
    async def test_zero_seconds_skips(self):
        bot = FakeBot()
        assert await countdown(bot, -100, 0) is None
        assert bot.sent_messages == []

    async def test_edits_once_per_second(self):
        bot = FakeBot()
        await countdown(bot, -100, 3)
        assert len(bot.sent_messages) == 1
        assert len(bot.edits) == 3  # 2, 1, 0
        assert bot.edits[-1] == bomber.FIRE_TEXT

    async def test_deletes_timer_by_default(self):
        bot = FakeBot()
        await countdown(bot, -100, 2)
        assert len(bot.deleted) == 1

    async def test_keeps_timer_when_asked(self):
        bot = FakeBot()
        await countdown(bot, -100, 2, delete_after=False)
        assert bot.deleted == []

    async def test_capped_at_max_delay(self):
        bot = FakeBot()
        await countdown(bot, -100, 10_000)
        assert len(bot.edits) == bomber.MAX_DELAY

    async def test_survives_edit_errors(self):
        bot = FakeBot(edit_error=bad_request("message is not modified"))
        await countdown(bot, -100, 2)
        assert bot.edits == []
        assert len(bot.deleted) == 1  # таймер всё равно убрали

    async def test_survives_delete_error(self):
        bot = FakeBot(delete_error=bad_request("can't delete"))
        await countdown(bot, -100, 1)  # не должно бросить

    async def test_falls_back_to_plain_wait(self):
        bot = FakeBot(send_message_error=bad_request())
        assert await countdown(bot, -100, 2) is None
        assert bot.edits == []

    async def test_forbidden_propagates(self):
        bot = FakeBot(send_message_error=forbidden())
        with pytest.raises(TelegramForbiddenError):
            await countdown(bot, -100, 2)


class TestStrike:
    async def test_full_sequence(self):
        bot = FakeBot()
        result = await strike(
            bot, -100, ["a", "b"], phrase="огонь по готовности", delay=2, reply_to=9
        )
        assert bot.sent_messages[0] == "огонь по готовности"
        assert len(bot.edits) == 2
        assert result.sent == 2

    async def test_stickers_not_replied_when_phrase_sent(self):
        seen: list[object] = []

        class RecordingBot(FakeBot):
            async def send_sticker(self, chat_id, sticker, reply_parameters=None, **kw):
                seen.append(reply_parameters)
                return FakeMessage(1, chat_id)

        await strike(RecordingBot(), -100, ["a"], phrase="бум", delay=0, reply_to=9)
        assert seen == [None]

    async def test_stickers_replied_when_no_phrase(self):
        seen: list[object] = []

        class RecordingBot(FakeBot):
            async def send_sticker(self, chat_id, sticker, reply_parameters=None, **kw):
                seen.append(reply_parameters)
                return FakeMessage(1, chat_id)

        await strike(RecordingBot(), -100, ["a"], phrase=None, delay=0, reply_to=9)
        assert seen[0] is not None

    async def test_no_delay_no_timer(self):
        bot = FakeBot()
        await strike(bot, -100, ["a"], phrase="бум", delay=0)
        assert bot.edits == []

    async def test_forbidden_short_circuits(self):
        bot = FakeBot(send_message_error=forbidden())
        result = await strike(bot, -100, ["a", "b"], phrase="бум", delay=1)
        assert result.forbidden is True
        assert result.sent == 0
        assert bot.sent_stickers == []


class TestCooldownGate:
    def test_allows_first_hit(self):
        gate = CooldownGate()
        assert gate.check(-100, 10) is True

    def test_blocks_within_window(self):
        gate = CooldownGate()
        gate.hit(-100)
        assert gate.check(-100, 10) is False

    def test_zero_cooldown_never_blocks(self):
        gate = CooldownGate()
        gate.hit(-100)
        assert gate.check(-100, 0) is True

    def test_chats_are_independent(self):
        gate = CooldownGate()
        gate.hit(-100)
        assert gate.check(-200, 10) is True

    async def test_unblocks_after_window(self):
        gate = CooldownGate()
        gate.hit(-100)
        await asyncio.sleep(0.05)
        assert gate.check(-100, 0) is True

    def test_remaining_counts_down(self):
        gate = CooldownGate()
        gate.hit(-100)
        left = gate.remaining(-100, 10)
        assert 0 < left <= 10

    def test_remaining_zero_without_cooldown(self):
        gate = CooldownGate()
        gate.hit(-100)
        assert gate.remaining(-100, 0) == 0.0

    def test_reset_single_chat(self):
        gate = CooldownGate()
        gate.hit(-100)
        gate.reset(-100)
        assert gate.check(-100, 10) is True

    def test_reset_all(self):
        gate = CooldownGate()
        gate.hit(-100)
        gate.hit(-200)
        gate.reset()
        assert gate.check(-100, 10) is True
        assert gate.check(-200, 10) is True


class TestBombResult:
    def test_ok_requires_sent(self):
        assert BombResult(sent=0).ok is False
        assert BombResult(sent=1).ok is True

    def test_errors_default_is_isolated(self):
        a, b = BombResult(), BombResult()
        a.errors.append("x")
        assert b.errors == []
