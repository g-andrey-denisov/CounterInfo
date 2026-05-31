"""Журнал проверок: /checkups, выбор даты (список / календарь), постраничный вывод."""

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import local_db
from callbacks import (
    CalendarDayCb,
    CheckupsDateCb,
    CheckupsPageCb,
    ExitCheckupsCb,
)
from constants import CHECKUPS_PAGE_SIZE
from formatting import fmt_iso_date, fmt_serial, parse_input_date
from keyboards import build_checkups_date_kb, checkups_nav_kb
from states import CheckupsForm

router = Router()


# ── /checkups + слово "Проверки" ──────────────────────────────────────────────


@router.message(Command("checkups"))
@router.message(F.text.func(lambda t: bool(t) and t.strip().lower() == "проверки"))
async def cmd_checkups(message: Message, state: FSMContext) -> None:
    await state.clear()
    dates = await local_db.get_checkup_dates(limit=5)
    if not dates:
        await message.answer("Журнал проверок пуст.")
        return
    await state.set_state(CheckupsForm.waiting_date)
    await message.answer(
        "Выберите дату или введите в формате <b>дд.мм.гггг</b>:",
        parse_mode="HTML",
        reply_markup=build_checkups_date_kb(dates),
    )


@router.message(CheckupsForm.waiting_date, F.text)
async def checkups_handle_date(message: Message, state: FSMContext) -> None:
    iso_date = parse_input_date(message.text.strip())
    if iso_date is None:
        await message.answer(
            "Неверный формат. Введите дату как <b>дд.мм.гггг</b>:", parse_mode="HTML"
        )
        return
    _, total = await local_db.get_checkups_by_date(iso_date, offset=0, limit=1)
    if total == 0:
        dates = await local_db.get_checkup_dates(limit=5)
        await message.answer(
            f"В указанную дату ({fmt_iso_date(iso_date)}) проверок не проводилось.\n\n"
            "Выберите дату или введите в формате <b>дд.мм.гггг</b>:",
            parse_mode="HTML",
            reply_markup=build_checkups_date_kb(dates),
        )
        return
    await state.clear()
    await _show_checkups_page(message, iso_date, page=0)


@router.callback_query(ExitCheckupsCb.filter())
async def cb_exit_checkups(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await query.message.delete()
    await query.answer("Выход из журнала проверок.")


@router.callback_query(CheckupsDateCb.filter())
async def cb_checkups_date(
    query: CallbackQuery, callback_data: CheckupsDateCb, state: FSMContext
) -> None:
    await state.clear()
    await query.message.edit_reply_markup(reply_markup=None)
    await query.answer()
    await _show_checkups_page(query.message, callback_data.date, page=0)


@router.callback_query(CalendarDayCb.filter(F.scope == "checkups"), StateFilter(CheckupsForm.waiting_date))
async def cb_calendar_day_checkups(
    query: CallbackQuery, callback_data: CalendarDayCb, state: FSMContext
) -> None:
    iso_date = f"{callback_data.year:04d}-{callback_data.month:02d}-{callback_data.day:02d}"
    await state.clear()
    await query.message.edit_reply_markup(reply_markup=None)
    await query.answer()
    await _show_checkups_page(query.message, iso_date, page=0)


@router.callback_query(CheckupsPageCb.filter())
async def cb_checkups_page(query: CallbackQuery, callback_data: CheckupsPageCb) -> None:
    items, total = await local_db.get_checkups_by_date(
        callback_data.date,
        offset=callback_data.page * CHECKUPS_PAGE_SIZE,
        limit=CHECKUPS_PAGE_SIZE,
    )
    total_pages = max(1, (total + CHECKUPS_PAGE_SIZE - 1) // CHECKUPS_PAGE_SIZE)
    text = _format_checkups_page(items, callback_data.date, callback_data.page, total, total_pages)
    kb = checkups_nav_kb(callback_data.date, callback_data.page, total_pages)
    await query.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await query.answer()


async def _show_checkups_page(message: Message, iso_date: str, page: int) -> None:
    items, total = await local_db.get_checkups_by_date(
        iso_date,
        offset=page * CHECKUPS_PAGE_SIZE,
        limit=CHECKUPS_PAGE_SIZE,
    )
    if total == 0:
        await message.answer(f"Проверок за {fmt_iso_date(iso_date)} не найдено.")
        return
    total_pages = max(1, (total + CHECKUPS_PAGE_SIZE - 1) // CHECKUPS_PAGE_SIZE)
    text = _format_checkups_page(items, iso_date, page, total, total_pages)
    kb = checkups_nav_kb(iso_date, page, total_pages)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


def _format_checkups_page(
    items: list[dict], iso_date: str, page: int, total: int, total_pages: int
) -> str:
    lines = [
        f"<b>Проверки за {fmt_iso_date(iso_date)}</b> "
        f"(стр. {page + 1}/{total_pages}, всего: {total})\n"
    ]
    for e in items:
        ca = e.get("checked_at", "")
        time_str = ca[11:16] if len(ca) >= 16 else "—"
        reading = e["reading"] or "—"
        comment = e["comment"] or "—"
        lines.append(
            f"{time_str}  {e['user_name'] or '—'}\n"
            f"<code>{fmt_serial(e['serial_number'])}</code>  {e['name'] or '—'}\n"
            f"Статус: <b>{e['status']}</b>  Показания: {reading}\n"
            f"Комментарий: {comment}\n"
            "──────────────────────"
        )
    return "\n".join(lines)
