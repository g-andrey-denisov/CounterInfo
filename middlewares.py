"""Middleware бота."""

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from config import settings


class AccessMiddleware(BaseMiddleware):
    """Ограничивает доступ списком ALLOWED_USER_IDS (если он задан)."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if (
            user
            and settings.ALLOWED_USER_IDS
            and user.id not in settings.ALLOWED_USER_IDS
        ):
            if isinstance(event, Message):
                await event.answer("Доступ запрещён.")
            elif isinstance(event, CallbackQuery):
                await event.answer("Доступ запрещён.", show_alert=True)
            return
        return await handler(event, data)
