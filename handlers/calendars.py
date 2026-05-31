"""Общие callback'и календаря и сетки годов: перелистывание, открытие, заглушка.

Выбор конкретного дня/года/пресета привязан к FSM-состоянию конкретной фичи и
обрабатывается в её модуле (reading / checkups / period / monthly).
"""

from datetime import datetime

from aiogram import Router
from aiogram.types import CallbackQuery

from callbacks import (
    CalendarIgnoreCb,
    CalendarNavCb,
    CalendarOpenCb,
    YearNavCb,
)
from constants import PERIOD_MAX_DAYS
from keyboards import (
    build_calendar_kb,
    build_year_kb,
    calendar_exit_row,
    calendar_period_end_rows,
    calendar_quick_rows,
    year_extra_rows_end,
)

router = Router()


@router.callback_query(CalendarIgnoreCb.filter())
async def cb_calendar_ignore(query: CallbackQuery) -> None:
    await query.answer()


@router.callback_query(CalendarNavCb.filter())
async def cb_calendar_nav(query: CallbackQuery, callback_data: CalendarNavCb) -> None:
    scope, ctx = callback_data.scope, callback_data.ctx
    if scope == "reading":
        extra = calendar_quick_rows()
    elif scope == "checkups":
        extra = calendar_exit_row()
    elif scope == "period_end":
        extra = calendar_period_end_rows(ctx) if ctx else None
    else:
        extra = None
    kb = build_calendar_kb(callback_data.year, callback_data.month, scope, extra_rows=extra, ctx=ctx)
    await query.message.edit_reply_markup(reply_markup=kb)
    await query.answer()


@router.callback_query(CalendarOpenCb.filter())
async def cb_calendar_open(query: CallbackQuery, callback_data: CalendarOpenCb) -> None:
    today = datetime.now().date()
    scope = callback_data.scope
    if scope == "reading":
        extra = calendar_quick_rows()
        kb = build_calendar_kb(today.year, today.month, scope, extra_rows=extra)
        await query.message.edit_reply_markup(reply_markup=kb)
    elif scope == "checkups":
        extra = calendar_exit_row()
        kb = build_calendar_kb(today.year, today.month, scope, extra_rows=extra)
        await query.message.edit_reply_markup(reply_markup=kb)
    elif scope == "period_start":
        kb = build_calendar_kb(today.year, today.month, scope)
        await query.message.edit_text(
            f"Выберите <b>начало</b> периода\n(максимум {PERIOD_MAX_DAYS} дней):",
            parse_mode="HTML",
            reply_markup=kb,
        )
    await query.answer()


@router.callback_query(YearNavCb.filter())
async def cb_year_nav(query: CallbackQuery, callback_data: YearNavCb) -> None:
    scope, ctx = callback_data.scope, callback_data.ctx
    extra = year_extra_rows_end(int(ctx)) if scope == "year_end" and ctx else None
    kb = build_year_kb(callback_data.base, scope, extra_rows=extra, ctx=ctx, max_year=datetime.now().year)
    await query.message.edit_reply_markup(reply_markup=kb)
    await query.answer()
