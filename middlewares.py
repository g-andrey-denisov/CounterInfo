"""Middleware бота."""

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)

import local_db
from callbacks import RequestAccessCb
from config import settings

# Префикс callback-кнопки «Запросить доступ» — единственное действие,
# которое разрешено неизвестному пользователю.
_REQUEST_PREFIX = "req"


def _access_offer_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Запросить доступ",
                    callback_data=RequestAccessCb().pack(),
                )
            ]
        ]
    )


class AccessMiddleware(BaseMiddleware):
    """Пропускает админов и пользователей из БД; неизвестным предлагает
    запросить доступ."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        is_admin = user.id in settings.ADMIN_IDS
        data["is_admin"] = is_admin

        if is_admin or await local_db.is_user_allowed(user.id):
            return await handler(event, data)

        # Неизвестный пользователь: пропускаем только нажатие «Запросить доступ».
        if (
            isinstance(event, CallbackQuery)
            and event.data
            and event.data.startswith(_REQUEST_PREFIX)
        ):
            return await handler(event, data)

        await self._offer_access(event)
        return None

    async def _offer_access(self, event: TelegramObject) -> None:
        text = "У вас нет доступа к боту. Нажмите кнопку, чтобы запросить доступ."
        if isinstance(event, Message):
            await event.answer(text, reply_markup=_access_offer_kb())
        elif isinstance(event, CallbackQuery):
            await event.answer("Доступ запрещён.", show_alert=True)
            if event.message:
                await event.message.answer(text, reply_markup=_access_offer_kb())
