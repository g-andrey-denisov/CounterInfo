"""Помесячный отчёт: /monthly, выбор счётчика и года/диапазона лет (грид / ввод)."""

from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

import db
import search
from callbacks import YearPickCb
from constants import MONTH_NAMES, MONTHLY_MAX_YEARS
from formatting import diff_str, fmt_consumption, fmt_serial, parse_year_input, send_long
from keyboards import build_year_kb, year_extra_rows_end
from states import MonthlyForm

router = Router()


# ── /monthly + слово "Месяцы" ─────────────────────────────────────────────────


@router.message(Command("monthly"))
@router.message(F.text.func(lambda t: bool(t) and t.strip().lower() == "месяцы"))
async def cmd_monthly(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(MonthlyForm.waiting_query)
    await message.answer(
        "<b>Помесячный отчёт</b>\n\n"
        "Введите серийный номер, код N.М или отправьте фото со штрих-кодом.",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(MonthlyForm.waiting_query, F.text)
async def monthly_handle_text(message: Message, state: FSMContext) -> None:
    serial = message.text.strip()
    if not serial:
        return
    row = await search.find_by_text(serial)
    await _monthly_store_counter(message, state, row, serial)


@router.message(MonthlyForm.waiting_query, F.photo)
async def monthly_handle_photo(message: Message, state: FSMContext, bot: Bot) -> None:
    barcode = await search.read_barcode_from_message(message, bot)
    if barcode is None:
        return
    row = await search.find_by_barcode(barcode)
    await _monthly_store_counter(message, state, row, barcode)


async def _monthly_store_counter(
    message: Message, state: FSMContext, row: dict | None, query: str
) -> None:
    if row is None:
        await message.answer(
            f"Счётчик <code>{query}</code> не найден. Попробуйте ещё раз.",
            parse_mode="HTML",
        )
        return
    await state.update_data(
        monthly_counter_id=row["Obj_Id_Counter"],
        monthly_name=row["Name"],
        monthly_serial=row["SerialNumber"],
    )
    await state.set_state(MonthlyForm.waiting_year)
    cur_year = datetime.now().year
    base = cur_year - 8
    kb = build_year_kb(base, "year_start", max_year=cur_year)
    await message.answer(
        f"<b>{row['Name']}</b>  <code>{fmt_serial(row['SerialNumber'])}</code>\n\n"
        f"Выберите год или начало диапазона (до {MONTHLY_MAX_YEARS} лет).\n"
        f"Можно ввести вручную: <b>гггг</b> или <b>гггг-гггг</b>:",
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.message(MonthlyForm.waiting_year, F.text)
async def monthly_handle_year(message: Message, state: FSMContext) -> None:
    parsed = parse_year_input(message.text.strip())
    if parsed is None:
        await message.answer(
            "Неверный формат. Введите год как <b>гггг</b> или <b>гггг-гггг</b>:",
            parse_mode="HTML",
        )
        return

    start_year, end_year = parsed
    if end_year < start_year:
        await message.answer("Начало диапазона не может быть позже конца. Попробуйте снова:")
        return

    if end_year - start_year + 1 > MONTHLY_MAX_YEARS:
        await message.answer(
            f"Диапазон не может превышать {MONTHLY_MAX_YEARS} лет "
            f"(запрошено {end_year - start_year + 1}). Попробуйте снова:"
        )
        return

    data = await state.get_data()
    await state.clear()

    counter_id = data["monthly_counter_id"]
    name = data["monthly_name"]
    serial = data["monthly_serial"]

    start_iso = f"{start_year}-01-01"
    end_iso = f"{end_year}-12-31"
    raw = await db.get_consumption_for_period(counter_id, start_iso, end_iso)
    rows = _build_monthly_rows(start_year, 1, end_year, 12, raw["pre"], raw["in_period"], raw["post"])
    text = _format_monthly_table(name, serial, start_year, end_year, rows)
    await send_long(message, text)


@router.callback_query(YearPickCb.filter(F.scope == "year_start"), StateFilter(MonthlyForm.waiting_year))
async def cb_year_pick_start(
    query: CallbackQuery, callback_data: YearPickCb, state: FSMContext
) -> None:
    start_year = callback_data.year
    if start_year == datetime.now().year:
        await query.message.edit_reply_markup(reply_markup=None)
        await query.answer()
        data = await state.get_data()
        await state.clear()
        start_iso = f"{start_year}-01-01"
        end_iso = f"{start_year}-12-31"
        raw = await db.get_consumption_for_period(data["monthly_counter_id"], start_iso, end_iso)
        rows = _build_monthly_rows(start_year, 1, start_year, 12, raw["pre"], raw["in_period"], raw["post"])
        text = _format_monthly_table(data["monthly_name"], data["monthly_serial"], start_year, start_year, rows)
        await send_long(query.message, text)
        return

    await state.update_data(monthly_cal_start=start_year)
    extra = year_extra_rows_end(start_year)
    kb = build_year_kb(start_year, "year_end", extra_rows=extra, ctx=str(start_year), max_year=datetime.now().year)
    await query.message.edit_text(
        f"Начало: <b>{start_year}</b>\n\n"
        f"Выберите <b>конец</b> диапазона (до {MONTHLY_MAX_YEARS} лет):",
        parse_mode="HTML",
        reply_markup=kb,
    )
    await query.answer()


@router.callback_query(YearPickCb.filter(F.scope == "year_end"), StateFilter(MonthlyForm.waiting_year))
async def cb_year_pick_end(
    query: CallbackQuery, callback_data: YearPickCb, state: FSMContext
) -> None:
    end_year = callback_data.year
    data = await state.get_data()
    start_year = data.get("monthly_cal_start")
    if start_year is None:
        await query.answer("Сначала выберите начало диапазона.", show_alert=True)
        return

    if end_year < start_year:
        await query.answer("Год конца не может быть раньше начала.", show_alert=True)
        return

    if end_year - start_year + 1 > MONTHLY_MAX_YEARS:
        await query.answer(
            f"Диапазон не может превышать {MONTHLY_MAX_YEARS} лет "
            f"(запрошено {end_year - start_year + 1}).",
            show_alert=True,
        )
        return

    await query.message.edit_reply_markup(reply_markup=None)
    await query.answer()

    counter_id = data["monthly_counter_id"]
    name = data["monthly_name"]
    serial = data["monthly_serial"]
    await state.clear()

    start_iso = f"{start_year}-01-01"
    end_iso = f"{end_year}-12-31"
    raw = await db.get_consumption_for_period(counter_id, start_iso, end_iso)
    rows = _build_monthly_rows(start_year, 1, end_year, 12, raw["pre"], raw["in_period"], raw["post"])
    text = _format_monthly_table(name, serial, start_year, end_year, rows)
    await send_long(query.message, text)


def _build_monthly_rows(
    start_year: int, start_month: int,
    end_year: int, end_month: int,
    pre: dict | None,
    in_period: list[dict],
    post: dict | None,
) -> list[dict]:
    rows = []
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        month_start = datetime(year, month, 1)
        month_end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)

        before = None
        for rec in reversed(in_period):
            ut = rec.get("UpdateTime")
            if ut and ut < month_start:
                before = rec
                break
        if before is None:
            before = pre

        in_month = [
            r for r in in_period
            if r.get("UpdateTime") and month_start <= r["UpdateTime"] < month_end
        ]
        displayed = in_month[-1] if in_month else None

        rows.append({"year": year, "month": month, "before": before, "displayed": displayed})
        month += 1
        if month > 12:
            month = 1
            year += 1
    return rows


def _format_monthly_table(
    name: str, serial: str, start_year: int, end_year: int, rows: list[dict]
) -> str:
    def _before_val(rec: dict | None) -> str:
        if not rec:
            return "0"
        c = rec.get("Consumption")
        return str(round(c)) if c is not None else "0"

    cur_ym = (datetime.now().year, datetime.now().month)
    rows = [r for r in rows if (r["year"], r["month"]) <= cur_ym]

    col_year = [str(r["year"]) for r in rows]
    col_month = [MONTH_NAMES[r["month"]] for r in rows]
    col_before = [_before_val(r["before"]) for r in rows]
    col_disp = [fmt_consumption(r["displayed"]) for r in rows]
    col_diff = [diff_str(b, d) for b, d in zip(col_before, col_disp)]

    H1, H2, H3, H4, H5 = "Год", "Месяц", "До", "За месяц", "Итого"
    w1 = max(len(H1), max((len(v) for v in col_year), default=0))
    w2 = max(len(H2), max((len(v) for v in col_month), default=0))
    w3 = max(len(H3), max((len(v) for v in col_before), default=0))
    w4 = max(len(H4), max((len(v) for v in col_disp), default=0))
    w5 = max(len(H5), max((len(v) for v in col_diff), default=0))

    lines = [
        f"{H1:<{w1}}  {H2:<{w2}}  {H3:<{w3}}  {H4:<{w4}}  {H5}",
        f"{'-'*w1}  {'-'*w2}  {'-'*w3}  {'-'*w4}  {'-'*w5}",
    ]
    for yr, mn, b, d, di in zip(col_year, col_month, col_before, col_disp, col_diff):
        lines.append(f"{yr:<{w1}}  {mn:<{w2}}  {b:<{w3}}  {d:<{w4}}  {di}")

    period_str = str(start_year) if start_year == end_year else f"{start_year}–{end_year}"
    header = (
        f"<b>Помесячный отчёт</b>\n\n"
        f"<b>Название:</b> {name}\n"
        f"<b>Серийный:</b> <code>{fmt_serial(serial)}</code>\n"
        f"<b>Период:</b> {period_str}\n\n"
    )
    return header + "<pre>" + "\n".join(lines) + "</pre>"
