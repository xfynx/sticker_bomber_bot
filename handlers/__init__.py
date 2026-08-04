"""Роутеры бота. Порядок регистрации важен: listener всегда последний."""
from aiogram import Router

from handlers import chatcfg, common, listener, owner


def build_router() -> Router:
    root = Router(name="root")
    root.include_router(common.router)
    root.include_router(owner.router)
    root.include_router(chatcfg.router)
    # listener ловит любые тексты — регистрируем в самом конце,
    # чтобы не перехватывать команды.
    root.include_router(listener.router)
    return root


__all__ = ["build_router"]
