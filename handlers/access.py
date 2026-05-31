"""Управление доступом: заявки от неизвестных пользователей, уведомления
администраторам с кнопками «Принять»/«Отклонить» и команда /users для
просмотра, удаления и ручного добавления пользователей."""

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)

import local_db
from callbacks import AccessDecisionCb, RequestAccessCb, UserAddCb, UserDeleteCb
from config import settings
from states import AccessForm

logger = logging.getLogger(__name__)

router = Router()


# ── Вспомогательное ────────────────────────────────────────────────────────────


def _user_label(user: User) -> str:
    """Человекочитаемое имя пользователя Telegram."""
    name = user.full_name or "—"
    return f"{name} (@{user.username})" if user.username else name


def _row_label(row: dict) -> str:
    """Имя пользователя из строки app_user / access_request."""
    name = row.get("full_name") or "—"
    username = row.get("username")
    return f"{name} (@{username})" if username else name


# ── Заявка на доступ (от неизвестного пользователя) ────────────────────────────


@router.callback_query(RequestAccessCb.filter())
async def cb_request_access(query: CallbackQuery, bot: Bot) -> None:
    user = query.from_user

    # Возможно, пользователя уже добавили, пока висела кнопка.
    if user.id in settings.ADMIN_IDS or await local_db.is_user_allowed(user.id):
        await query.answer("У вас уже есть доступ.", show_alert=True)
        if query.message:
            await query.message.edit_reply_markup(reply_markup=None)
        return

    is_new = await local_db.add_access_request(
        user.id, user.username, user.full_name
    )
    if query.message:
        await query.message.edit_reply_markup(reply_markup=None)

    if not is_new:
        await query.answer("Заявка уже отправлена, ожидайте решения.", show_alert=True)
        return

    await query.answer("Заявка отправлена администраторам.", show_alert=True)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Принять",
                    callback_data=AccessDecisionCb(user_id=user.id, approve=1).pack(),
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=AccessDecisionCb(user_id=user.id, approve=0).pack(),
                ),
            ]
        ]
    )
    text = (
        f"<b>Запрос доступа</b>\n\n"
        f"Пользователь: {_user_label(user)}\n"
        f"ID: <code>{user.id}</code>"
    )
    for admin_id in settings.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            logger.warning("Не удалось уведомить администратора %s", admin_id)


@router.callback_query(AccessDecisionCb.filter())
async def cb_access_decision(
    query: CallbackQuery, callback_data: AccessDecisionCb, bot: Bot, is_admin: bool
) -> None:
    if not is_admin:
        await query.answer("Только для администраторов.", show_alert=True)
        return

    target_id = callback_data.user_id
    req = await local_db.get_access_request(target_id)

    if req is None:
        await query.answer("Заявка уже обработана.", show_alert=True)
        if query.message:
            await query.message.edit_reply_markup(reply_markup=None)
        return

    label = _row_label(req)

    if callback_data.approve:
        await local_db.add_user(
            target_id, req["username"], req["full_name"], query.from_user.id
        )
        await local_db.delete_access_request(target_id)
        verdict = f"✅ Доступ предоставлен: {label}"
        try:
            await bot.send_message(
                target_id, "Вам предоставлен доступ к боту. Отправьте /help."
            )
        except Exception:
            logger.warning("Не удалось уведомить пользователя %s", target_id)
    else:
        await local_db.delete_access_request(target_id)
        verdict = f"❌ Заявка отклонена: {label}"
        try:
            await bot.send_message(target_id, "Ваша заявка на доступ отклонена.")
        except Exception:
            logger.warning("Не удалось уведомить пользователя %s", target_id)

    await query.answer()
    if query.message:
        await query.message.edit_text(verdict)


# ── /users — управление списком (только админ) ─────────────────────────────────


def _users_keyboard(users: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"🗑 {_row_label(u)} · {u['user_id']}",
                callback_data=UserDeleteCb(user_id=u["user_id"]).pack(),
            )
        ]
        for u in users
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="➕ Добавить вручную", callback_data=UserAddCb().pack()
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("users"))
async def cmd_users(message: Message, state: FSMContext, is_admin: bool) -> None:
    if not is_admin:
        await message.answer("Команда доступна только администраторам.")
        return
    await state.clear()
    await _send_users_list(message)


async def _send_users_list(message: Message) -> None:
    users = await local_db.get_users()
    header = (
        f"<b>Пользователи с доступом ({len(users)})</b>"
        if users
        else "<b>Список пуст.</b> Добавьте пользователя вручную или дождитесь заявки."
    )
    await message.answer(
        header, parse_mode="HTML", reply_markup=_users_keyboard(users)
    )


@router.callback_query(UserDeleteCb.filter())
async def cb_user_delete(
    query: CallbackQuery, callback_data: UserDeleteCb, is_admin: bool
) -> None:
    if not is_admin:
        await query.answer("Только для администраторов.", show_alert=True)
        return
    removed = await local_db.remove_user(callback_data.user_id)
    await query.answer("Удалён." if removed else "Пользователь не найден.")
    users = await local_db.get_users()
    header = (
        f"<b>Пользователи с доступом ({len(users)})</b>"
        if users
        else "<b>Список пуст.</b>"
    )
    if query.message:
        await query.message.edit_text(
            header, parse_mode="HTML", reply_markup=_users_keyboard(users)
        )


@router.callback_query(UserAddCb.filter())
async def cb_user_add(query: CallbackQuery, state: FSMContext, is_admin: bool) -> None:
    if not is_admin:
        await query.answer("Только для администраторов.", show_alert=True)
        return
    await query.answer()
    await state.set_state(AccessForm.waiting_user_id)
    await query.message.answer(
        "Отправьте <b>Telegram ID</b> пользователя (можно с именем после пробела, "
        "например <code>123456 Иван</code>):",
        parse_mode="HTML",
    )


@router.message(AccessForm.waiting_user_id, F.text)
async def handle_add_user(
    message: Message, state: FSMContext, bot: Bot, is_admin: bool
) -> None:
    if not is_admin:
        await state.clear()
        return
    parts = message.text.strip().split(maxsplit=1)
    raw_id = parts[0].lstrip("-")
    if not raw_id.isdigit():
        await message.answer("Нужен числовой Telegram ID. Попробуйте ещё раз:")
        return
    user_id = int(parts[0])
    full_name = parts[1] if len(parts) > 1 else None

    await state.clear()
    added = await local_db.add_user(user_id, None, full_name, message.from_user.id)
    await local_db.delete_access_request(user_id)

    if not added:
        await message.answer("Пользователь уже в списке.")
    else:
        await message.answer(f"Добавлен пользователь <code>{user_id}</code>.", parse_mode="HTML")
        try:
            await bot.send_message(
                user_id, "Вам предоставлен доступ к боту. Отправьте /help."
            )
        except Exception:
            logger.warning("Не удалось уведомить пользователя %s", user_id)

    await _send_users_list(message)
