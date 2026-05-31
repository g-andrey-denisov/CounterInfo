"""Блокнот: /notebook (просмотр) и /clear (очистка)."""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import local_db
from callbacks import ClearConfirmCb
from formatting import fmt_serial, plural, send_long

router = Router()


# ── /notebook + слово "Блокнот" ───────────────────────────────────────────────


@router.message(Command("notebook", "note"))
@router.message(F.text.func(lambda t: bool(t) and t.strip().lower() == "блокнот"))
async def cmd_notebook(message: Message, state: FSMContext) -> None:
    await state.clear()
    entries = await local_db.get_notebook(message.from_user.id)
    if not entries:
        await message.answer("Блокнот пуст.")
        return
    n = len(entries)
    lines = [f"<b>Блокнот ({n} {plural(n, 'запись', 'записи', 'записей')})</b>\n"]
    for i, e in enumerate(entries, 1):
        comment = e["comment"] or "не указано"
        lines.append(
            f"<b>#{i}</b>  {e['ts']}\n"
            f"Серийный:   <code>{fmt_serial(e['serial_number'])}</code>\n"
            f"Название:   {e['name'] or '—'}\n"
            f"Состояние:  {e['state'] or '—'}\n"
            f"Показ.(БД): {e['db_consumption'] or '—'}\n"
            f"Комментарий: {comment}\n"
            "──────────────────────"
        )
    await send_long(message, "\n".join(lines))


# ── /clear + слово "Очисти" ───────────────────────────────────────────────────


@router.message(Command("clear"))
@router.message(F.text.func(lambda t: bool(t) and t.strip().lower() == "очисти"))
async def cmd_clear(message: Message, state: FSMContext) -> None:
    await state.clear()
    if not await local_db.notebook_count(message.from_user.id):
        await message.answer("Блокнот пуст — нечего очищать.")
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, очистить", callback_data=ClearConfirmCb(yes=1).pack()
                ),
                InlineKeyboardButton(
                    text="Отмена", callback_data=ClearConfirmCb(yes=0).pack()
                ),
            ]
        ]
    )
    await message.answer("Очистить блокнот? Все записи будут удалены.", reply_markup=kb)


@router.callback_query(ClearConfirmCb.filter())
async def cb_clear_confirm(query: CallbackQuery, callback_data: ClearConfirmCb) -> None:
    if callback_data.yes:
        await local_db.clear_notebook(query.from_user.id)
        await query.message.edit_text("Блокнот очищен.", reply_markup=None)
    else:
        await query.message.edit_text("Отменено.", reply_markup=None)
    await query.answer()
