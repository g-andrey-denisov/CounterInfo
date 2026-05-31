"""Посуточный отчёт за период: /period, выбор счётчика и периода (пресет / календарь / ввод)."""

from datetime import datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

import db
import search
from callbacks import CalendarDayCb, PeriodPresetCb
from constants import PERIOD_MAX_DAYS
from formatting import (
    diff_str,
    fmt_consumption,
    fmt_iso_date,
    fmt_serial,
    parse_period_input,
    send_long,
)
from keyboards import build_calendar_kb, calendar_period_end_rows, period_preset_kb
from states import PeriodForm

router = Router()


# ── /period + слово "Период" ──────────────────────────────────────────────────


@router.message(Command("period"))
@router.message(F.text.func(lambda t: bool(t) and t.strip().lower() == "период"))
async def cmd_period(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(PeriodForm.waiting_query)
    await message.answer(
        "<b>Показания за период</b>\n\n"
        "Введите серийный номер, код N.М или отправьте фото со штрих-кодом.",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(PeriodForm.waiting_query, F.text)
async def period_handle_text(message: Message, state: FSMContext) -> None:
    serial = message.text.strip()
    if not serial:
        return
    row = await search.find_by_text(serial)
    await _period_store_counter(message, state, row, serial)


@router.message(PeriodForm.waiting_query, F.photo)
async def period_handle_photo(message: Message, state: FSMContext, bot: Bot) -> None:
    barcode = await search.read_barcode_from_message(message, bot)
    if barcode is None:
        return
    row = await search.find_by_barcode(barcode)
    await _period_store_counter(message, state, row, barcode)


async def _period_store_counter(
    message: Message, state: FSMContext, row: dict | None, query: str
) -> None:
    if row is None:
        await message.answer(
            f"Счётчик <code>{query}</code> не найден. Попробуйте ещё раз.",
            parse_mode="HTML",
        )
        return
    await state.update_data(
        period_counter_id=row["Obj_Id_Counter"],
        period_name=row["Name"],
        period_serial=row["SerialNumber"],
    )
    await state.set_state(PeriodForm.waiting_period)
    await message.answer(
        f"<b>{row['Name']}</b>  <code>{fmt_serial(row['SerialNumber'])}</code>\n\n"
        f"Выберите период или введите в формате <b>дд.мм.гггг - дд.мм.гггг</b>\n"
        f"(максимум {PERIOD_MAX_DAYS} дней):",
        parse_mode="HTML",
        reply_markup=period_preset_kb(),
    )


@router.callback_query(PeriodPresetCb.filter(), StateFilter(PeriodForm.waiting_period))
async def cb_period_preset(
    query: CallbackQuery, callback_data: PeriodPresetCb, state: FSMContext
) -> None:
    await query.message.edit_reply_markup(reply_markup=None)
    await query.answer()
    data = await state.get_data()
    await state.clear()

    counter_id = data["period_counter_id"]
    name = data["period_name"]
    serial = data["period_serial"]

    start_iso, end_iso = callback_data.start, callback_data.end
    raw = await db.get_consumption_for_period(counter_id, start_iso, end_iso)
    rows = _build_period_rows(start_iso, end_iso, raw["pre"], raw["in_period"], raw["post"])
    text = _format_period_table(name, serial, start_iso, end_iso, rows)
    await query.message.answer(text, parse_mode="HTML")


@router.callback_query(CalendarDayCb.filter(F.scope == "period_start"), StateFilter(PeriodForm.waiting_period))
async def cb_calendar_day_period_start(
    query: CallbackQuery, callback_data: CalendarDayCb, state: FSMContext
) -> None:
    start_iso = f"{callback_data.year:04d}-{callback_data.month:02d}-{callback_data.day:02d}"
    await state.update_data(period_cal_start=start_iso)
    extra = calendar_period_end_rows(start_iso)
    kb = build_calendar_kb(
        callback_data.year, callback_data.month, "period_end",
        extra_rows=extra, ctx=start_iso,
    )
    await query.message.edit_text(
        f"Начало: <b>{fmt_iso_date(start_iso)}</b>\n\nВыберите <b>конец</b> периода:",
        parse_mode="HTML",
        reply_markup=kb,
    )
    await query.answer()


@router.callback_query(CalendarDayCb.filter(F.scope == "period_end"), StateFilter(PeriodForm.waiting_period))
async def cb_calendar_day_period_end(
    query: CallbackQuery, callback_data: CalendarDayCb, state: FSMContext
) -> None:
    end_iso = f"{callback_data.year:04d}-{callback_data.month:02d}-{callback_data.day:02d}"
    data = await state.get_data()
    start_iso = data.get("period_cal_start")
    if not start_iso:
        await query.answer("Сначала выберите начало периода.", show_alert=True)
        return

    start_dt = datetime.strptime(start_iso, "%Y-%m-%d")
    end_dt = datetime.strptime(end_iso, "%Y-%m-%d")

    if end_dt < start_dt:
        await query.answer("Конец периода не может быть раньше начала.", show_alert=True)
        return

    delta = (end_dt - start_dt).days + 1
    if delta > PERIOD_MAX_DAYS:
        await query.answer(
            f"Период не может превышать {PERIOD_MAX_DAYS} дней (запрошено {delta}).",
            show_alert=True,
        )
        return

    await query.message.edit_reply_markup(reply_markup=None)
    await query.answer()

    counter_id = data["period_counter_id"]
    name = data["period_name"]
    serial = data["period_serial"]
    await state.clear()

    raw = await db.get_consumption_for_period(counter_id, start_iso, end_iso)
    rows = _build_period_rows(start_iso, end_iso, raw["pre"], raw["in_period"], raw["post"])
    text = _format_period_table(name, serial, start_iso, end_iso, rows)
    await send_long(query.message, text)


@router.message(PeriodForm.waiting_period, F.text)
async def period_handle_period(message: Message, state: FSMContext) -> None:
    parsed = parse_period_input(message.text.strip())
    if parsed is None:
        await message.answer(
            "Неверный формат. Введите период как <b>дд.мм.гггг - дд.мм.гггг</b>:",
            parse_mode="HTML",
        )
        return

    start_iso, end_iso, corrections = parsed
    start_dt = datetime.strptime(start_iso, "%Y-%m-%d")
    end_dt = datetime.strptime(end_iso, "%Y-%m-%d")

    if end_dt < start_dt:
        await message.answer(
            "Начало периода не может быть позже его конца. Попробуйте снова:"
        )
        return

    delta = (end_dt - start_dt).days + 1
    if delta > PERIOD_MAX_DAYS:
        await message.answer(
            f"Период не может превышать {PERIOD_MAX_DAYS} дней "
            f"(запрошено {delta}). Попробуйте снова:"
        )
        return

    data = await state.get_data()
    await state.clear()

    counter_id = data["period_counter_id"]
    name = data["period_name"]
    serial = data["period_serial"]

    raw = await db.get_consumption_for_period(counter_id, start_iso, end_iso)
    rows = _build_period_rows(start_iso, end_iso, raw["pre"], raw["in_period"], raw["post"])
    text = _format_period_table(name, serial, start_iso, end_iso, rows, corrections)
    await send_long(message, text)


def _build_period_rows(
    start_date: str,
    end_date: str,
    pre: dict | None,
    in_period: list[dict],
    post: dict | None,
) -> list[dict]:
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    rows = []
    current = start_dt
    while current <= end_dt:
        day_end = current + timedelta(days=1)

        before = None
        for rec in reversed(in_period):
            ut = rec.get("UpdateTime")
            if ut and ut < current:
                before = rec
                break
        if before is None:
            before = pre

        on_date = None
        for rec in reversed(in_period):
            ut = rec.get("UpdateTime")
            if ut and current <= ut < day_end:
                on_date = rec
                break

        after = None
        if not on_date:
            for rec in in_period:
                ut = rec.get("UpdateTime")
                if ut and ut >= day_end:
                    after = rec
                    break
            if after is None:
                after = post

        rows.append({"date": current, "before": before, "on_date": on_date, "after": after})
        current += timedelta(days=1)
    return rows


def _format_period_table(
    name: str, serial: str, start_date: str, end_date: str, rows: list[dict],
    corrections: list[str] | None = None,
) -> str:
    col_date = [r["date"].strftime("%d.%m.%Y") for r in rows]
    col_before: list[str] = []
    col_third: list[str] = []

    for r in rows:
        col_before.append(fmt_consumption(r["before"]))
        if r["on_date"]:
            col_third.append(fmt_consumption(r["on_date"]))
        elif r["after"]:
            col_third.append(fmt_consumption(r["after"]))
        else:
            col_third.append("нет")

    col_diff = [diff_str(b, t) for b, t in zip(col_before, col_third)]

    H1, H2, H3, H4 = "Дата", "До", "На дату", "Итого"
    w1 = max(len(H1), max((len(v) for v in col_date), default=0))
    w2 = max(len(H2), max((len(v) for v in col_before), default=0))
    w3 = max(len(H3), max((len(v) for v in col_third), default=0))
    w4 = max(len(H4), max((len(v) for v in col_diff), default=0))

    lines = [
        f"{H1:<{w1}}  {H2:<{w2}}  {H3:<{w3}}  {H4}",
        f"{'-'*w1}  {'-'*w2}  {'-'*w3}  {'-'*w4}",
    ]
    for d, b, t, di in zip(col_date, col_before, col_third, col_diff):
        lines.append(f"{d:<{w1}}  {b:<{w2}}  {t:<{w3}}  {di}")

    # Итог: первые и последние показания за период
    non_empty = [v for v in col_third if v != "нет"]
    first_val = non_empty[0] if non_empty else "нет"
    last_val = non_empty[-1] if len(non_empty) > 1 else first_val

    total_val = diff_str(first_val, last_val)

    HS1, HS2, HS3 = "Первые", "Последние", "Всего"
    ws1 = max(len(HS1), len(first_val))
    ws2 = max(len(HS2), len(last_val))
    ws3 = max(len(HS3), len(total_val))
    lines += [
        "",
        f"{HS1:<{ws1}}  {HS2:<{ws2}}  {HS3}",
        f"{'-'*ws1}  {'-'*ws2}  {'-'*ws3}",
        f"{first_val:<{ws1}}  {last_val:<{ws2}}  {total_val}",
    ]

    correction_note = ""
    if corrections:
        correction_note = (
            "⚠️ Дата введена неправильно, приблизил к ближайшей: "
            + "; ".join(corrections) + "\n\n"
        )
    header = (
        f"<b>Показания за период</b>\n\n"
        f"{correction_note}"
        f"<b>Название:</b> {name}\n"
        f"<b>Серийный:</b> <code>{fmt_serial(serial)}</code>\n"
        f"<b>Период:</b> {fmt_iso_date(start_date)} — {fmt_iso_date(end_date)}\n\n"
    )
    return header + "<pre>" + "\n".join(lines) + "</pre>"
